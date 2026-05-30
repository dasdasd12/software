"""Foreground CLI launcher helpers for managed local agent sessions."""

import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional


VALID_AGENTS = {"claude", "codex"}
VALID_NATIVE_CLAUDE_PERMISSION_MODES = {"default", "plan"}
LAUNCH_TOKEN_ENV = "AI_KEYB_LAUNCH_TOKEN"
CLAUDE_HOOK_TOKEN_ENV = "AI_KEYB_CLAUDE_HOOK_TOKEN"
FOREGROUND_REGISTRATION_TOKEN_ENV = "AI_KEYB_FOREGROUND_REGISTRATION_TOKEN"
FOREGROUND_EXIT_TOKEN_ENV = "AI_KEYB_FOREGROUND_EXIT_TOKEN"
SENSITIVE_ENV_KEY_MARKERS = (
    "API_KEY",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "BEARER_TOKEN",
    "ID_TOKEN",
    "TOKEN",
    "APIKEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "PASSWORD",
    "PASSWD",
    "SECRET",
    "CREDENTIAL",
    "AUTHORIZATION",
)


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


def _validate_permission_mode(permission_mode: str) -> str:
    if permission_mode not in VALID_NATIVE_CLAUDE_PERMISSION_MODES:
        raise ValueError("permission_mode must be one of: default, plan")
    return permission_mode


def build_foreground_cli_command(
    agent: str,
    workspace: str,
    api_url: str,
    token: Optional[str] = None,
    foreground_launch_id: Optional[str] = None,
    native_cli: bool = False,
    permission_mode: str = "default",
    python_executable: Optional[str] = None,
) -> List[str]:
    """Build the argv for the known local foreground CLI host script.

    This intentionally accepts only structured fields, never an arbitrary
    command string, so callers cannot smuggle shell syntax into a launcher.
    """

    agent = _validate_agent(agent)
    api_url = _validate_api_url(api_url)
    permission_mode = _validate_permission_mode(permission_mode)
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
    if foreground_launch_id:
        command += ["--launch-id", str(foreground_launch_id)]
    if native_cli:
        command.append("--native-cli")
        command += ["--permission-mode", permission_mode]
    return command


def build_foreground_cli_env(
    base_env: Optional[dict] = None,
    extra_env: Optional[dict] = None,
    token: Optional[str] = None,
    hook_token: Optional[str] = None,
    registration_token: Optional[str] = None,
    exit_token: Optional[str] = None,
) -> dict:
    """Build a terminal-friendly environment without provider/API secrets."""
    source_env = os.environ if base_env is None else base_env
    env = {
        key: value
        for key, value in source_env.items()
        if not _is_sensitive_env_key(key)
    }
    if extra_env:
        for key, value in extra_env.items():
            if _is_sensitive_env_key(key):
                continue
            env[key] = value
    if token is not None:
        env[LAUNCH_TOKEN_ENV] = token
    if hook_token is not None:
        env[CLAUDE_HOOK_TOKEN_ENV] = hook_token
    if registration_token is not None:
        env[FOREGROUND_REGISTRATION_TOKEN_ENV] = registration_token
    if exit_token is not None:
        env[FOREGROUND_EXIT_TOKEN_ENV] = exit_token
    return env


def _is_sensitive_env_key(key: str) -> bool:
    normalized = str(key).upper()
    if normalized in {
        LAUNCH_TOKEN_ENV,
        CLAUDE_HOOK_TOKEN_ENV,
        FOREGROUND_REGISTRATION_TOKEN_ENV,
        FOREGROUND_EXIT_TOKEN_ENV,
    }:
        return True
    return any(marker in normalized for marker in SENSITIVE_ENV_KEY_MARKERS)


class ForegroundCliLauncher:
    """Launch the local agent CLI host in a foreground terminal process."""

    def __init__(
        self,
        api_url: str,
        token: Optional[str] = None,
        hook_token: Optional[str] = None,
        python_executable: Optional[str] = None,
        env: Optional[dict] = None,
    ):
        self.api_url = api_url
        self.token = token
        self.hook_token = hook_token
        self.python_executable = python_executable
        self.env = env

    def launch(
        self,
        agent: str,
        workspace: str,
        foreground_launch_id: Optional[str] = None,
        native_cli: bool = False,
        permission_mode: str = "default",
        registration_token: Optional[str] = None,
        hook_token: Optional[str] = None,
        exit_token: Optional[str] = None,
    ):
        resolved_workspace = str(Path(workspace).resolve())
        command = build_foreground_cli_command(
            agent=agent,
            workspace=resolved_workspace,
            api_url=self.api_url,
            token=self.token,
            foreground_launch_id=foreground_launch_id,
            native_cli=native_cli,
            permission_mode=permission_mode,
            python_executable=self.python_executable,
        )
        env = build_foreground_cli_env(
            extra_env=self.env,
            token=self.token,
            hook_token=hook_token or self.hook_token,
            registration_token=registration_token,
            exit_token=exit_token,
        )

        kwargs = {
            "cwd": resolved_workspace,
            "env": env,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            kwargs["close_fds"] = True
            return subprocess.Popen(command, **kwargs)
        if hasattr(os, "setsid"):
            kwargs["preexec_fn"] = os.setsid
        return subprocess.Popen(command, **kwargs)
