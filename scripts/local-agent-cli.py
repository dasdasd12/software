#!/usr/bin/env python
"""Local API terminal host for managed foreground agent sessions."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Optional


DEFAULT_API_URL = "ws://127.0.0.1:8765"
NATIVE_CLAUDE_PERMISSION_MODES = ("default", "plan")
CODEX_PROXY_CAPABILITY = "codex:hook"
CODEX_PROXY_LOG_ENV = "AI_KEYB_CODEX_PROXY_LOG"
CODEX_CONFIG_OVERRIDES_ENV = "AI_KEYB_CODEX_CONFIG_OVERRIDES"
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
CODEX_PROXY_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "execCommandApproval",
    "applyPatchApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
}
CODEX_TUI_SAFE_MESSAGE_BYTES = 900 * 1024
CODEX_OPTIONAL_LARGE_NOTIFICATION_METHODS = {
    "app/list/updated",
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
    parser.add_argument("--model", default="")
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--launch-id", default="")
    parser.add_argument("--native-cli", action="store_true")
    parser.add_argument("--permission-mode", choices=NATIVE_CLAUDE_PERMISSION_MODES, default="default")
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


def build_native_claude_command(
    settings_path: str,
    permission_mode: str = "default",
    context: str = "",
):
    if permission_mode not in NATIVE_CLAUDE_PERMISSION_MODES:
        raise ValueError("permission_mode must be one of: default, plan")
    executable = shutil.which("claude") or "claude"
    command = [
        executable,
        "--permission-mode",
        permission_mode,
        "--settings",
        settings_path,
        "--name",
        "AI Keyboard Claude",
    ]
    if context:
        command.append(context)
    return command


def build_native_codex_command(
    remote_url: str,
    workspace: str,
    context: str = "",
    model: str = "",
    reasoning_effort: str = "",
    config_overrides: Optional[list] = None,
):
    executable = shutil.which("codex.cmd") or shutil.which("codex") or "codex"
    command = [
        executable,
        "--no-alt-screen",
        "--remote",
        remote_url,
        "--cd",
        str(Path(workspace).resolve()),
        "--ask-for-approval",
        "untrusted",
        "--sandbox",
        "workspace-write",
    ]
    if model:
        command.extend(["--model", model])
    if reasoning_effort:
        command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
    for override in config_overrides or []:
        command.extend(["--config", str(override)])
    if context:
        command.append(context)
    return command


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


def _free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _start_pipe_printer(pipe, prefix: str, log_path: Optional[Path] = None) -> threading.Thread:
    def read_pipe() -> None:
        while True:
            line = pipe.readline()
            if not line:
                return
            print(f"{prefix}{line.rstrip()}", file=sys.stderr, flush=True)
            if log_path is not None:
                try:
                    with log_path.open("a", encoding="utf-8") as handle:
                        handle.write(str(line))
                except OSError:
                    pass

    thread = threading.Thread(target=read_pipe)
    thread.daemon = True
    thread.start()
    return thread


def _native_codex_env() -> Dict[str, str]:
    env = dict(os.environ)
    env.pop(LAUNCH_TOKEN_ENV, None)
    env.pop(CLAUDE_HOOK_TOKEN_ENV, None)
    env.pop(FOREGROUND_REGISTRATION_TOKEN_ENV, None)
    env.pop(FOREGROUND_EXIT_TOKEN_ENV, None)
    return env


def _native_codex_config_overrides() -> list:
    raw = os.environ.get(CODEX_CONFIG_OVERRIDES_ENV, "")
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item)]
    if isinstance(parsed, str) and parsed:
        return [parsed]
    return []


class CodexLocalApiBridge:
    def __init__(self, api_url: str, session_id: str, hook_token: str):
        self.api_url = api_url
        self.session_id = session_id
        self.hook_token = hook_token
        self._ws = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            await ws.close()

    async def send_notification(self, notification: Dict[str, Any]) -> None:
        async with self._lock:
            ws = await self._ensure_connected()
            await _send_json(ws, {
                "type": "codex_rpc_notification",
                "session_id": self.session_id,
                "notification": notification,
                "timestamp": now_ts(),
            })

    async def request_native_response(self, native_request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str(native_request.get("id"))
        async with self._lock:
            ws = await self._ensure_connected()
            await _send_json(ws, {
                "type": "codex_rpc_request",
                "session_id": self.session_id,
                "request": native_request,
                "timestamp": now_ts(),
            })
            while True:
                payload = _load_json(await ws.recv())
                if payload.get("type") == "codex_rpc_result" and payload.get("request_id") == request_id:
                    response = payload.get("native_response")
                    return response if isinstance(response, dict) else {
                        "id": native_request.get("id"),
                        "error": {"code": -32603, "message": "Local API returned an invalid Codex RPC response"},
                    }
                if payload.get("type") == "error":
                    return {
                        "id": native_request.get("id"),
                        "error": {
                            "code": -32603,
                            "message": payload.get("message") or payload.get("code") or "Local API Codex bridge failed",
                        },
                    }

    async def mark_delivered(self, request_id: str, response_written: bool, error: str = "") -> None:
        async with self._lock:
            ws = await self._ensure_connected()
            payload: Dict[str, Any] = {
                "type": "codex_rpc_delivered",
                "session_id": self.session_id,
                "request_id": request_id,
                "response_written": bool(response_written),
                "timestamp": now_ts(),
            }
            if error:
                payload["error"] = error
            await _send_json(ws, payload)

    async def _ensure_connected(self):
        if self._ws is not None:
            return self._ws
        import websockets

        self._ws = await websockets.connect(self.api_url)
        await _send_json(self._ws, {
            "type": "hello",
            "token": self.hook_token or None,
            "client_kind": "agent-hook",
            "client_id": f"codex-cli-proxy:{self.session_id}",
            "capabilities": [CODEX_PROXY_CAPABILITY],
            "timestamp": now_ts(),
        })
        while True:
            payload = _load_json(await self._ws.recv())
            if payload.get("type") == "hello_ack":
                return self._ws
            if payload.get("type") == "error":
                raise RuntimeError(payload.get("message") or payload.get("code") or "Codex proxy auth failed")


class CodexNativeProxy:
    def __init__(
        self,
        api_url: str,
        session_id: str,
        hook_token: str,
        workspace: str,
        initial_context: str = "",
        model: str = "",
        reasoning_effort: str = "",
    ):
        self.api_url = api_url
        self.session_id = session_id
        self.hook_token = hook_token
        self.workspace = str(Path(workspace).resolve())
        self.initial_context = initial_context
        self.model = model
        self.reasoning_effort = reasoning_effort
        self._initial_turn_sent = False
        self.backend_uri = ""
        self.proxy_uri = ""
        self.backend_process = None
        self.proxy_server = None
        self.local_api = CodexLocalApiBridge(api_url, session_id, hook_token)
        self.rpc_log_path = None
        self.stderr_log_path = None
        self.tui_stderr_log_path = None
        if os.environ.get(CODEX_PROXY_LOG_ENV):
            safe_session_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in session_id)
            temp_dir = Path(tempfile.gettempdir())
            self.rpc_log_path = temp_dir / f"ai-keyb-codex-proxy-{safe_session_id}.jsonl"
            self.stderr_log_path = temp_dir / f"ai-keyb-codex-app-server-{safe_session_id}.log"
            self.tui_stderr_log_path = temp_dir / f"ai-keyb-codex-tui-{safe_session_id}.log"

    async def start(self) -> str:
        import websockets

        backend_port = _free_tcp_port()
        self.backend_uri = f"ws://127.0.0.1:{backend_port}"
        executable = shutil.which("codex.cmd") or shutil.which("codex") or "codex"
        self.backend_process = subprocess.Popen(
            [executable, "app-server", "--listen", self.backend_uri],
            cwd=self.workspace,
            env=_native_codex_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE if self.stderr_log_path is not None else subprocess.DEVNULL,
            text=True,
        )
        if self.backend_process.stderr is not None:
            _start_pipe_printer(self.backend_process.stderr, "[codex app-server] ", self.stderr_log_path)

        proxy_port = _free_tcp_port()
        self.proxy_uri = f"ws://127.0.0.1:{proxy_port}"
        self.proxy_server = await websockets.serve(
            self._handle_tui_client,
            "127.0.0.1",
            proxy_port,
            max_size=None,
        )
        if self.rpc_log_path is not None or self.stderr_log_path is not None:
            print(
                f"Codex proxy diagnostics: rpc={self.rpc_log_path} stderr={self.stderr_log_path} tui={self.tui_stderr_log_path}",
                file=sys.stderr,
                flush=True,
            )
        return self.proxy_uri

    async def stop(self) -> None:
        if self.proxy_server is not None:
            self.proxy_server.close()
            await self.proxy_server.wait_closed()
            self.proxy_server = None
        await self.local_api.close()
        process = self.backend_process
        self.backend_process = None
        if process is None:
            return
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    async def _handle_tui_client(self, client_ws) -> None:
        import websockets

        backend_ws = await self._connect_backend(websockets)
        tasks = [
            asyncio.create_task(self._pipe_client_to_backend(client_ws, backend_ws)),
            asyncio.create_task(self._pipe_backend_to_client(backend_ws, client_ws)),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception as exc:
            self._log_proxy_status("tui_proxy_pipe_error", error=str(exc))
            raise
        finally:
            self._log_proxy_status("tui_proxy_closing")
            await asyncio.gather(
                backend_ws.close(),
                client_ws.close(),
                return_exceptions=True,
            )

    async def _connect_backend(self, websockets_module):
        last_exc = None
        for _ in range(80):
            try:
                return await websockets_module.connect(self.backend_uri, max_size=None)
            except Exception as exc:
                last_exc = exc
                await asyncio.sleep(0.1)
        raise RuntimeError(f"Codex app-server did not accept WebSocket connection: {last_exc}")

    async def _pipe_client_to_backend(self, client_ws, backend_ws) -> None:
        try:
            async for raw in client_ws:
                self._log_rpc("client_to_backend", raw)
                await backend_ws.send(raw)
        except Exception as exc:
            self._log_proxy_status("client_to_backend_error", error=str(exc))
            raise
        finally:
            self._log_proxy_status("client_to_backend_closed")

    async def _pipe_backend_to_client(self, backend_ws, client_ws) -> None:
        try:
            async for raw in backend_ws:
                self._log_rpc("backend_to_client", raw)
                try:
                    message = json.loads(raw)
                except Exception:
                    await client_ws.send(raw)
                    continue
                if not isinstance(message, dict):
                    await client_ws.send(raw)
                    continue
                method = message.get("method")
                if isinstance(method, str) and "id" not in message:
                    if self._should_suppress_backend_message_for_tui(message, raw):
                        continue
                    await self.local_api.send_notification(message)
                    await client_ws.send(raw)
                    continue
                if isinstance(method, str) and "id" in message and method in CODEX_PROXY_METHODS:
                    response_written = False
                    error = ""
                    request_id = str(message.get("id"))
                    try:
                        native_response = await self.local_api.request_native_response(message)
                        self._log_rpc("proxy_to_backend_response", native_response)
                        await backend_ws.send(json.dumps(native_response, ensure_ascii=False))
                        response_written = True
                    except Exception as exc:
                        error = str(exc)
                        fallback = {
                            "id": message.get("id"),
                            "error": {"code": -32603, "message": f"Local API Codex proxy failed: {exc}"},
                        }
                        self._log_rpc("proxy_to_backend_response", fallback)
                        await backend_ws.send(json.dumps(fallback, ensure_ascii=False))
                        response_written = True
                    finally:
                        await self.local_api.mark_delivered(request_id, response_written, error)
                    continue
                await client_ws.send(raw)
                await self._maybe_start_initial_turn(message, backend_ws)
        except Exception as exc:
            self._log_proxy_status("backend_to_client_error", error=str(exc))
            raise
        finally:
            self._log_proxy_status("backend_to_client_closed")

    async def _maybe_start_initial_turn(self, message: Dict[str, Any], backend_ws) -> None:
        if self._initial_turn_sent or not self.initial_context:
            return
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            return
        self._initial_turn_sent = True
        request = self._initial_turn_request(thread_id)
        self._log_rpc("proxy_to_backend_initial_turn", request)
        await backend_ws.send(json.dumps(request, ensure_ascii=False))

    def _initial_turn_request(self, thread_id: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": self.initial_context, "text_elements": []}],
            "cwd": self.workspace,
            "approvalPolicy": "untrusted",
            "approvalsReviewer": "user",
        }
        if self.model:
            params["model"] = self.model
        if self.reasoning_effort:
            params["effort"] = self.reasoning_effort
        return {
            "id": f"ai-keyb-initial-turn-{uuid.uuid4().hex}",
            "method": "turn/start",
            "params": params,
        }

    def _log_rpc(self, direction: str, raw: Any) -> None:
        if self.rpc_log_path is None:
            return
        try:
            if isinstance(raw, str):
                payload = json.loads(raw)
            else:
                payload = raw
            record = {
                "direction": direction,
                "payload": payload,
                "timestamp": now_ts(),
            }
            with self.rpc_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _log_proxy_status(self, status: str, error: str = "") -> None:
        payload: Dict[str, Any] = {"status": status}
        if error:
            payload["error"] = error
        self._log_rpc("proxy_status", payload)

    def _should_suppress_backend_message_for_tui(self, message: Dict[str, Any], raw: str) -> bool:
        method = message.get("method")
        if method not in CODEX_OPTIONAL_LARGE_NOTIFICATION_METHODS:
            return False
        size_bytes = len(raw.encode("utf-8"))
        if size_bytes <= CODEX_TUI_SAFE_MESSAGE_BYTES:
            return False
        self._log_rpc(
            "proxy_suppressed_backend_to_client",
            {
                "method": method,
                "size_bytes": size_bytes,
                "reason": "optional notification exceeds Codex TUI WebSocket receive limit",
            },
        )
        return True


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
        if args.native_cli and args.agent == "codex":
            return await _run_native_codex_cli(ws, args, state)
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
    command = build_native_claude_command(
        settings_path,
        permission_mode=args.permission_mode,
        context=args.context,
    )
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


async def _run_native_codex_cli(ws, args, state: Dict[str, Any]) -> int:
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

    proxy = CodexNativeProxy(
        args.api_url,
        state["session_id"],
        args.hook_token,
        args.workspace,
        initial_context=args.context,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    remote_url = await proxy.start()
    command = build_native_codex_command(
        remote_url,
        args.workspace,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        config_overrides=_native_codex_config_overrides(),
    )
    popen_kwargs = {
        "cwd": args.workspace,
        "env": _native_codex_env(),
    }
    tui_stderr_log_path = getattr(proxy, "tui_stderr_log_path", None)
    if tui_stderr_log_path is not None:
        popen_kwargs.update({
            "stderr": subprocess.PIPE,
            "text": True,
        })
    process = subprocess.Popen(command, **popen_kwargs)
    if tui_stderr_log_path is not None and process.stderr is not None:
        _start_pipe_printer(process.stderr, "[codex tui] ", tui_stderr_log_path)
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
        await proxy.stop()


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
