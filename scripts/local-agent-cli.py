#!/usr/bin/env python
"""Local API terminal host for managed foreground agent sessions."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Optional


DEFAULT_API_URL = "ws://127.0.0.1:8765"
LAUNCH_TOKEN_ENV = "AI_KEYB_LAUNCH_TOKEN"
CLAUDE_HOOK_TOKEN_ENV = "AI_KEYB_CLAUDE_HOOK_TOKEN"
FOREGROUND_REGISTRATION_TOKEN_ENV = "AI_KEYB_FOREGROUND_REGISTRATION_TOKEN"
FOREGROUND_EXIT_TOKEN_ENV = "AI_KEYB_FOREGROUND_EXIT_TOKEN"
NATIVE_CLAUDE_SECRET_ENV_VARS = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
}
NATIVE_CLAUDE_SESSION_ENV_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
)
DEFAULT_CAPABILITIES = ["agent:launch", "permission:respond", "session:list"]
PRINT_EVENT_TYPES = {
    "agent_message_delta",
    "task_update",
    "permission_request",
    "permission_ack",
    "task_completed",
    "task_failed",
}


def now_ts() -> float:
    return time.time()


def parse_args(argv, env=None):
    parser = argparse.ArgumentParser(description="Run a managed Local API agent session in a terminal.")
    parser.add_argument("--agent", choices=["claude", "codex"], required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--client-kind", default="desktop-ui")
    parser.add_argument("--client-id", default="local-agent-cli")
    parser.add_argument("--context", default="")
    parser.add_argument("--launch-id", default="")
    parser.add_argument("--native-cli", action="store_true")
    args = parser.parse_args(argv)
    args.token = (env or os.environ).get(LAUNCH_TOKEN_ENV, "")
    args.hook_token = (env or os.environ).get(CLAUDE_HOOK_TOKEN_ENV, "")
    args.registration_token = (env or os.environ).get(FOREGROUND_REGISTRATION_TOKEN_ENV, "")
    args.exit_token = (env or os.environ).get(FOREGROUND_EXIT_TOKEN_ENV, "")
    return args


def _command(command_type: str, payload: Optional[Dict[str, Any]] = None, target: Optional[Dict[str, Any]] = None):
    command = {
        "command_id": "cli_%s" % uuid.uuid4().hex,
        "type": command_type,
        "source": {"kind": "desktop-ui", "client_id": "local-agent-cli"},
        "payload": payload or {},
    }
    if target is not None:
        command["target"] = target
    return {"type": "command", "command": command, "timestamp": now_ts()}


def build_hello_message(client_kind: str = "desktop-ui", token: str = "", client_id: str = "local-agent-cli"):
    return {
        "type": "hello",
        "token": token or None,
        "client_kind": client_kind,
        "client_id": client_id,
        "capabilities": list(DEFAULT_CAPABILITIES),
        "timestamp": now_ts(),
    }


def build_launch_command(
    agent: str,
    workspace: str,
    context: str = "",
    foreground_launch_id: str = "",
    control_mode: str = "managed_native",
):
    payload = {
        "agent": agent,
        "workspace": workspace,
        "context": context,
        "launch_surface": "foreground_cli",
        "control_mode": control_mode,
        "frontend_pid": os.getpid(),
    }
    if foreground_launch_id:
        payload["foreground_launch_id"] = foreground_launch_id
    return _command(
        "agent.session.launch_or_resume",
        payload=payload,
        target={"session_id": "new"},
    )


def build_register_foreground_command(
    agent: str,
    workspace: str,
    foreground_launch_id: str = "",
    registration_token: str = "",
):
    payload = {
        "agent": agent,
        "workspace": workspace,
        "launch_surface": "foreground_cli",
        "control_mode": "native_cli",
        "frontend_pid": os.getpid(),
    }
    if foreground_launch_id:
        payload["foreground_launch_id"] = foreground_launch_id
    if registration_token:
        payload["foreground_registration_token"] = registration_token
    return _command(
        "agent.session.register_foreground",
        payload=payload,
        target={"session_id": "new"},
    )


def build_input_command(session_id: str, text: str):
    return _command(
        "agent.session.input",
        payload={"text": text},
        target={"session_id": session_id},
    )


def build_permission_response(session_id: str, request_id: str, approved: bool):
    return {
        "type": "permission_response",
        "session_id": session_id,
        "request_id": request_id,
        "approved": bool(approved),
        "timestamp": now_ts(),
    }


def build_interrupt_command(session_id: str):
    return _command("agent.run.interrupt", target={"session_id": session_id})


def build_close_command(session_id: str):
    return _command("agent.session.close", target={"session_id": session_id})


def build_foreground_exited_command(session_id: str, exit_code: int, exit_token: str = ""):
    payload = {"exit_code": int(exit_code)}
    if exit_token:
        payload["foreground_exit_token"] = exit_token
    return _command(
        "agent.session.foreground_exited",
        payload=payload,
        target={"session_id": session_id},
    )


async def _send_json(ws, payload: Dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, ensure_ascii=False))


def _load_json(raw: str) -> Dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Local API message was not an object")
    return payload


def _event_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("type") == "event":
        event = payload.get("event")
        if isinstance(event, dict):
            return event
    return payload


def _extract_session_id(payload: Dict[str, Any]) -> Optional[str]:
    event = _event_payload(payload)
    for container in (event.get("payload"), event):
        if isinstance(container, dict):
            session_id = container.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
    return None


def _extract_permission(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    event = _event_payload(payload)
    event_type = event.get("type")
    if event_type != "permission_request":
        return None
    session_id = event.get("session_id")
    request_id = event.get("request_id")
    if isinstance(session_id, str) and isinstance(request_id, str):
        return {"session_id": session_id, "request_id": request_id}
    return None


def _print_relevant(payload: Dict[str, Any]) -> None:
    event = _event_payload(payload)
    event_type = event.get("type")
    if event_type in PRINT_EVENT_TYPES:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    elif payload.get("type") == "error":
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def _start_stdin_reader(queue: asyncio.Queue, stdin=None) -> threading.Thread:
    loop = asyncio.get_event_loop()
    input_stream = stdin or sys.stdin

    def read_stdin() -> None:
        while True:
            line = input_stream.readline()
            if line == "":
                loop.call_soon_threadsafe(queue.put_nowait, "/exit")
                return
            loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\r\n"))

    thread = threading.Thread(target=read_stdin)
    thread.daemon = True
    thread.start()
    return thread


def build_claude_hook_settings(
    hook_python: str,
    hook_script: str,
    api_url: str,
    session_id: str,
    timeout: int = 600,
) -> Dict[str, Any]:
    handler = {
        "type": "command",
        "command": hook_python,
        "args": [
            hook_script,
            "--api-url",
            api_url,
            "--session-id",
            session_id,
            "--client-kind",
            "agent-hook",
            "--client-id",
            "claude-code-hook:%s" % session_id,
            "--timeout",
            str(timeout),
        ],
        "timeout": timeout,
    }
    async_handler = dict(handler)
    async_handler["async"] = True
    async_handler["timeout"] = 30
    settings_env = {"ANTHROPIC_AUTH_TOKEN": ""}
    for key in NATIVE_CLAUDE_SESSION_ENV_VARS:
        value = os.environ.get(key)
        if value:
            settings_env[key] = value
    return {
        "env": settings_env,
        "hooks": {
            "PermissionRequest": [{"matcher": "*", "hooks": [handler]}],
            "PreToolUse": [{"matcher": "AskUserQuestion|ExitPlanMode", "hooks": [handler]}],
            "UserPromptSubmit": [{"hooks": [async_handler]}],
            "MessageDisplay": [{"hooks": [async_handler]}],
            "PostToolUse": [{"matcher": "*", "hooks": [async_handler]}],
            "PostToolUseFailure": [{"matcher": "*", "hooks": [async_handler]}],
            "Stop": [{"hooks": [async_handler]}],
            "SessionEnd": [{"hooks": [async_handler]}],
        }
    }


def write_claude_hook_settings(
    api_url: str,
    session_id: str,
    directory: str = None,
) -> str:
    root = Path(__file__).resolve().parents[1]
    hook_script = root / "scripts" / "claude-code-hook.py"
    settings = build_claude_hook_settings(
        str(Path(sys.executable).resolve()),
        str(hook_script),
        api_url,
        session_id,
    )
    settings_dir = Path(directory or tempfile.gettempdir())
    settings_dir.mkdir(parents=True, exist_ok=True)
    path = settings_dir / ("ai-keyb-claude-hooks-%s.json" % session_id)
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    return str(path)


def build_native_claude_command(settings_path: str):
    executable = shutil.which("claude") or "claude"
    return [
        executable,
        "--permission-mode",
        "default",
        "--settings",
        settings_path,
        "--name",
        "AI Keyboard Claude",
    ]


async def _receiver(ws, state: Dict[str, Any], stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        raw = await ws.recv()
        payload = _load_json(raw)
        session_id = _extract_session_id(payload)
        if session_id:
            state["session_id"] = session_id
        permission = _extract_permission(payload)
        if permission:
            state["pending_permission"] = permission
        _print_relevant(payload)


async def _drain_receiver(ws, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            raw = await ws.recv()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not stop_event.is_set():
                print(f"Local API control connection closed: {exc}", file=sys.stderr, flush=True)
            return
        try:
            payload = _load_json(raw)
        except Exception as exc:
            print(f"Ignoring invalid Local API message while draining: {exc}", file=sys.stderr, flush=True)
            continue
        if payload.get("type") == "error":
            print(json.dumps(payload, ensure_ascii=False), flush=True)


async def _sender(ws, state: Dict[str, Any], lines: asyncio.Queue, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        line = await lines.get()
        if not line:
            continue
        session_id = state.get("session_id")
        if line == "/exit":
            if session_id:
                await _send_json(ws, build_close_command(session_id))
            stop_event.set()
            return
        if line == "/approve" or line == "/deny":
            pending = state.get("pending_permission")
            if not pending:
                print("No pending permission.", flush=True)
                continue
            await _send_json(
                ws,
                build_permission_response(
                    pending["session_id"],
                    pending["request_id"],
                    line == "/approve",
                ),
            )
            state["pending_permission"] = None
            continue
        if not session_id:
            print("No active session yet.", flush=True)
            continue
        if line == "/interrupt":
            await _send_json(ws, build_interrupt_command(session_id))
        elif line == "/close":
            await _send_json(ws, build_close_command(session_id))
        else:
            await _send_json(ws, build_input_command(session_id, line))


async def run_cli(args) -> int:
    try:
        import websockets
    except ImportError:
        print("websockets is required to connect to the Local API.", file=sys.stderr)
        return 2

    state = {"session_id": None, "pending_permission": None}
    lines = asyncio.Queue()
    stop_event = asyncio.Event()
    async with websockets.connect(args.api_url) as ws:
        await _send_json(ws, build_hello_message(args.client_kind, args.token, args.client_id))
        if args.native_cli and args.agent == "claude":
            return await _run_native_claude_cli(ws, args, state)
        await _send_json(ws, build_launch_command(
            args.agent,
            args.workspace,
            args.context,
            args.launch_id,
        ))
        _start_stdin_reader(lines)
        receiver_task = asyncio.create_task(_receiver(ws, state, stop_event))
        sender_task = asyncio.create_task(_sender(ws, state, lines, stop_event))
        done, pending = await asyncio.wait(
            [receiver_task, sender_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_event.set()
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc
    return 0


async def _run_native_claude_cli(ws, args, state: Dict[str, Any]) -> int:
    await _send_json(ws, build_register_foreground_command(
        args.agent,
        args.workspace,
        args.launch_id,
        args.registration_token,
    ))

    while True:
        payload = _load_json(await ws.recv())
        session_id = _extract_session_id(payload)
        if session_id:
            state["session_id"] = session_id
        event = _event_payload(payload)
        if event.get("type") == "agent.session.created" and state.get("session_id"):
            break
        if payload.get("type") == "error":
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            return 1

    settings_path = write_claude_hook_settings(args.api_url, state["session_id"])
    command = build_native_claude_command(settings_path)
    process = subprocess.Popen(
        command,
        cwd=args.workspace,
        env=_native_claude_env(args),
    )
    drain_stop = asyncio.Event()
    drain_task = asyncio.create_task(_drain_receiver(ws, drain_stop))
    loop = asyncio.get_event_loop()
    try:
        exit_code = await loop.run_in_executor(None, process.wait)
        drain_stop.set()
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await _send_json(ws, build_foreground_exited_command(state["session_id"], exit_code, args.exit_token))
        return exit_code
    finally:
        drain_stop.set()
        drain_task.cancel()
        try:
            Path(settings_path).unlink()
        except OSError:
            pass


def _native_claude_env(args) -> Dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in NATIVE_CLAUDE_SECRET_ENV_VARS
    }
    env.pop(LAUNCH_TOKEN_ENV, None)
    env.pop(FOREGROUND_REGISTRATION_TOKEN_ENV, None)
    env.pop(FOREGROUND_EXIT_TOKEN_ENV, None)
    if args.hook_token:
        env[CLAUDE_HOOK_TOKEN_ENV] = args.hook_token
    else:
        env.pop(CLAUDE_HOOK_TOKEN_ENV, None)
    return env


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
