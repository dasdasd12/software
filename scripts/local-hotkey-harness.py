#!/usr/bin/env python3
"""Run the Local API global hotkey test harness."""

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from harness.hotkeys import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
