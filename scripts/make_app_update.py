#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Build DFU artifacts for this sample (Strategy B coupled FLPR + cpuapp OTA).

Default (coupled):
- mergehex(remote, app) → app_and_flpr_merged.hex
- imgtool sign merged hex → build/dfu/app_update.bin (single MCUboot slot image)

``--cpuapp-only`` signs cpuapp zephyr.hex / zephyr.signed.bin only (debug).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_SLOT_SIZE = "0xF2000"  # 968 KiB — full slots (no WITH_FLPR_PARTITIONS)
DEFAULT_VERSION = "1.0.0+0"
FLPR_PAYLOAD_ADDR = 0xE8000
FLPR_PAYLOAD_SIZE = 0x18000  # 96 KiB
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


def hex_address_span(hex_path: Path) -> tuple[int, int]:
    """Return (min_addr, max_exclusive) for data records in an Intel HEX file."""
    upper = 0
    min_addr: int | None = None
    max_addr = 0

    with hex_path.open(encoding="ascii") as f:
        for line in f:
            if not line.startswith(":"):
                continue
            count = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            rectype = int(line[7:9], 16)
            if rectype == 2:
                upper = int(line[9:13], 16) << 4
            elif rectype == 4:
                upper = int(line[9:13], 16) << 16
            elif rectype == 0:
                full = upper + addr
                min_addr = full if min_addr is None else min(min_addr, full)
                max_addr = max(max_addr, full + count)
            elif rectype == 1:
                break

    if min_addr is None:
        raise SystemExit(f"No data records in {hex_path}")

    return min_addr, max_addr


def verify_flpr_hex(flpr_hex: Path) -> None:
    min_addr, max_addr = hex_address_span(flpr_hex)
    span = max_addr - min_addr

    if min_addr != FLPR_PAYLOAD_ADDR:
        raise SystemExit(
            f"FLPR hex starts at {min_addr:#x}, expected {FLPR_PAYLOAD_ADDR:#x}. "
            "Rebuild remote with coupled overlay (cpuflpr_rram @ 0xE8000)."
        )
    if span > FLPR_PAYLOAD_SIZE:
        raise SystemExit(
            f"FLPR image span {span} bytes ({span:#x}) exceeds partition "
            f"{FLPR_PAYLOAD_SIZE} bytes ({FLPR_PAYLOAD_SIZE:#x})."
        )

    print(f"FLPR payload OK: {min_addr:#x}..{max_addr:#x} ({span} bytes)")


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def sign_image(imgtool: Path, key: Path, slot_size: str, version: str, src: Path, dst: Path) -> None:
    """Sign like NCS image_signing.cmake for nRF54L ed25519 builds.

    Matches the board build: ``--sha 512 --rom-fixed 0x10000 --header-size 0x800``
    and **no** ``--pad-header`` (input already has the 0x800 zero header gap).
    Using ``--pad-header`` on such an image shifts app+FLPR by +0x800 and breaks
    XIP / VPR load addresses. SHA256 TLVs are rejected by img_mgmt (expects
    IMAGE_SHA_LEN=64 / SHA512), so slot1 never appears after upload.
    """
    run(
        [
            sys.executable,
            str(imgtool),
            "sign",
            "--version",
            version,
            "--align",
            "16",
            "--slot-size",
            slot_size,
            "--header-size",
            "0x800",
            "--rom-fixed",
            "0x10000",
            "--sha",
            "512",
            "-k",
            str(key),
            str(src),
            str(dst),
        ]
    )


def merged_hex_to_bin(merged_hex: Path, out_bin: Path) -> None:
    """Flatten merged Intel HEX to a contiguous bin (minaddr..maxaddr)."""
    try:
        from intelhex import IntelHex
    except ImportError as exc:
        raise SystemExit(
            "intelhex is required to flatten the merged image; "
            "use the NCS toolchain Python or: pip install intelhex"
        ) from exc

    ih = IntelHex(str(merged_hex))
    data = bytes(ih.tobinarray(start=ih.minaddr(), end=ih.maxaddr()))
    # Slot image must start at slot base with zeroed MCUboot header gap.
    if ih.minaddr() != 0x10000:
        raise SystemExit(
            f"Merged hex minaddr {ih.minaddr():#x} != slot base 0x10000"
        )
    if any(data[:0x800]):
        raise SystemExit("Merged image header gap (first 0x800) is not zeros")

    flpr_off = FLPR_PAYLOAD_ADDR - 0x10000
    if flpr_off + 16 > len(data):
        raise SystemExit("Merged image shorter than FLPR payload offset")
    print(
        f"Merged bin: {len(data)} bytes; FLPR@{flpr_off:#x}="
        f"{data[flpr_off:flpr_off+4].hex()}"
    )
    out_bin.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create coupled DFU artifacts (merged app+FLPR app_update.bin)"
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
        help=f"imgtool version string (default: {DEFAULT_VERSION})",
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
        "--cpuapp-only",
        action="store_true",
        help="Sign cpuapp-only image (no FLPR merge); debug / legacy",
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

    if args.cpuapp_only:
        if app_signed_bin.is_file():
            shutil.copy2(app_signed_bin, signed_bin)
            print(f"Copied build-signed cpuapp image -> {signed_bin}")
        else:
            app_bin = build_dir / app_image / "zephyr" / "zephyr.bin"
            src = app_bin if app_bin.is_file() else app_hex
            sign_image(imgtool, key, args.slot_size, args.version, src, signed_bin)
        print("Wrote (cpuapp-only):", signed_bin)
        return 0

    verify_flpr_hex(flpr_hex)
    run([sys.executable, str(mergehex), str(flpr_hex), str(app_hex), "-o", str(merged)])
    merged_bin = out_dir / "app_and_flpr_merged.bin"
    merged_hex_to_bin(merged, merged_bin)
    sign_image(imgtool, key, args.slot_size, args.version, merged_bin, signed_bin)

    print("Wrote:")
    print(f"  {merged}")
    print(f"  {merged_bin}")
    print(f"  {signed_bin}")
    print("Coupled OTA: app_update.bin contains cpuapp + in-slot FLPR payload @ 0xE8000.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc
