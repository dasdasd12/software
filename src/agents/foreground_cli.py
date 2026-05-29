"""Foreground CLI launcher helpers for managed local agent sessions."""

import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional


VALID_AGENTS = {"claude", "codex"}
LAUNCH_TOKEN_ENV = "AI_KEYB_LAUNCH_TOKEN"
SAFE_ENV_KEYS = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "APPDATA",
    "LOCALAPPDATA",
    "PYTHONPATH",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script_path() -> Path:
    return _repo_root() / "scripts" / "local-agent-cli.py"


def _validate_agent(agent: str) -> str:
    if not isinstance(agent, str) or agent not in VALID_AGENTS:
        raise ValueError("agent must be one of: claude, codex")
    return agent


def _validate_api_url(api_url: str) -> str:
    if not isinstance(api_url, str) or not api_url:
        raise ValueError("api_url is required")
    if not (api_url.startswith("ws://") or api_url.startswith("wss://")):
        raise ValueError("api_url must be a WebSocket URL")
    return api_url


def build_foreground_cli_command(
    agent: str,
    workspace: str,
    api_url: str,
    token: Optional[str] = None,
    python_executable: Optional[str] = None,
) -> List[str]:
    """Build the argv for the known local foreground CLI host script.

    This intentionally accepts only structured fields, never an arbitrary
    command string, so callers cannot smuggle shell syntax into a launcher.
    """

    agent = _validate_agent(agent)
    api_url = _validate_api_url(api_url)
    if not isinstance(workspace, str) or not workspace:
        raise ValueError("workspace is required")

    executable = python_executable or sys.executable
    command = [
        executable,
        _script_path().as_posix(),
        "--agent",
        agent,
        "--workspace",
        str(Path(workspace).resolve()),
        "--api-url",
        api_url,
    ]
    return command


class ForegroundCliLauncher:
    """Launch the local agent CLI host in a foreground terminal process."""

    def __init__(
        self,
        api_url: str,
        token: Optional[str] = None,
        python_executable: Optional[str] = None,
        env: Optional[dict] = None,
    ):
        self.api_url = api_url
        self.token = token
        self.python_executable = python_executable
        self.env = env

    def launch(self, agent: str, workspace: str):
        resolved_workspace = str(Path(workspace).resolve())
        command = build_foreground_cli_command(
            agent=agent,
            workspace=resolved_workspace,
            api_url=self.api_url,
            token=self.token,
            python_executable=self.python_executable,
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in SAFE_ENV_KEYS
        }
        if self.env:
            env.update(self.env)
        if self.token is not None:
            env[LAUNCH_TOKEN_ENV] = self.token

        kwargs = {
            "cwd": resolved_workspace,
            "env": env,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        return subprocess.Popen(command, **kwargs)
