#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from keyboard.factory_profile_runtime import load_compile_write  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile factory_default_profile.json to the H417 AKPF flash image."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "config" / "factory_default_profile.json",
        help="Source profile JSON path.",
    )
    parser.add_argument(
        "--bin",
        type=Path,
        default=ROOT / "build" / "factory_default_profile.akpf.bin",
        help="Output binary image path.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "build" / "factory_default_profile.akpf.json",
        help="Output compile manifest path.",
    )
    parser.add_argument(
        "--c",
        type=Path,
        default=None,
        help="Optional generated C image source path.",
    )
    parser.add_argument(
        "--h",
        type=Path,
        default=None,
        help="Optional generated C image header path.",
    )
    args = parser.parse_args()

    result = load_compile_write(args.source, args.bin, args.manifest, args.c, args.h)
    print(f"wrote {args.bin} ({len(result.image)} bytes)")
    if args.manifest is not None:
        print(f"wrote {args.manifest}")
    if args.c is not None:
        print(f"wrote {args.c}")
    if result.manifest["warnings"]:
        print("warnings:")
        for warning in result.manifest["warnings"]:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
