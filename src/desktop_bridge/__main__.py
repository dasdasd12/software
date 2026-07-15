"""Command-line entry point for the desktop JSONL sidecar."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .protocol import serve_jsonl
from .service import DesktopBridgeService, default_factory_profile_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KIIIe keyboard desktop bridge (JSONL over stdio)"
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="serve newline-delimited JSON requests on stdin/stdout",
    )
    parser.add_argument(
        "--factory-profile",
        type=Path,
        default=default_factory_profile_path(),
        help="path to factory_default_profile.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.stdio:
        _parser().error("--stdio is required")

    # Rust launches the sidecar with ``-u``.  Reconfigure as a second line of
    # defence for direct development runs, especially on Windows consoles.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if stdout_reconfigure is not None:
        stdout_reconfigure(line_buffering=True)

    service = DesktopBridgeService(args.factory_profile)
    return serve_jsonl(service)


if __name__ == "__main__":
    raise SystemExit(main())
