#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Dual-pane SEGGER RTT viewer for nRF54LM20B cpuapp (M33) + FLPR (RV32).

One J-Link SWD session (NRF54LM20B_M33 @ 4 kHz) concurrently drains two fixed
RTT control blocks via software ring-buffer reads (pylink-square memory access).
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Deque

DEFAULT_SN = os.environ.get("JLINK_SN", "1051800018")
DEFAULT_SPEED_KHZ = 4000
DEFAULT_M33_CB = 0x20000470
DEFAULT_FLPR_CB = 0x20070A10
DEFAULT_DEVICE = "NRF54LM20B_M33"
RTT_ID = b"SEGGER RTT"

# SEGGER_RTT_CB / SEGGER_RTT_BUFFER_UP layout (32-bit little-endian)
_CB_HDR_SIZE = 24  # acID[16] + MaxNumUp + MaxNumDown
_BUF_DESC_SIZE = 24  # sName, pBuffer, Size, WrOff, RdOff, Flags
_OFF_PBUFFER = 4
_OFF_SIZE = 8
_OFF_WROFF = 12
_OFF_RDOFF = 16


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def console():
    from rich.console import Console

    return Console(force_terminal=True, soft_wrap=True, legacy_windows=False)


def parse_addr(text: str) -> int:
    return int(text, 0)


@dataclass
class RttUpBuffer:
    """Host-side reader for one SEGGER RTT up-buffer (target → host)."""

    jlink: object
    lock: threading.Lock
    cb_addr: int
    channel: int = 0
    label: str = ""
    ready: bool = False
    status: str = "waiting for RTT…"
    _partial: bytearray = field(default_factory=bytearray)

    def _desc_addr(self) -> int:
        return self.cb_addr + _CB_HDR_SIZE + self.channel * _BUF_DESC_SIZE

    def _read_u32(self, addr: int) -> int:
        words = self.jlink.memory_read32(addr, 1)
        return int(words[0]) & 0xFFFFFFFF

    def _write_u32(self, addr: int, value: int) -> None:
        self.jlink.memory_write32(addr, [value & 0xFFFFFFFF])

    def _read_bytes(self, addr: int, length: int) -> bytes:
        if length <= 0:
            return b""
        data = self.jlink.memory_read8(addr, length)
        return bytes(data)

    def ensure_ready(self) -> bool:
        with self.lock:
            try:
                ident = self._read_bytes(self.cb_addr, 16)
            except Exception as exc:  # noqa: BLE001 — probe/target may be mid-boot
                self.ready = False
                self.status = f"read error: {exc}"
                return False
            if not ident.startswith(RTT_ID):
                self.ready = False
                self.status = "waiting for RTT…"
                return False
            max_up = self._read_u32(self.cb_addr + 16)
            if self.channel < 0 or self.channel >= max_up:
                self.ready = False
                self.status = f"channel {self.channel} out of range (max {max_up})"
                return False
            desc = self._desc_addr()
            p_buffer = self._read_u32(desc + _OFF_PBUFFER)
            size = self._read_u32(desc + _OFF_SIZE)
            if p_buffer == 0 or size < 2:
                self.ready = False
                self.status = "up-buffer not configured"
                return False
            self.ready = True
            self.status = "connected"
            return True

    def read(self) -> bytes:
        """Drain available up-buffer bytes; empty if CB not ready."""
        with self.lock:
            if not self.ready:
                return b""
            try:
                desc = self._desc_addr()
                p_buffer = self._read_u32(desc + _OFF_PBUFFER)
                size = self._read_u32(desc + _OFF_SIZE)
                wr_off = self._read_u32(desc + _OFF_WROFF)
                rd_off = self._read_u32(desc + _OFF_RDOFF)
            except Exception as exc:  # noqa: BLE001
                self.ready = False
                self.status = f"read error: {exc}"
                return b""

            if p_buffer == 0 or size < 2 or wr_off >= size or rd_off >= size:
                self.ready = False
                self.status = "invalid buffer state"
                return b""

            if wr_off == rd_off:
                return b""

            if wr_off > rd_off:
                chunks = [self._read_bytes(p_buffer + rd_off, wr_off - rd_off)]
            else:
                chunks = [
                    self._read_bytes(p_buffer + rd_off, size - rd_off),
                    self._read_bytes(p_buffer, wr_off),
                ]
            try:
                self._write_u32(desc + _OFF_RDOFF, wr_off)
            except Exception as exc:  # noqa: BLE001
                self.ready = False
                self.status = f"RdOff write error: {exc}"
                return b""
            return b"".join(chunks)

    def feed_lines(self, data: bytes, lines: Deque[str], max_lines: int) -> None:
        if not data:
            return
        self._partial.extend(data)
        while True:
            try:
                nl = self._partial.index(0x0A)
            except ValueError:
                break
            raw = bytes(self._partial[: nl + 1])
            del self._partial[: nl + 1]
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            lines.append(text)
            while len(lines) > max_lines:
                lines.popleft()
        # Cap orphan partial without newline to avoid unbounded growth
        if len(self._partial) > 4096:
            text = bytes(self._partial).decode("utf-8", errors="replace")
            self._partial.clear()
            lines.append(text)
            while len(lines) > max_lines:
                lines.popleft()


class DualRttApp:
    def __init__(
        self,
        sn: str,
        speed_khz: int,
        m33_cb: int,
        flpr_cb: int,
        channel: int,
        poll_ms: float,
        max_lines: int,
    ) -> None:
        self.sn = sn
        self.speed_khz = speed_khz
        self.channel = channel
        self.poll_s = max(poll_ms, 1.0) / 1000.0
        self.max_lines = max_lines
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.jlink = None
        self.m33_lines: Deque[str] = collections.deque(maxlen=max_lines)
        self.flpr_lines: Deque[str] = collections.deque(maxlen=max_lines)
        self.m33: RttUpBuffer | None = None
        self.flpr: RttUpBuffer | None = None
        self.m33_cb = m33_cb
        self.flpr_cb = flpr_cb
        self.error: str | None = None
        # UI scroll: 0 = pinned to newest (auto-tail). Higher = look further back.
        self.focus = "m33"  # "m33" | "flpr"
        self.m33_scroll = 0
        self.flpr_scroll = 0
        self._body_rows = 20

    def open(self) -> None:
        from pylink import enums
        from pylink.jlink import JLink

        jlink = JLink()
        jlink.open(serial_no=int(self.sn) if str(self.sn).isdigit() else self.sn)
        jlink.set_tif(enums.JLinkInterfaces.SWD)
        jlink.connect(DEFAULT_DEVICE, speed=self.speed_khz)
        # Keep targets running — RTT is polled from live RAM.
        if jlink.halted():
            jlink.restart()
        self.jlink = jlink
        self.m33 = RttUpBuffer(
            jlink, self.lock, self.m33_cb, self.channel, label="NRF54LM20B_M33"
        )
        self.flpr = RttUpBuffer(
            jlink, self.lock, self.flpr_cb, self.channel, label="NRF54LM20B_RV32"
        )

    def close(self) -> None:
        self.stop.set()
        jlink = self.jlink
        self.jlink = None
        if jlink is not None:
            try:
                if jlink.opened():
                    jlink.close()
            except Exception:
                pass

    def poll_once(self) -> None:
        assert self.m33 is not None and self.flpr is not None
        for reader, lines in ((self.m33, self.m33_lines), (self.flpr, self.flpr_lines)):
            if not reader.ready:
                reader.ensure_ready()
            data = reader.read()
            reader.feed_lines(data, lines, self.max_lines)

    def poll_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
            self.stop.wait(self.poll_s)

    def _scroll_attr(self) -> str:
        return "m33_scroll" if self.focus == "m33" else "flpr_scroll"

    def _focused_lines(self) -> Deque[str]:
        return self.m33_lines if self.focus == "m33" else self.flpr_lines

    def _max_scroll(self, lines: Deque[str]) -> int:
        return max(0, len(lines) - self._body_rows)

    def _nudge_scroll(self, delta: int) -> None:
        lines = self._focused_lines()
        attr = self._scroll_attr()
        cur = getattr(self, attr)
        new = max(0, min(self._max_scroll(lines), cur + delta))
        setattr(self, attr, new)

    def _follow_bottom(self) -> None:
        setattr(self, self._scroll_attr(), 0)

    def handle_keys(self) -> None:
        """Non-blocking keyboard: Tab focus, arrows/PgUp/PgDn/Home/End scroll."""
        while True:
            key = _read_key()
            if key is None:
                return
            kind, payload = key
            if kind == "char":
                if payload in (b"\t", "\t"):
                    self.focus = "flpr" if self.focus == "m33" else "m33"
                elif payload in (b"q", b"Q", "q", "Q"):
                    self.stop.set()
                elif payload in (b"\x03",):  # Ctrl+C
                    self.stop.set()
                continue
            # special / named
            name = payload if kind == "named" else _win_special_name(payload)
            if name == "up":
                self._nudge_scroll(1)
            elif name == "down":
                self._nudge_scroll(-1)
            elif name == "pgup":
                self._nudge_scroll(self._body_rows)
            elif name == "pgdn":
                self._nudge_scroll(-self._body_rows)
            elif name == "home":
                lines = self._focused_lines()
                setattr(self, self._scroll_attr(), self._max_scroll(lines))
            elif name == "end":
                self._follow_bottom()

    def _viewport_lines(
        self,
        lines: Deque[str],
        max_rows: int,
        max_cols: int,
        status: str,
        scroll_offset: int,
    ):
        """Fixed-height body. scroll_offset=0 tails newest; higher looks further back."""
        from rich.text import Text

        if max_rows < 1:
            max_rows = 1
        if max_cols < 8:
            max_cols = 8

        if not lines:
            body = Text.from_markup(f"[dim]{status}[/dim]")
            for _ in range(max_rows - 1):
                body.append("\n")
            return body

        all_lines = list(lines)
        n = len(all_lines)
        max_off = max(0, n - max_rows)
        off = max(0, min(max_off, scroll_offset))
        end = n - off
        start = max(0, end - max_rows)
        chunk = all_lines[start:end]

        body = Text()
        for i, line in enumerate(chunk):
            if i:
                body.append("\n")
            if len(line) > max_cols:
                body.append(line[: max_cols - 1] + "…")
            else:
                body.append(line)
        for _ in range(max_rows - len(chunk)):
            body.append("\n")
        return body

    def render(self, ui):
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.text import Text

        assert self.m33 is not None and self.flpr is not None

        term_h = max(ui.size.height, 8)
        term_w = max(ui.size.width, 40)
        header_h = 2
        chrome = 4
        body_rows = max(1, term_h - header_h - chrome)
        self._body_rows = body_rows
        col_w = max(12, (term_w // 2) - 4)

        # Clamp offsets if buffer shrank / viewport grew.
        self.m33_scroll = min(self.m33_scroll, self._max_scroll(self.m33_lines))
        self.flpr_scroll = min(self.flpr_scroll, self._max_scroll(self.flpr_lines))

        def pane(
            reader: RttUpBuffer,
            lines: Deque[str],
            title: str,
            border: str,
            scroll: int,
            focused: bool,
        ) -> Panel:
            body = self._viewport_lines(lines, body_rows, col_w, reader.status, scroll)
            follow = "FOLLOW" if scroll == 0 else f"+{scroll} back"
            focus_mark = " ●" if focused else ""
            subtitle = (
                f"CB 0x{reader.cb_addr:08X}  ch{reader.channel}  "
                f"{reader.status}  [{follow}]{focus_mark}"
            )
            style = f"bold {border}" if focused else border
            return Panel(
                body,
                title=f"[bold]{title}[/] {reader.label}",
                subtitle=subtitle,
                border_style=style,
                height=body_rows + chrome,
                expand=True,
            )

        header = Text()
        header.append("dual_rtt", style="bold")
        header.append(f"  SN {self.sn}  SWD {self.speed_khz} kHz  device {DEFAULT_DEVICE}")
        if self.error:
            header.append(f"  error: {self.error}", style="bold red")
        header.append(
            "  Tab focus  ↑↓/PgUp/PgDn scroll  End follow  Ctrl+C quit",
            style="dim",
        )

        layout = Layout()
        layout.split_column(
            Layout(header, name="header", size=header_h),
            Layout(name="body"),
        )
        layout["body"].split_row(
            Layout(
                pane(
                    self.m33,
                    self.m33_lines,
                    "M33",
                    "cyan",
                    self.m33_scroll,
                    self.focus == "m33",
                ),
                name="m33",
            ),
            Layout(
                pane(
                    self.flpr,
                    self.flpr_lines,
                    "RV32",
                    "magenta",
                    self.flpr_scroll,
                    self.focus == "flpr",
                ),
                name="flpr",
            ),
        )
        return layout

    def run(self) -> int:
        from rich.live import Live

        ui = console()
        self.open()
        poller = threading.Thread(target=self.poll_loop, name="rtt-poll", daemon=True)
        poller.start()
        interrupted = False
        try:
            with Live(
                self.render(ui),
                console=ui,
                refresh_per_second=10,
                screen=True,
                vertical_overflow="crop",
            ) as live:
                while not self.stop.is_set():
                    self.handle_keys()
                    live.update(self.render(ui))
                    time.sleep(0.05)
        except KeyboardInterrupt:
            interrupted = True
        finally:
            self.close()
            poller.join(timeout=2.0)
            if interrupted:
                ui.print("[dim]Interrupted[/]")
        return 0


def _win_special_name(code: bytes) -> str | None:
    # Second byte after 0xE0 / 0x00 prefix from msvcrt.getch().
    mapping = {
        b"H": "up",
        b"P": "down",
        b"I": "pgup",
        b"Q": "pgdn",
        b"G": "home",
        b"O": "end",
    }
    return mapping.get(code)


def _read_key():
    """Non-blocking key read. Returns (kind, payload) or None.

    kind: 'char' | 'special' | 'named'
    """
    if sys.platform == "win32":
        import msvcrt

        if not msvcrt.kbhit():
            return None
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            return ("special", msvcrt.getch())
        return ("char", ch)

    # POSIX: best-effort non-blocking stdin
    import select

    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        # Drain a short CSI sequence if present
        rest = ""
        if select.select([sys.stdin], [], [], 0.01)[0]:
            rest += sys.stdin.read(1)
        if rest == "[" and select.select([sys.stdin], [], [], 0.01)[0]:
            rest += sys.stdin.read(1)
        seq = rest
        named = {
            "[A": "up",
            "[B": "down",
            "[5": "pgup",
            "[6": "pgdn",
            "[H": "home",
            "[F": "end",
        }.get(seq)
        if named:
            # Consume trailing '~' for PgUp/PgDn if present
            if named in ("pgup", "pgdn") and select.select([sys.stdin], [], [], 0.01)[0]:
                sys.stdin.read(1)
            return ("named", named)
        return None
    return ("char", ch.encode() if isinstance(ch, str) else ch)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Dual-pane J-Link RTT viewer for nRF54LM20B M33 + FLPR (pylink-square)."
    )
    p.add_argument(
        "--sn",
        default=DEFAULT_SN,
        help=f"J-Link serial number (default: env JLINK_SN or {DEFAULT_SN})",
    )
    p.add_argument(
        "--speed",
        type=int,
        default=DEFAULT_SPEED_KHZ,
        metavar="KHZ",
        help=f"SWD speed in kHz (default: {DEFAULT_SPEED_KHZ})",
    )
    p.add_argument(
        "--m33-cb",
        type=parse_addr,
        default=DEFAULT_M33_CB,
        metavar="ADDR",
        help=f"M33 RTT control block address (default: 0x{DEFAULT_M33_CB:08X})",
    )
    p.add_argument(
        "--flpr-cb",
        type=parse_addr,
        default=DEFAULT_FLPR_CB,
        metavar="ADDR",
        help=f"FLPR RTT control block address (default: 0x{DEFAULT_FLPR_CB:08X})",
    )
    p.add_argument(
        "--channel",
        type=int,
        default=0,
        help="RTT up-buffer channel index (default: 0)",
    )
    p.add_argument(
        "--poll-ms",
        type=float,
        default=20.0,
        help="Poll interval in milliseconds (default: 20)",
    )
    p.add_argument(
        "--lines",
        type=int,
        default=1000,
        help="Scrollback buffer lines per pane (default: 1000; viewport auto-tails)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_arg_parser().parse_args(argv)

    try:
        from pylink.jlink import JLink  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "pylink-square is required for dual RTT.\n"
            "  python -m pip install pylink-square rich"
        ) from e

    try:
        import rich  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "rich is required for dual RTT.\n"
            "  python -m pip install pylink-square rich"
        ) from e

    app = DualRttApp(
        sn=str(args.sn),
        speed_khz=args.speed,
        m33_cb=args.m33_cb,
        flpr_cb=args.flpr_cb,
        channel=args.channel,
        poll_ms=args.poll_ms,
        max_lines=args.lines,
    )
    return app.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        raise SystemExit(130)
