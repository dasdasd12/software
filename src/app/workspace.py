"""Workspace resolution for agent launches."""

import os
from pathlib import Path
from typing import Mapping, Optional


def resolve_workspace(
    cli_workspace: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    start: Optional[Path] = None,
    config_default: str = ".",
) -> Path:
    """Resolve the project workspace for agent launches."""
    base = Path(start) if start is not None else Path.cwd()
    base = base.resolve()
    env_map = os.environ if env is None else env

    for candidate in (
        cli_workspace,
        env_map.get("AI_KEYB_WORKSPACE") if env_map is not None else None,
    ):
        if candidate:
            return _resolve_path(candidate, base)

    if config_default and str(config_default).strip() not in {".", ""}:
        return _resolve_path(str(config_default), base)

    found = _find_project_root(base)
    return found if found is not None else base


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _find_project_root(start: Path) -> Optional[Path]:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "software").is_dir():
            return candidate
    return None
