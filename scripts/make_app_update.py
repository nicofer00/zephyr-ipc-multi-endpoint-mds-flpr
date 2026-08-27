#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Build DFU artifacts for this sample.

With the nordic-flpr layout, FLPR RRAM (@0x1e5000) sits *outside* the MCUboot
slot0/slot1 regions. So:

- ``app_update.bin`` — MCUboot-signed **cpuapp** image for BLE OTA (slot0/slot1).
  Prefer the build system's ``zephyr.signed.bin`` when present (Ed25519 on NCS 3.4).
- ``app_and_flpr_merged.hex`` — mergehex of app + FLPR for inspection / dual-hex
  factory programming (not a single MCUboot slot payload).

Optional ``--resign-merged`` attempts imgtool on the merged hex (usually fails
with the stock FLPR partition map because the address span exceeds slot size).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_SLOT_SIZE = "0xE6000"  # 920 KiB — nordic-flpr WITH_FLPR_PARTITIONS
DEFAULT_VERSION = "1.0.0+0"
APP_IMAGE_CANDIDATES = (
    "multi_endpoint",
    "zephyr-ipc-multi-endpoint-mds-flpr",
)


def find_zephyr_base(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("ZEPHYR_BASE")
    if env:
        return Path(env).resolve()
    fallback = Path(r"C:\ncs\zephyr")
    if fallback.is_dir():
        return fallback.resolve()
    raise SystemExit("ZEPHYR_BASE is not set and C:\\ncs\\zephyr was not found")


def find_key(zephyr_base: Path, explicit: str | None) -> Path:
    if explicit:
        key = Path(explicit).resolve()
        if not key.is_file():
            raise SystemExit(f"Signing key not found: {key}")
        return key

    candidates = [
        zephyr_base.parent / "bootloader" / "mcuboot" / "root-ed25519.pem",
        zephyr_base / "bootloader" / "mcuboot" / "root-ed25519.pem",
        zephyr_base.parent / "bootloader" / "mcuboot" / "root-rsa-2048.pem",
        zephyr_base / "bootloader" / "mcuboot" / "root-rsa-2048.pem",
    ]
    for key in candidates:
        if key.is_file():
            return key.resolve()
    raise SystemExit("Signing key not found. Pass --key path/to/key.pem")


def find_tool(zephyr_base: Path, *rel_parts: str) -> Path:
    primary = zephyr_base.joinpath(*rel_parts)
    if primary.is_file():
        return primary.resolve()
    if rel_parts[0] == "bootloader":
        alt = zephyr_base.parent.joinpath(*rel_parts)
        if alt.is_file():
            return alt.resolve()
    raise SystemExit(f"Tool not found: {primary}")


def detect_app_image(build_dir: Path, explicit: str | None) -> str:
    if explicit:
        hex_path = build_dir / explicit / "zephyr" / "zephyr.hex"
        if not hex_path.is_file():
            raise SystemExit(f"Missing app hex: {hex_path}")
        return explicit

    for cand in APP_IMAGE_CANDIDATES:
        if (build_dir / cand / "zephyr" / "zephyr.hex").is_file():
            return cand

    raise SystemExit(f"Could not find application zephyr.hex under {build_dir}")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create DFU artifacts (app_update.bin + optional mergehex)"
    )
    parser.add_argument(
        "--build-dir",
        required=True,
        help="Sysbuild output directory (contains <app>/zephyr and remote/zephyr)",
    )
    parser.add_argument(
        "--app-image",
        default="",
        help="Sysbuild app folder name under build-dir (auto-detect if omitted)",
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"imgtool version if --resign-* is used (default: {DEFAULT_VERSION})",
    )
    parser.add_argument(
        "--slot-size",
        default=DEFAULT_SLOT_SIZE,
        help=f"Primary slot size (default: {DEFAULT_SLOT_SIZE})",
    )
    parser.add_argument("--key", default="", help="PEM signing key path")
    parser.add_argument(
        "--zephyr-base",
        default="",
        help="Zephyr tree (default: $ZEPHYR_BASE or C:\\ncs\\zephyr)",
    )
    parser.add_argument(
        "--resign-app",
        action="store_true",
        help="Re-sign app zephyr.hex with imgtool instead of copying zephyr.signed.bin",
    )
    parser.add_argument(
        "--resign-merged",
        action="store_true",
        help="Also imgtool-sign the merged app+FLPR hex (usually too large for slot0)",
    )
    args = parser.parse_args()

    build_dir = Path(args.build_dir).resolve()
    zephyr_base = find_zephyr_base(args.zephyr_base or None)
    app_image = detect_app_image(build_dir, args.app_image or None)
    key = find_key(zephyr_base, args.key or None)

    app_hex = build_dir / app_image / "zephyr" / "zephyr.hex"
    app_signed_bin = build_dir / app_image / "zephyr" / "zephyr.signed.bin"
    flpr_hex = build_dir / "remote" / "zephyr" / "zephyr.hex"
    if not flpr_hex.is_file():
        raise SystemExit(f"Missing FLPR hex: {flpr_hex}")

    mergehex = find_tool(zephyr_base, "scripts", "build", "mergehex.py")
    imgtool = find_tool(zephyr_base, "bootloader", "mcuboot", "scripts", "imgtool.py")

    out_dir = build_dir / "dfu"
    out_dir.mkdir(parents=True, exist_ok=True)
    merged = out_dir / "app_and_flpr_merged.hex"
    signed_bin = out_dir / "app_update.bin"

    print(f"App:  {app_hex}")
    print(f"FLPR: {flpr_hex}")
    print(f"Key:  {key}")
    print(f"Slot: {args.slot_size}  Version: {args.version}")

    run([sys.executable, str(mergehex), str(flpr_hex), str(app_hex), "-o", str(merged)])

    if args.resign_app or not app_signed_bin.is_file():
        print("Signing app hex with imgtool → app_update.bin")
        run(
            [
                sys.executable,
                str(imgtool),
                "sign",
                "--version",
                args.version,
                "--align",
                "16",
                "--slot-size",
                args.slot_size,
                "--pad-header",
                "--header-size",
                "0x800",
                "-k",
                str(key),
                str(app_hex),
                str(signed_bin),
            ]
        )
    else:
        shutil.copy2(app_signed_bin, signed_bin)
        print(f"Copied build-signed image -> {signed_bin}")

    if args.resign_merged:
        signed_merged = out_dir / "app_and_flpr_merged.signed.hex"
        run(
            [
                sys.executable,
                str(imgtool),
                "sign",
                "--version",
                args.version,
                "--align",
                "16",
                "--slot-size",
                args.slot_size,
                "--pad-header",
                "--header-size",
                "0x800",
                "-k",
                str(key),
                str(merged),
                str(signed_merged),
            ]
        )

    print("Wrote:")
    print(f"  {merged}")
    print(f"  {signed_bin}")
    print(
        "Note: with nordic-flpr, FLPR is outside MCUboot slots; "
        "app_update.bin is cpuapp-only. Reflash remote when FLPR changes."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
