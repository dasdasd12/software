"""Compile a profile JSON and upload it into a keyboard slot over CDC.

The keyboard exposes the config channel as a USB CDC serial port
(interface of the USBFS composite device). No firmware rebuild is
needed: the JSON is compiled to an AKPK package on the fly, streamed
into the selected user slot and activated.

Examples:
    python scripts/upload-profile.py --port COM5 --slot 1 my_profile.json
    python scripts/upload-profile.py --port COM5 --info
    python scripts/upload-profile.py --port COM5 --activate 2
    python scripts/upload-profile.py --port COM5 --factory
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from keyboard.akpk import build_package  # noqa: E402
from devices.transports.serial_cdc import AkpkSerialClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path,
                        help="profile JSON to compile and upload")
    parser.add_argument("--port", required=True, help="CDC serial port")
    parser.add_argument("--slot", type=int, default=1,
                        help="target user slot 1..3 (default 1)")
    parser.add_argument("--chunk", type=int, default=64,
                        help="bytes per DATA line (default 64)")
    parser.add_argument("--no-activate", action="store_true",
                        help="upload only, keep the current profile")
    parser.add_argument("--info", action="store_true",
                        help="query device state and exit")
    parser.add_argument("--activate", type=int, metavar="SLOT",
                        help="activate an existing slot and exit")
    parser.add_argument("--factory", action="store_true",
                        help="activate the factory default and exit")
    args = parser.parse_args()

    with AkpkSerialClient(args.port) as client:
        print(f"device: {client.ping()}")

        if args.info:
            print(_format_info(client))
            return 0
        if args.factory:
            print(f"activate: {client.activate(0)}")
            print(_format_info(client))
            return 0
        if args.activate is not None:
            print(f"activate: {client.activate(args.activate)}")
            print(_format_info(client))
            return 0

        if args.source is None:
            parser.error("profile JSON required unless using "
                         "--info/--activate/--factory")

        profile = json.loads(args.source.read_text(encoding="utf-8"))
        result = build_package(profile)
        for warning in result.warnings:
            print(f"warning: {warning}")
        print(f"package: {len(result.package)} bytes "
              f"(profile_id={result.profile_id} "
              f"id16=0x{result.profile_id16:04x} rev={result.revision})")

        def progress(done: int, total: int) -> None:
            percent = (done * 100) // total
            print(f"\rupload: {done}/{total} bytes ({percent}%)",
                  end="", flush=True)

        client.upload(result.package, args.slot, args.chunk, progress)
        print("\ncommit: ok")

        if not args.no_activate:
            print(f"activate: {client.activate(args.slot)}")

        print(_format_info(client))
    return 0


def _format_info(client: AkpkSerialClient) -> str:
    info = client.info()
    slots = "".join("1" if v else "0" for v in info.slot_valid)
    return (f"state: active={info.active_slot} "
            f"id16=0x{info.profile_id16:04x} gen={info.generation} "
            f"slots={slots}")


if __name__ == "__main__":
    raise SystemExit(main())
