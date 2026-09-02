#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Upload coupled app_update.bin over USB CDC2 (UART SMP / MI_04)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ZEPHYR_USB_VID = 0x2FE3


def find_cdc2_port() -> str:
    try:
        from serial.tools import list_ports
    except ImportError as e:
        raise SystemExit(
            "pyserial is required to auto-detect the CDC2 port.\n"
            "Install: pip install pyserial\n"
            "Or pass --port COMx / /dev/ttyACMx"
        ) from e

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
        # Stable Windows order is not guaranteed; prefer sorted by location suffix.
        def key(item: tuple[str, str]) -> str:
            return item[1]

        ranked = sorted(others, key=key)
        # Last of three is usually MI_04 when locations are x.0 / x.2 / x.4
        return ranked[-1][0]

    raise SystemExit(
        "Could not find Zephyr USB CDC SMP port (VID 2FE3, MI_04).\n"
        "Is the device enumerated with triple CDC?\n"
        "Pass --port explicitly. Seen:\n  "
        + ("\n  ".join(f"{d} ({i})" for d, i in others) or "(none)")
    )


def find_smpmgr() -> list[str] | None:
    exe = shutil.which("smpmgr")
    if exe:
        return [exe]
    # Prefer `python -m smpmgr` on the same interpreter.
    try:
        import smpmgr  # noqa: F401

        return [sys.executable, "-m", "smpmgr"]
    except ImportError:
        pass
    return None


def run_smpmgr(smpmgr: list[str], port: str, image: Path, confirm: bool, timeout: float) -> int:
    cmd = [*smpmgr, "--port", port, "--timeout", str(timeout), "upgrade"]
    if confirm:
        cmd.append("--confirm")
    cmd.append(str(image))
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


async def run_smpclient(port: str, image: Path, confirm: bool) -> None:
    from smpclient import SMPClient
    from smpclient.generics import error
    from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite
    from smpclient.requests.os_management import ResetWrite
    from smpclient.transport.serial import SMPSerialTransport

    data = image.read_bytes()
    transport = SMPSerialTransport()
    client = SMPClient(transport, port)
    async with client:
        print(f"Connected on {port}, uploading {image} ({len(data)} bytes)...", flush=True)
        async for offset in client.upload(data):
            pct = 100.0 * offset / len(data)
            print(f"\r  {offset}/{len(data)} ({pct:5.1f}%)", end="", flush=True)
        print(flush=True)

        states = await client.request(ImageStatesRead())
        if error(states):
            raise SystemExit(f"image state-read failed: {states}")
        # Prefer slot 1 hash for test mark when present.
        hash_to_test = None
        for img in getattr(states, "images", []) or []:
            slot = getattr(img, "slot", None)
            h = getattr(img, "hash", None)
            if slot == 1 and h:
                hash_to_test = bytes(h)
                break
        if hash_to_test is None and getattr(states, "images", None):
            h = getattr(states.images[-1], "hash", None)
            if h:
                hash_to_test = bytes(h)
        if hash_to_test is None:
            raise SystemExit(f"upload finished but no image hash to mark: {states}")

        write = ImageStatesWrite(hash=hash_to_test, confirm=confirm)
        resp = await client.request(write)
        if error(resp):
            raise SystemExit(f"image state-write failed: {resp}")
        print("Marked image for", "confirm" if confirm else "test", flush=True)

        reset = await client.request(ResetWrite())
        if error(reset):
            raise SystemExit(f"reset failed: {reset}")
        print("Reset sent.", flush=True)


def main() -> int:
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
        default=30.0,
        help="SMP request timeout seconds for smpmgr (default 30)",
    )
    args = ap.parse_args()

    sample_root = Path(__file__).resolve().parents[1]
    image = args.image or (sample_root / "build" / "dfu" / "app_update.bin")
    if not image.is_file():
        raise SystemExit(
            f"Missing image: {image}\n"
            "Run: just dfu   (or just build / just usb-dfu which packages first)"
        )

    port = args.port or find_cdc2_port()
    print(f"USB DFU port: {port}", flush=True)
    print(f"Image: {image} ({image.stat().st_size} bytes)", flush=True)

    smpmgr = find_smpmgr()
    if smpmgr:
        return run_smpmgr(smpmgr, port, image, args.confirm, args.timeout)

    try:
        import smpclient  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "Neither smpmgr nor smpclient is installed.\n"
            "  pip install smpmgr          # CLI (Python >= 3.10 recommended)\n"
            "  pip install smpclient       # library fallback used by this script\n"
            "See README.md prerequisites."
        ) from e

    import asyncio

    asyncio.run(run_smpclient(port, image, args.confirm))
    return 0


if __name__ == "__main__":
    sys.exit(main())
