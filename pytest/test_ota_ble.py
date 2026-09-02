# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""BLE OTA pytest: coupled app+FLPR MCUboot image (Strategy B)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest
from twister_harness import DeviceAdapter, MCUmgrBle, Shell
from twister_harness.helpers.utils import find_in_config, match_lines

logger = logging.getLogger(__name__)

WELCOME_STRING = r"APP version:"
UPGRADE_VERSION = "1.0.1+0"


def _sample_root() -> Path:
    return Path(__file__).resolve().parents[1]


def create_coupled_upgrade_image(build_dir: Path, version: str) -> Path:
    script = _sample_root() / "scripts" / "make_app_update.py"
    out = build_dir / "dfu" / f"test_{version.replace('.', '_').replace('+', '_')}.bin"

    subprocess.run(
        [
            sys.executable,
            str(script),
            "--build-dir",
            str(build_dir),
            "--version",
            version,
        ],
        check=True,
    )

    base = build_dir / "dfu" / "app_update.bin"
    if not base.is_file():
        raise AssertionError(f"DFU script did not produce {base}")

    if out != base:
        out.write_bytes(base.read_bytes())

    return out


def get_upgrade_string_to_verify(build_dir: Path) -> str:
    sysbuild_config = build_dir / "zephyr" / ".config"
    if find_in_config(sysbuild_config, "SB_CONFIG_MCUBOOT_MODE_SWAP_USING_OFFSET"):
        return "Starting swap using offset algorithm"
    return "Starting swap using move algorithm"


def clear_buffer(dut: DeviceAdapter) -> None:
    disconnect = False
    if not dut.is_device_connected():
        dut.connect()
        disconnect = True
    dut.clear_buffer()
    if disconnect:
        dut.disconnect()


def test_coupled_ota_ble_confirm(mcumgr_ble: MCUmgrBle, dut: DeviceAdapter, shell: Shell):
    """Upload merged app+FLPR image over BLE; verify version and FLPR IPC after swap."""
    build_dir = Path(dut.device_config.build_dir)
    logger.info("Prepare coupled upgrade image @ %s", UPGRADE_VERSION)
    image_to_test = create_coupled_upgrade_image(build_dir, UPGRADE_VERSION)

    logger.info("Upload merged image with mcumgr (BLE)")
    dut.disconnect()
    mcumgr_ble.image_upload(image_to_test)

    logger.info("Test uploaded image")
    second_hash = mcumgr_ble.get_hash_to_test()
    mcumgr_ble.image_test(second_hash)
    clear_buffer(dut)
    mcumgr_ble.reset_device()

    dut.connect()
    output = dut.readlines_until(regex=WELCOME_STRING)
    upgrade_string = get_upgrade_string_to_verify(build_dir)
    match_lines(
        output,
        [
            "Swap type: test",
            upgrade_string,
            f"APP version: {UPGRADE_VERSION}",
        ],
    )

    logger.info("Verify FLPR IPC after coupled OTA")
    pong = shell.exec_command("flpr ping")
    assert "pong ok" in pong

    logger.info("Confirm image")
    mcumgr_ble.image_confirm(second_hash)
    mcumgr_ble.reset_device()

    dut.connect()
    output = dut.readlines_until(regex=WELCOME_STRING)
    match_lines(output, [f"APP version: {UPGRADE_VERSION}"])

    pong = shell.exec_command("flpr ping")
    assert "pong ok" in pong
