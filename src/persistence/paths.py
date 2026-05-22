"""Storage path helpers for the Local Core Service."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoragePaths:
    app_data: Path
    local_data: Path

    @property
    def database_path(self) -> Path:
        return self.app_data / "app.db"

    @property
    def logs_dir(self) -> Path:
        return self.local_data / "logs"

    @property
    def diagnostics_dir(self) -> Path:
        return self.local_data / "diagnostics"


def default_storage_paths(app_name: str = "AI Keyboard") -> StoragePaths:
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    localappdata = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return StoragePaths(
        app_data=appdata / app_name,
        local_data=localappdata / app_name,
    )
