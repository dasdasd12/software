"""Compile the factory profile JSON into an AKPK package.

Outputs the package binary + manifest under build/ and (optionally) the
C array image embedded into the H417 V3F firmware.

Usage:
    python scripts/compile-factory-akpk.py [--firmware-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keyboard.akpk import build_package, emit_c_image  # noqa: E402

DEFAULT_FIRMWARE_DIR = (
    ROOT.parent / "hardware" / "firmware" / "h417" / "v3f" / "applications"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "config" / "factory_default_profile.json",
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build")
    parser.add_argument(
        "--firmware-dir",
        type=Path,
        default=DEFAULT_FIRMWARE_DIR,
        help="directory for factory_profile_image.c/.h (empty to skip)",
    )
    args = parser.parse_args()

    profile = json.loads(args.source.read_text(encoding="utf-8"))
    result = build_package(profile)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = args.out_dir / "factory_default_profile.akpk.bin"
    manifest_path = args.out_dir / "factory_default_profile.akpk.json"
    bin_path.write_bytes(result.package)
    manifest_path.write_text(
        json.dumps(result.manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if args.firmware_dir and str(args.firmware_dir):
        source_text, header_text = emit_c_image(
            result.package,
            symbol="g_v3f_factory_profile_image",
            header_guard="V3F_FACTORY_PROFILE_IMAGE_H",
            header_name="factory_profile_image.h",
        )
        args.firmware_dir.mkdir(parents=True, exist_ok=True)
        (args.firmware_dir / "factory_profile_image.c").write_text(
            source_text, encoding="ascii"
        )
        (args.firmware_dir / "factory_profile_image.h").write_text(
            header_text, encoding="ascii"
        )

    print(f"package: {bin_path} ({len(result.package)} bytes)")
    print(f"runtime table: {len(result.runtime_table)} bytes")
    print(f"profile_id16: 0x{result.profile_id16:04x} "
          f"revision: {result.revision}")
    for warning in result.warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
