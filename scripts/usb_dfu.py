#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Upload coupled app_update.bin over USB CDC2 (UART SMP / MI_04)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path


ZEPHYR_USB_VID = 0x2FE3


def _force_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError under nrfutil/Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)] if verbose else [logging.NullHandler()],
        force=True,
    )
    for name in ("smpclient", "smp", "serial"):
        logging.getLogger(name).setLevel(level)


def console():
    from rich.console import Console

    # legacy_windows=False + utf-8 stdio avoids braille spinner encode crashes.
    return Console(force_terminal=True, soft_wrap=True, legacy_windows=False)


def find_cdc2_port(wait_s: float = 0.0, poll_s: float = 1.0) -> str:
    try:
        from serial.tools import list_ports
    except ImportError as e:
        raise SystemExit(
            "pyserial is required to auto-detect the CDC2 port.\n"
            "Install: pip install pyserial\n"
            "Or pass --port COMx / /dev/ttyACMx"
        ) from e

    import time

    deadline = time.time() + max(0.0, wait_s)
    attempt = 0
    while True:
        attempt += 1
        matches: list[str] = []
        others: list[tuple[str, str]] = []
        for p in list_ports.comports():
            if p.vid != ZEPHYR_USB_VID:
                continue
            hwid = p.hwid or ""
            loc = p.location or ""
            others.append((p.device, f"loc={loc} hwid={hwid}"))
            if "MI_04" in hwid or loc.endswith("x.4") or loc.endswith(":4") or loc.endswith(".4"):
                matches.append(p.device)


        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SystemExit(
                "Multiple CDC2 candidates: "
                + ", ".join(matches)
                + "\nPass --port explicitly."
            )
        if len(others) == 3:

            def key(item: tuple[str, str]) -> str:
                return item[1]

            ranked = sorted(others, key=key)
            return ranked[-1][0]

        if time.time() >= deadline:
            raise SystemExit(
                "Could not find Zephyr USB CDC SMP port (VID 2FE3, MI_04).\n"
                "After reset, Windows may take several seconds to re-enumerate CDC.\n"
                "Try: unplug/replug USB, or wait then `just usb-dfu` / pass --port.\n"
                "Seen:\n  "
                + ("\n  ".join(f"{d} ({i})" for d, i in others) or "(none)")
            )
        time.sleep(poll_s)


def find_smpmgr() -> list[str] | None:
    exe = shutil.which("smpmgr")
    if exe:
        return [exe]
    try:
        import smpmgr  # noqa: F401

        return [sys.executable, "-m", "smpmgr"]
    except ImportError:
        pass
    return None


def run_smpmgr(smpmgr: list[str], port: str, image: Path, confirm: bool, timeout: float) -> int:
    cmd = [
        *smpmgr,
        "--port",
        port,
        "--timeout",
        str(timeout),
        "--loglevel",
        "WARNING",
        "upgrade",
        "--format",
        "any",
    ]
    if confirm:
        cmd.append("--confirm")
    cmd.append(str(image))
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def _progress(ui):
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeElapsedColumn,
        TransferSpeedColumn,
    )

    # No SpinnerColumn — braille glyphs break on Windows cp1252 via nrfutil.
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=ui,
        expand=True,
    )


def _serial_transport():
    """Cap encoded SMP frames for UART/CDC line buffers.

    smpclient Auto sizes chunks to MCUMGR NETBUF (often >2 KiB). With default
    UART_MCUMGR_RX_BUF_COUNT=2 the first upload packet can stall forever.
    BufferSize matches a practical UART MTU (see prj.conf).
    """
    from smpclient.transport.serial import BufferSize, SMPSerialTransport

    return SMPSerialTransport(fragmentation_strategy=BufferSize(buf_size=1024))


async def run_smpclient(port: str, image: Path, confirm: bool, timeout: float, ui) -> None:
    from smpclient import SMPClient

    from dfu_common import ensure_ab_slot_ready, mark_uploaded_image

    data = image.read_bytes()
    total = len(data)
    # Short request timeout during connect/_initialize (MCUMgr params); restore after.
    init_timeout = min(15.0, timeout)
    client = SMPClient(_serial_transport(), port, timeout_s=init_timeout)

    with ui.status(f"[cyan]Connecting on {port}...", spinner="line"):
        await client.connect(connect_timeout_s=init_timeout)
    client._timeout_s = timeout

    try:
        await ensure_ab_slot_ready(client, timeout, ui)

        ui.print(
            f"[green]Connected[/] — uploading [bold]{image.name}[/] ({total:,} bytes)\n"
            "[dim]First chunk can take a while (secondary slot erase).[/]"
        )
        with _progress(ui) as progress:
            task = progress.add_task("Upload", total=total)
            async for offset in client.upload(data, first_timeout_s=max(60.0, timeout)):
                progress.update(task, completed=offset)

        await mark_uploaded_image(client, confirm, timeout, ui)
    finally:
        await client.disconnect()


def main() -> int:
    _force_utf8_stdio()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--image",
        type=Path,
        default=None,
        help="FW image (default: build/dfu/app_update.bin next to this sample)",
    )
    ap.add_argument(
        "--port",
        default=os.environ.get("USB_DFU_PORT"),
        help="Serial port for CDC2 / uart-mcumgr (or set USB_DFU_PORT)",
    )
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="Permanently confirm image (skips MCUboot test/revert). Prefer default test swap.",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="SMP request timeout seconds (default 120)",
    )
    ap.add_argument(
        "--smpmgr",
        action="store_true",
        help="Use smpmgr CLI (may crash on Windows consoles that reject Unicode spinners)",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show library DEBUG logs on stderr",
    )
    ap.add_argument(
        "--wait-port",
        type=float,
        default=20.0,
        help="Seconds to wait for CDC2 to reappear after reset (default 20)",
    )
    args = ap.parse_args()
    configure_logging(args.verbose)

    sample_root = Path(__file__).resolve().parents[1]
    image = args.image or (sample_root / "build" / "dfu" / "app_update.bin")
    if not image.is_file():
        raise SystemExit(
            f"Missing image: {image}\n"
            "Run: just dfu   (or just build / just usb-dfu which packages first)"
        )

    port = args.port or find_cdc2_port(wait_s=args.wait_port)
    print(f"USB DFU port {port}", flush=True)

    try:
        from rich.console import Console  # noqa: F401

        ui = console()
    except ImportError:
        ui = None

    if args.smpmgr:
        smpmgr = find_smpmgr()
        if not smpmgr:
            raise SystemExit("smpmgr not found; omit --smpmgr to use smpclient")
        print(f"USB DFU port: {port}", flush=True)
        print(f"Image: {image} ({image.stat().st_size} bytes)", flush=True)
        return run_smpmgr(smpmgr, port, image, args.confirm, args.timeout)

    try:
        from smpclient.transport.serial import SMPSerialTransport  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "smpclient is required for USB DFU.\n"
            "  python -m pip install smpclient rich\n"
            "Or: python scripts/usb_dfu.py --smpmgr"
        ) from e

    if ui is None:
        raise SystemExit("rich is required: python -m pip install rich")

    ui.print(f"[dim]USB DFU port[/] [bold]{port}[/]")
    ui.print(f"[dim]Image[/] {image} ([bold]{image.stat().st_size:,}[/] bytes)")
    asyncio.run(run_smpclient(port, image, args.confirm, args.timeout, ui))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        raise SystemExit(130) from None
