#!/usr/bin/env python3
# Copyright (c) 2026 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0
"""Convert west ncs-sbom tag-value SPDX docs and merge into one spdx.json.

Expects ``build/sbom/sbom_*.spdx`` from::

    west ncs-sbom -d build --output-spdx build/sbom/sbom_{domain}.spdx

Then runs syft (from anchore_syft or PATH) to produce ``build/sbom/spdx.json``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SAMPLE_ROOT = Path(__file__).resolve().parents[1]


def parse_version_string(path: Path) -> str:
    """Parse Zephyr VERSION → MAJOR.MINOR.PATCH[-EXTRAVERSION]."""
    if not path.is_file():
        return "0.0.0"

    fields: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        fields[key.strip()] = val.strip()

    major = fields.get("VERSION_MAJOR", "0")
    minor = fields.get("VERSION_MINOR", "0")
    patch = fields.get("PATCHLEVEL", "0")
    extra = fields.get("EXTRAVERSION", "")
    ver = f"{major}.{minor}.{patch}"
    if extra:
        ver = f"{ver}-{extra}"
    return ver


def find_syft() -> str:
    """Resolve syft binary from PATH or anchore_syft package."""
    for name in ("syft", "anchore_syft"):
        path = shutil.which(name)
        if path:
            return path

    try:
        import anchore_syft  # type: ignore

        pkg_dir = Path(anchore_syft.__file__).resolve().parent
        for cand in pkg_dir.rglob("syft*"):
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
            # Windows: syft.exe
            if cand.is_file() and cand.suffix.lower() in (".exe", ""):
                if cand.name.lower().startswith("syft"):
                    return str(cand)
    except ImportError:
        pass

    raise SystemExit(
        "syft not found on PATH and anchore_syft is not installed.\n"
        "Install SBOM deps once:\n"
        f"  {sys.executable} -m pip install -r {SAMPLE_ROOT / 'scripts' / 'requirements-sbom.txt'}"
    )


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.check_call(cmd, cwd=cwd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=SAMPLE_ROOT / "build",
        help="Sysbuild output directory (default: <sample>/build)",
    )
    parser.add_argument(
        "--name",
        default=SAMPLE_ROOT.name,
        help="Product/source name for the merged SBOM",
    )
    parser.add_argument(
        "--version-file",
        type=Path,
        default=SAMPLE_ROOT / "VERSION",
        help="Zephyr VERSION file for --source-version",
    )
    args = parser.parse_args()

    build_dir = args.build_dir.resolve()
    sbom_dir = build_dir / "sbom"
    raw_dir = sbom_dir / "raw"
    out_json = sbom_dir / "spdx.json"

    spdx_inputs = sorted(sbom_dir.glob("sbom_*.spdx"))
    if not spdx_inputs:
        raise SystemExit(
            f"No ncs-sbom outputs matching {sbom_dir / 'sbom_*.spdx'}.\n"
            "Run: west ncs-sbom -d <build> --output-spdx <build>/sbom/sbom_{domain}.spdx"
        )

    syft = find_syft()
    version = parse_version_string(args.version_file.resolve())

    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    for spdx_path in spdx_inputs:
        dest = raw_dir / f"{spdx_path.stem}.spdx.json"
        run(
            [syft, "convert", str(spdx_path), f"-o=spdx-json={dest}"],
            cwd=SAMPLE_ROOT,
        )
        if not dest.is_file() or dest.stat().st_size == 0:
            raise SystemExit(f"syft convert produced no output: {dest}")

    # Relative paths avoid Windows `sbom:C:\...` colon issues for later grype use.
    raw_rel = os.path.relpath(raw_dir, SAMPLE_ROOT).replace("\\", "/")
    out_rel = os.path.relpath(out_json, SAMPLE_ROOT).replace("\\", "/")

    run(
        [
            syft,
            "scan",
            f"dir:{raw_rel}",
            "--override-default-catalogers",
            "sbom-cataloger",
            "--source-name",
            args.name,
            "--source-version",
            version,
            f"-o=spdx-json={out_rel}",
        ],
        cwd=SAMPLE_ROOT,
    )

    if not out_json.is_file() or out_json.stat().st_size == 0:
        raise SystemExit(f"Merged SBOM missing: {out_json}")

    print(f"Wrote {out_json}")
    print(f"Scan with: grype sbom:{out_rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
