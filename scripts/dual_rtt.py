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

    def open(self) -> None:
        import pylink

        jlink = pylink.JLink()
        jlink.open(serial_no=int(self.sn) if str(self.sn).isdigit() else self.sn)
        jlink.set_tif(pylink.enums.JLinkInterfaces.SWD)
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

    def render(self):
        from rich.console import Group
        from rich.panel import Panel
        from rich.columns import Columns
        from rich.text import Text

        assert self.m33 is not None and self.flpr is not None

        def pane(reader: RttUpBuffer, lines: Deque[str], title: str, border: str) -> Panel:
            body_lines = list(lines) if lines else [f"[dim]{reader.status}[/dim]"]
            # Use plain text join; Rich markup only on status placeholder
            if lines:
                body = Text("\n".join(body_lines))
            else:
                body = Text.from_markup(f"[dim]{reader.status}[/dim]")
            subtitle = f"CB 0x{reader.cb_addr:08X}  ch{reader.channel}  {reader.status}"
            return Panel(
                body,
                title=f"[bold]{title}[/] {reader.label}",
                subtitle=subtitle,
                border_style=border,
                expand=True,
            )

        header = Text()
        header.append("dual_rtt", style="bold")
        header.append(f"  SN {self.sn}  SWD {self.speed_khz} kHz  device {DEFAULT_DEVICE}")
        if self.error:
            header.append(f"\nerror: {self.error}", style="bold red")
        header.append("\nCtrl+C to quit", style="dim")

        cols = Columns(
            [
                pane(self.m33, self.m33_lines, "M33", "cyan"),
                pane(self.flpr, self.flpr_lines, "RV32", "magenta"),
            ],
            equal=True,
            expand=True,
        )
        return Group(header, cols)

    def run(self) -> int:
        from rich.live import Live

        ui = console()
        self.open()
        poller = threading.Thread(target=self.poll_loop, name="rtt-poll", daemon=True)
        poller.start()
        try:
            with Live(self.render(), console=ui, refresh_per_second=10, screen=False) as live:
                while not self.stop.is_set():
                    live.update(self.render())
                    time.sleep(0.1)
        except KeyboardInterrupt:
            ui.print("\n[dim]Interrupted[/]")
        finally:
            self.close()
            poller.join(timeout=2.0)
        return 0


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
        default=200,
        help="Scrollback lines per pane (default: 200)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    args = build_arg_parser().parse_args(argv)

    try:
        import pylink  # noqa: F401
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
