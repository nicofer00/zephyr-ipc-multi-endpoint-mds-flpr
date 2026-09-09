#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Locate the shared NCS west workspace from nrfutil (no SDK clone into this repo)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _toolchain_path(version: str) -> Path:
    out = subprocess.check_output(
        ["nrfutil", "toolchain-manager", "list", "--json", "--skip-overhead"],
        text=True,
    )
    data = json.loads(out)
    for toolchain in data.get("toolchains", []):
        if toolchain.get("ncs_version") == version:
            return Path(toolchain["path"]).resolve()
    installed = ", ".join(t.get("ncs_version", "?") for t in data.get("toolchains", []))
    raise SystemExit(
        f"NCS toolchain {version} not found (installed: {installed or 'none'}).\n"
        f"Install once (shared, outside this repo):\n"
        f"  nrfutil toolchain-manager install --ncs-version {version}"
    )


def _install_dir_hint() -> Path | None:
    try:
        out = subprocess.check_output(
            ["nrfutil", "toolchain-manager", "config", "--show"],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    match = re.search(r"Install directory:\s*(.+)", out)
    if not match:
        return None
    path = Path(match.group(1).strip()).resolve()
    return path if path.is_dir() else None


def _find_west_root(start: Path) -> Path | None:
    """Walk start and its parents for a directory that has .west/ and zephyr/."""
    cur = start.resolve()
    candidates = [cur, *cur.parents]
    for parent in candidates:
        if (parent / ".west").is_dir() and (parent / "zephyr").is_dir():
            return parent
        versioned = [
            p
            for p in parent.glob("v*")
            if p.is_dir() and (p / ".west").is_dir() and (p / "zephyr").is_dir()
        ]
        if versioned:
            return sorted(versioned, key=lambda p: p.name)[-1]
    return None


def ncs_root_for(version: str) -> Path:
    toolchain = _toolchain_path(version)
    root = _find_west_root(toolchain)
    if root is None:
        hint = _install_dir_hint()
        if hint is not None:
            root = _find_west_root(hint)
            versioned = hint / version
            if (versioned / ".west").is_dir() and (versioned / "zephyr").is_dir():
                root = versioned

    if root is None:
        hint = _install_dir_hint()
        where = hint.as_posix() if hint else "(nrfutil install-dir)"
        raise SystemExit(
            f"No west workspace (.west + zephyr) found for NCS {version}.\n"
            f"Toolchain is at {toolchain.as_posix()}, but SDK sources are missing.\n"
            f"\n"
            f"Install the SDK once into the shared nrfutil install dir ({where}),\n"
            f"not into this application repo. For example:\n"
            f"  nrfutil sdk-manager install {version}\n"
            f"or use nRF Connect for Desktop → Toolchain Manager → install SDK {version}.\n"
            f"\n"
            f"Then re-run: just setup {version}"
        )
    return root


def _normalize_arg(arg: str) -> str:
    """nrfutil on Windows drops backslashes in forwarded argv; use POSIX paths."""
    if "\\" not in arg:
        return arg
    return arg.replace("\\", "/")


def doctor(version: str) -> int:
    root = ncs_root_for(version)
    print(f"NCS version : {version}")
    print(f"west topdir : {root.as_posix()}")
    print(f"ZEPHYR_BASE : {(root / 'zephyr').as_posix()}")
    print("This application repo is not a west workspace; use `just build` / `just west`.")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: ncs_launch.py <ncs-version> <command> [args...]\n"
            "       ncs_launch.py <ncs-version> --print-root\n"
            "       ncs_launch.py <ncs-version> --doctor",
            file=sys.stderr,
        )
        return 2

    version = sys.argv[1]
    if len(sys.argv) == 2:
        return doctor(version)

    if sys.argv[2] == "--print-root":
        print(ncs_root_for(version).as_posix())
        return 0

    if sys.argv[2] == "--doctor":
        return doctor(version)

    root = ncs_root_for(version)
    zephyr_base = (root / "zephyr").resolve()
    cmd = [_normalize_arg(a) for a in sys.argv[2:]]
    argv = [
        "nrfutil",
        "toolchain-manager",
        "launch",
        f"--ncs-version={version}",
        f"--chdir={root.as_posix()}",
        "--",
        *cmd,
    ]
    print("+", " ".join(argv), flush=True)
    env = os.environ.copy()
    env["ZEPHYR_BASE"] = str(zephyr_base)
    env["WEST_TOPDIR"] = str(root.resolve())
    env["NCS_VERSION"] = version
    return subprocess.call(argv, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
