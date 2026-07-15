"""Desktop-only bridge between the Tauri shell and keyboard services.

The package deliberately has no import-time device discovery or connection
side effects.  Run it with ``python -m desktop_bridge --stdio`` to start the
newline-delimited JSON protocol.
"""

from __future__ import annotations

BRIDGE_VERSION = "0.1.0"
PROTOCOL_VERSION = "1"

__all__ = ["BRIDGE_VERSION", "PROTOCOL_VERSION"]
