# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for USB/BLE DFU scripts."""

from __future__ import annotations


def _img_flags(img) -> dict:
    return {
        "slot": getattr(img, "slot", None),
        "version": str(getattr(img, "version", "")),
        "active": bool(getattr(img, "active", False)),
        "confirmed": bool(getattr(img, "confirmed", False)),
        "pending": bool(getattr(img, "pending", False)),
        "permanent": bool(getattr(img, "permanent", False)),
    }


async def ensure_ab_slot_ready(client, timeout: float, ui) -> None:
    """Confirm running image + erase secondary so the next upload has a free slot.

    After a *test* swap, MCUboot keeps the old image in slot1 for revert. Until the
    new image is confirmed, img_mgmt returns NO_FREE_SLOT on upload.
    """
    from smpclient.generics import error
    from smpclient.requests.image_management import ImageErase, ImageStatesRead, ImageStatesWrite

    with ui.status("[cyan]Checking image slots...", spinner="line"):
        states = await client.request(ImageStatesRead(), timeout_s=timeout)
    if error(states):
        raise SystemExit(f"image state-read failed: {states}")

    images = list(getattr(states, "images", []) or [])
    flags = [_img_flags(i) for i in images]
    for f in flags:
        ui.print(
            f"[dim]slot{f['slot']}: v={f['version']} "
            f"active={f['active']} confirmed={f['confirmed']} "
            f"pending={f['pending']}[/]"
        )

    need_confirm = any(f["active"] and not f["confirmed"] for f in flags)
    if need_confirm:
        with ui.status("[cyan]Confirming active image (end test/revert)...", spinner="line"):
            # confirm=True, hash=None → confirm currently running image
            resp = await client.request(ImageStatesWrite(confirm=True), timeout_s=timeout)
        if error(resp):
            raise SystemExit(
                f"Failed to confirm active image: {resp}\n"
                "Without confirm, secondary stays reserved for MCUboot revert "
                "(IMG_MGMT_ERR.NO_FREE_SLOT)."
            )
        ui.print("[green]Confirmed[/] active image")

    with ui.status("[cyan]Erasing secondary slot...", spinner="line"):
        erase = await client.request(ImageErase(), timeout_s=timeout)
    if error(erase):
        # Empty secondary is fine; other errors are not.
        msg = str(erase)
        if "NO_IMAGE" in msg or "no image" in msg.lower():
            ui.print("[dim]Secondary slot already empty[/]")
        else:
            raise SystemExit(f"image erase (slot1) failed: {erase}")
    else:
        ui.print("[green]Secondary slot erased[/] — ready for next A/B upload")


async def mark_uploaded_image(client, confirm: bool, timeout: float, ui) -> None:
    """Mark the inactive (uploaded) slot for test or permanent confirm, then reset."""
    from smpclient.generics import error
    from smpclient.requests.image_management import ImageStatesRead, ImageStatesWrite
    from smpclient.requests.os_management import ResetWrite

    with ui.status("[cyan]Reading image state...", spinner="line"):
        states = await client.request(ImageStatesRead(), timeout_s=timeout)
    if error(states):
        raise SystemExit(f"image state-read failed: {states}")

    images = list(getattr(states, "images", []) or [])
    flags = [_img_flags(i) for i in images]
    for f in flags:
        ui.print(
            f"[dim]post-upload slot{f['slot']}: v={f['version']} "
            f"active={f['active']} confirmed={f['confirmed']}[/]"
        )

    active_hash: bytes | None = None
    inactive_hash: bytes | None = None
    for img in images:
        h = getattr(img, "hash", None)
        if not h:
            continue
        hb = bytes(h)
        if getattr(img, "active", False):
            active_hash = hb
        else:
            inactive_hash = hb

    if inactive_hash is None:
        raise SystemExit(f"upload finished but no inactive-slot hash to mark: {states}")

    # img_mgmt_find_by_hash() returns the first matching slot (usually active).
    # Re-uploading the same binary makes both slots share one hash → test denied.
    if active_hash is not None and inactive_hash == active_hash:
        raise SystemExit(
            "Uploaded image is identical to the running image (same MCUmgr hash).\n"
            "MCUmgr cannot set 'test' on the active slot "
            "(IMAGE_SETTING_TEST_TO_ACTIVE_DENIED).\n"
            "Bump the sample VERSION file, rebuild, re-package, then DFU again."
        )

    mark = "confirm" if confirm else "test"
    with ui.status(f"[cyan]Marking inactive image for {mark}...", spinner="line"):
        write = ImageStatesWrite(hash=inactive_hash, confirm=confirm)
        resp = await client.request(write, timeout_s=timeout)
    if error(resp):
        raise SystemExit(f"image state-write failed: {resp}")
    ui.print(f"[green]Marked[/] for {mark}")

    with ui.status("[cyan]Resetting device...", spinner="line"):
        reset = await client.request(ResetWrite(), timeout_s=timeout)
    if error(reset):
        raise SystemExit(f"reset failed: {reset}")
    ui.print("[green]Reset sent[/] — device will swap to the new image")
