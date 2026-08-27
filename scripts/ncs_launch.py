#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Run a command via nrfutil with the NCS west workspace as cwd.

Resolves the workspace from `nrfutil toolchain-manager list --json` for the
requested NCS version (toolchain path .../toolchains/<hash> -> parent parent).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def ncs_root_for(version: str) -> Path:
    out = subprocess.check_output(
        ["nrfutil", "toolchain-manager", "list", "--json", "--skip-overhead"],
        text=True,
    )
    data = json.loads(out)
    for toolchain in data.get("toolchains", []):
        if toolchain.get("ncs_version") == version:
            # <install>/toolchains/<hash> -> <install> (west workspace root)
            return Path(toolchain["path"]).resolve().parent.parent
    installed = ", ".join(t.get("ncs_version", "?") for t in data.get("toolchains", []))
    raise SystemExit(
        f"NCS {version} not found in nrfutil toolchain-manager list "
        f"(installed: {installed or 'none'})"
    )


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: ncs_launch.py <ncs-version> <command> [args...]\n"
            "       ncs_launch.py <ncs-version> --print-root",
            file=sys.stderr,
        )
        return 2

    version = sys.argv[1]
    root = ncs_root_for(version)

    if sys.argv[2] == "--print-root":
        print(root.as_posix())
        return 0

    argv = [
        "nrfutil",
        "toolchain-manager",
        "launch",
        f"--ncs-version={version}",
        f"--chdir={root.as_posix()}",
        "--",
        *sys.argv[2:],
    ]
    print("+", " ".join(argv), flush=True)
    return subprocess.call(argv)


if __name__ == "__main__":
    raise SystemExit(main())
