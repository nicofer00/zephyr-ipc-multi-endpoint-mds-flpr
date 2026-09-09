#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Upload coupled app_update.bin over BLE SMP (no host MCUboot TLV inspection)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_BLE_NAME = "Nordic_Memfault"
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
SAMPLE_ROOT = Path(__file__).resolve().parents[1]



def configure_logging(verbose: bool, logfile: Path | None) -> None:
    """Keep bleak/smpclient quiet unless --verbose (avoids ble.py spam)."""
    level = logging.DEBUG if verbose else logging.WARNING
    handlers: list[logging.Handler] = []
    if verbose:
        handlers.append(logging.StreamHandler(sys.stderr))
    if logfile is not None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers or [logging.NullHandler()],
        force=True,
    )
    for name in ("bleak", "smpclient", "smp"):
        logging.getLogger(name).setLevel(level)


def find_smpmgr() -> list[str] | None:
    exe = shutil.which("smpmgr")
    if exe:
        return [exe]
    try:
        import smpmgr  # noqa: F401

        return [sys.executable, "-m", "smpmgr"]
    except ImportError:
        return None


def smpmgr_supports_format_any(smpmgr: list[str]) -> bool:
    try:
        out = subprocess.check_output(
            [*smpmgr, "upgrade", "--help"], text=True, stderr=subprocess.STDOUT
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
    return "--format" in out


def console():
    from rich.console import Console

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    return Console(force_terminal=True, soft_wrap=True, legacy_windows=False)


async def scan_ble_name(name: str, timeout_s: float = 10.0) -> str | None:
    from bleak import BleakScanner

    devices = await BleakScanner.discover(timeout=timeout_s, return_adv=True)
    best: tuple[int, str] | None = None
    named = 0
    for device, adv in devices.values():
        if device.name != name:
            continue
        named += 1
        rssi = adv.rssi if adv.rssi is not None else -127
        if best is None or rssi > best[0]:
            best = (rssi, device.address)
    return best[1] if best else None


def resolve_ble_target(ble: str | None, scan_timeout: float, ui) -> str:
    """Prefer advertised name; identity from bt id-show is not connectable with privacy."""
    if ble and not MAC_RE.match(ble):
        return ble

    name = DEFAULT_BLE_NAME
    if ble and MAC_RE.match(ble):
        try:
            with ui.status(f"[cyan]Scanning for {name}...", spinner="line"):
                addr = asyncio.run(scan_ble_name(name, timeout_s=min(scan_timeout, 8.0)))
        except ImportError:
            addr = None
        if addr:
            ui.print(f"[dim]Found {name} @ {addr} (ignoring stale MAC {ble})[/]")
            return name
        ui.print(f"[yellow]Using MAC {ble}[/] [dim](prefer --ble {DEFAULT_BLE_NAME})[/]")
        return ble

    if ble:
        ui.print(f"[yellow]Using MAC {ble}[/] [dim](prefer --ble {DEFAULT_BLE_NAME})[/]")
        return ble

    try:
        with ui.status(f"[cyan]Scanning for {name}...", spinner="line"):
            addr = asyncio.run(scan_ble_name(name, timeout_s=scan_timeout))
    except ImportError as e:
        raise SystemExit(
            f"Pass --ble {DEFAULT_BLE_NAME} or install BLE scan support:\n"
            "  python -m pip install 'smpclient[ble]'"
        ) from e

    if not addr:
        raise SystemExit(
            f"Could not find advertising BLE device named {name!r}.\n"
            "Is the DK running and connectable? Try shell: bt advertise on\n"
            "If unpaired: pair Nordic_Memfault in Windows Bluetooth settings first."
        )

    ui.print(f"[green]Found[/] {name} @ [bold]{addr}[/]")
    return name


def run_smpmgr(
    smpmgr: list[str],
    ble: str,
    image: Path | None,
    confirm: bool,
    timeout: float,
    logfile: Path | None,
    probe: bool,
    verbose: bool,
) -> int:
    # WARNING keeps bleak DEBUG off; smpmgr still shows its own progress bar.
    loglevel = "DEBUG" if verbose else "WARNING"
    cmd = [
        *smpmgr,
        "--ble",
        ble,
        "--timeout",
        str(timeout),
        "--loglevel",
        loglevel,
    ]
    if logfile:
        cmd.extend(["--logfile", str(logfile)])
    if probe:
        cmd.extend(["image", "state-read"])
    else:
        assert image is not None
        cmd.extend(["upgrade", "--format", "any"])
        if confirm:
            cmd.append("--confirm")
        cmd.append(str(image))
    return subprocess.call(cmd)


def _progress():
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeElapsedColumn,
        TransferSpeedColumn,
    )

    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console(),
        expand=True,
    )


def _ble_transport():
    """Prefer fresh GATT DB on Windows; stale AUTHEN cache causes error 5 on CCC."""
    from smpclient.transport.ble import SMPBLETransport

    if sys.platform == "win32":
        return SMPBLETransport(winrt={"use_cached_services": False})
    return SMPBLETransport()


async def ensure_ble_paired(ble: str, scan_timeout: float, ui) -> None:
    """Windows: pair/bond so SMP CCC notify is allowed (Just Works)."""
    from bleak import BleakClient, BleakScanner

    with ui.status(f"[cyan]Ensuring BLE bond with {ble}...", spinner="line"):
        if MAC_RE.match(ble):
            device = await BleakScanner.find_device_by_address(ble, timeout=scan_timeout)
        else:
            device = await BleakScanner.find_device_by_name(ble, timeout=scan_timeout)
        if device is None:
            raise SystemExit(
                f"Could not find {ble!r} to pair.\n"
                "Enable advertising (bt advertise on) and retry."
            )
        kwargs = {"timeout": 30.0}
        if sys.platform == "win32":
            kwargs["winrt"] = {"use_cached_services": False}
        async with BleakClient(device, **kwargs) as client:
            try:
                await client.pair()
                ui.print(f"[green]Paired[/] {ble} @ {device.address}")
            except Exception as e:
                # Already bonded in Windows often raises; continue and let SMP try.
                ui.print(
                    f"[yellow]Pair note:[/] {e}\n"
                    "[dim]If DFU fails with Insufficient Authentication: remove "
                    "Nordic_Memfault in Windows Bluetooth settings, then retry "
                    "(firmware needs CONFIG_BT_SMP_ENFORCE_MITM=n).[/]"
                )


async def run_smpclient_probe(
    ble: str, timeout: float, ui, scan_timeout: float, skip_pair: bool, connect_timeout: float
) -> None:
    from smpclient import SMPClient
    from smpclient.generics import error, success
    from smpclient.requests.image_management import ImageStatesRead

    if not skip_pair:
        await ensure_ble_paired(ble, scan_timeout, ui)
    with ui.status(f"[cyan]Connecting to {ble} (timeout {connect_timeout:.0f}s)...", spinner="line"):
        client = SMPClient(_ble_transport(), ble, timeout_s=timeout)
        async with client:
            ui.print(f"[green]Connected[/] to {ble}")
            r = await asyncio.wait_for(client.request(ImageStatesRead()), timeout=timeout)
            if error(r):
                raise SystemExit(f"image state-read failed: {r}")
            if success(r):
                ui.print("[bold]Image states[/]")
                for img in r.images:
                    ui.print(f"  {img}")


async def run_smpclient_ble(
    ble: str,
    image: Path,
    confirm: bool,
    timeout: float,
    ui,
    scan_timeout: float,
    skip_pair: bool,
    connect_timeout: float,
) -> None:
    from smpclient import SMPClient

    from dfu_common import ensure_ab_slot_ready, mark_uploaded_image

    data = image.read_bytes()
    total = len(data)

    if skip_pair:
        ui.print("[dim]Skipping pre-pair (--skip-pair)[/]")
    else:
        await ensure_ble_paired(ble, scan_timeout, ui)

    with ui.status(f"[cyan]Connecting to {ble} (timeout {connect_timeout:.0f}s)...", spinner="line"):
        client = SMPClient(_ble_transport(), ble, timeout_s=timeout)
        try:
            await client.connect(connect_timeout_s=connect_timeout)
        except Exception as e:
            if "Insufficient Authentication" in str(e) or "protocol_error" in str(e).lower():
                raise SystemExit(
                    "BLE SMP notify failed: Insufficient Authentication.\n"
                    "1) Flash firmware with CONFIG_BT_SMP_ENFORCE_MITM=n\n"
                    "2) Windows Bluetooth: remove Nordic_Memfault, pair again\n"
                    "3) Retry just ble-dfu\n"
                    f"Detail: {e}"
                ) from e
            raise

    try:
        await ensure_ab_slot_ready(client, timeout, ui)

        ui.print(f"[green]Connected[/] — uploading [bold]{image.name}[/] ({total:,} bytes)")
        with _progress() as progress:
            task = progress.add_task("Upload", total=total)
            async for offset in client.upload(data):
                progress.update(task, completed=offset)

        await mark_uploaded_image(client, confirm, timeout, ui)
    finally:
        await client.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ble",
        default=os.environ.get("BLE_DFU_ADDR"),
        help=(
            f"BLE device name or address (default: scan for {DEFAULT_BLE_NAME}). "
            "Do not use bt id-show identity when CONFIG_BT_PRIVACY=y."
        ),
    )
    ap.add_argument(
        "--image",
        type=Path,
        default=None,
        help="FW image (default: build/dfu/app_update.bin next to this sample)",
    )
    ap.add_argument(
        "--confirm",
        action="store_true",
        help="Permanently confirm image (skips MCUboot test/revert). Prefer default test swap.",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="SMP request timeout seconds (default 300)",
    )
    ap.add_argument(
        "--scan-timeout",
        type=float,
        default=12.0,
        help="Seconds to scan for device name when --ble is omitted (default 12)",
    )
    ap.add_argument(
        "--logfile",
        type=Path,
        default=None,
        help="Optional debug log file (implies quieter console; use with --verbose)",
    )
    ap.add_argument(
        "--probe",
        action="store_true",
        help="SMP image state-read only (connection test, no upload)",
    )
    ap.add_argument(
        "--smpmgr",
        action="store_true",
        help="Use smpmgr CLI instead of smpclient (smpmgr shows its own progress UI)",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show bleak/smpclient DEBUG logs on stderr",
    )
    ap.add_argument(
        "--skip-pair",
        action="store_true",
        help="Skip Bleak pre-pair session (use when already paired in Windows; avoids double-connect)",
    )
    ap.add_argument(
        "--connect-timeout",
        type=float,
        default=45.0,
        help="BLE GATT connect timeout seconds (default 45; separate from --timeout)",
    )
    args = ap.parse_args()

    try:
        from rich.console import Console  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "rich is required for the BLE DFU UI.\n"
            "  python -m pip install rich\n"
            "(Also installed with: pip install smpmgr)"
        ) from e

    ui = console()
    configure_logging(args.verbose, args.logfile)

    sample_root = Path(__file__).resolve().parents[1]
    image = args.image or (sample_root / "build" / "dfu" / "app_update.bin")
    if not args.probe and not image.is_file():
        raise SystemExit(
            f"Missing image: {image}\n"
            "Run: just dfu   (or just build / just ble-dfu which packages first)"
        )

    ble = resolve_ble_target(args.ble, args.scan_timeout, ui)
    if not args.probe:
        ui.print(f"[dim]Image[/] {image} ([bold]{image.stat().st_size:,}[/] bytes)")

    # Default: smpclient + rich progress (quiet). Optional --smpmgr for CLI.
    if not args.smpmgr:
        try:
            from smpclient.transport.ble import SMPBLETransport  # noqa: F401
        except ImportError:
            pass
        else:
            if args.probe:
                asyncio.run(
                    run_smpclient_probe(
                        ble,
                        args.timeout,
                        ui,
                        args.scan_timeout,
                        args.skip_pair,
                        args.connect_timeout,
                    )
                )
            else:
                asyncio.run(
                    run_smpclient_ble(
                        ble,
                        image,
                        args.confirm,
                        args.timeout,
                        ui,
                        args.scan_timeout,
                        args.skip_pair,
                        args.connect_timeout,
                    )
                )
            return 0

    smpmgr = find_smpmgr()
    if smpmgr and (args.probe or smpmgr_supports_format_any(smpmgr)):
        return run_smpmgr(
            smpmgr,
            ble,
            None if args.probe else image,
            args.confirm,
            args.timeout,
            args.logfile,
            args.probe,
            args.verbose,
        )

    try:
        from smpclient.transport.ble import SMPBLETransport  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "Need smpclient[ble] or smpmgr:\n"
            "  python -m pip install 'smpclient[ble]' rich\n"
            "  python -m pip install 'smpmgr>=0.19'"
        ) from e

    if args.probe:
        asyncio.run(
            run_smpclient_probe(
                ble,
                args.timeout,
                ui,
                args.scan_timeout,
                args.skip_pair,
                args.connect_timeout,
            )
        )
    else:
        asyncio.run(
            run_smpclient_ble(
                ble,
                image,
                args.confirm,
                args.timeout,
                ui,
                args.scan_timeout,
                args.skip_pair,
                args.connect_timeout,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        raise SystemExit(130) from None
