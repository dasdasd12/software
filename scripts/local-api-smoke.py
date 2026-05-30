#!/usr/bin/env python3
"""
Smoke client for the Local Core Service MVP WebSocket API.

This is a local UI/test/automation API client. It is not the firmware device
transport and should not be used as the keyboard protocol contract.

Examples:
    python scripts/local-api-smoke.py --scenario basic
    python scripts/local-api-smoke.py --scenario real-agent --agent codex --context "say hello"
    python scripts/local-api-smoke.py --scenario approval-real --agent claude --context "use a tool"
    python scripts/local-api-smoke.py --scenario permission --request-id req_1 --approved true
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import websockets


TERMINAL_TYPES = {"task_completed", "task_failed", "error"}
DEFAULT_CONTEXT = "say hello"
DEFAULT_CLAUDE_APPROVAL_CONTEXT = (
    "Run this exact harmless command and report its output: "
    "python -c \"print('claude approval smoke')\""
)
DEFAULT_CODEX_APPROVAL_CONTEXT = (
    "Run this exact harmless command and report its output: "
    "python -c \"print('codex approval smoke')\""
)
VIRTUAL_DEVICE_ID = "kbd_virtual_smoke"
ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = "src/bridge/config.yaml"


def now_ts() -> int:
    return int(time.time())


def parse_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "y", "approve", "approved"}


def build_service_start_command(config: Any, workspace: Any = None) -> List[str]:
    config_path = Path(config)
    if not config_path.is_absolute():
        config_path = ROOT_DIR / config_path
    command = [
        sys.executable,
        str(ROOT_DIR / "src" / "bridge" / "server.py"),
        "--config",
        str(config_path),
    ]
    if workspace:
        command.extend(["--workspace", str(workspace)])
    return command


def start_local_core_service(config: Any, workspace: Any = None) -> subprocess.Popen:
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(build_service_start_command(config, workspace), **kwargs)


class LocalApiSmokeClient:
    def __init__(
        self,
        url: str,
        timeout: float,
        json_log: bool,
        token: str,
        client_kind: str,
        client_id: str,
        capabilities: List[str],
        workspace: str = None,
    ):
        self.url = url
        self.timeout = timeout
        self.json_log = json_log
        self.token = token
        self.client_kind = client_kind
        self.client_id = client_id
        self.capabilities = capabilities
        self.workspace = workspace

    def log(self, direction: str, payload: Dict[str, Any]) -> None:
        record = {
            "direction": direction,
            "payload": payload,
            "timestamp": now_ts(),
        }
        if self.json_log:
            print(json.dumps(record, ensure_ascii=False))
            return

        msg_type = payload.get("type", "unknown")
        print(f"{direction} {msg_type}: {json.dumps(payload, ensure_ascii=False)}")

    async def send(self, ws, payload: Dict[str, Any]) -> None:
        self.log("SEND", payload)
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def recv_json(self, ws) -> Dict[str, Any]:
        return await self.recv_json_with_timeout(ws, self.timeout)

    async def recv_json_with_timeout(self, ws, timeout: float) -> Dict[str, Any]:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        payload = json.loads(raw)
        self.log("RECV", payload)
        return payload

    @staticmethod
    def _payload_summary(payload: Dict[str, Any]) -> str:
        if not payload:
            return "none"
        summary = {}
        for key in ("type", "session_id", "request_id", "agent", "state", "code", "message"):
            value = payload.get(key)
            if isinstance(value, str) and len(value) > 160:
                value = value[:160] + "...(truncated)"
            if isinstance(value, (str, int, float, bool)) or value is None:
                summary[key] = value
        return json.dumps(summary, ensure_ascii=False)

    def _approval_timeout_error(
        self,
        stage: str,
        session_id: str,
        last_payload: Dict[str, Any],
    ) -> RuntimeError:
        session_text = session_id or "<unknown>"
        last_type = last_payload.get("type") if last_payload else "none"
        return RuntimeError(
            f"Timed out waiting for {stage}; "
            f"session_id={session_text}; "
            f"last_payload_type={last_type}; "
            f"last_payload={self._payload_summary(last_payload)}"
        )

    async def wait_for_type(self, ws, expected_type: str) -> Dict[str, Any]:
        while True:
            payload = await self.recv_json(ws)
            if payload.get("type") == "error":
                raise RuntimeError(f"Local API error while waiting for {expected_type}: {payload}")
            if payload.get("type") == expected_type:
                return payload

    async def wait_for_type_for_session(
        self,
        ws,
        expected_type: str,
        session_id: str,
    ) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        last_payload = None

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                last_type = last_payload.get("type") if last_payload else None
                raise TimeoutError(
                    f"Timed out waiting for {expected_type}; "
                    f"session_id={session_id}; "
                    f"last_payload_type={last_type}"
                )
            try:
                payload = await self.recv_json_with_timeout(ws, remaining)
            except asyncio.TimeoutError:
                last_type = last_payload.get("type") if last_payload else None
                raise TimeoutError(
                    f"Timed out waiting for {expected_type}; "
                    f"session_id={session_id}; "
                    f"last_payload_type={last_type}"
                )
            last_payload = payload
            if payload.get("type") == "error":
                raise RuntimeError(f"Local API error while waiting for {expected_type}: {payload}")
            if payload.get("session_id") == session_id and payload.get("type") in {"task_failed", "error"}:
                raise RuntimeError(f"Session failed while waiting for {expected_type}: {payload}")
            if payload.get("type") == expected_type and payload.get("session_id") == session_id:
                return payload

    async def hello(self, ws) -> None:
        await self.send(ws, {
            "type": "hello",
            "token": self.token or None,
            "client_kind": self.client_kind,
            "client_id": self.client_id,
            "capabilities": self.capabilities,
            "timestamp": now_ts(),
        })
        await self.wait_for_type(ws, "hello_ack")

    def agent_launch_payload(self, agent: str, context: str) -> Dict[str, Any]:
        payload = {
            "type": "agent_launch",
            "agent": agent,
            "session_id": "new",
            "context": context,
            "timestamp": now_ts(),
        }
        if self.workspace:
            payload["workspace"] = self.workspace
        return payload

    def foreground_cli_command_payload(self, agent: str) -> Dict[str, Any]:
        payload = {"agent": agent}
        if self.workspace:
            payload["workspace"] = self.workspace
        return {
            "type": "command",
            "command": {
                "command_id": "cmd_smoke_foreground_cli",
                "type": "agent.cli.launch_foreground",
                "source": {"kind": self.client_kind, "client_id": self.client_id},
                "payload": payload,
            },
            "timestamp": now_ts(),
        }

    def session_close_command_payload(self, session_id: str) -> Dict[str, Any]:
        return {
            "type": "command",
            "command": {
                "command_id": "cmd_smoke_close_%s" % session_id,
                "type": "agent.session.close",
                "source": {"kind": self.client_kind, "client_id": self.client_id},
                "target": {"session_id": session_id},
                "payload": {},
            },
            "timestamp": now_ts(),
        }

    async def run_basic(self) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, {"type": "list_sessions", "agent": "all", "timestamp": now_ts()})
            await self.wait_for_type(ws, "session_list")
            await self.send(ws, {"type": "heartbeat", "timestamp": now_ts()})

    async def run_permission(self, request_id: str, approved: bool) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, {
                "type": "permission_response",
                "request_id": request_id,
                "approved": approved,
                "timestamp": now_ts(),
            })
            payload = await self.recv_json(ws)
            if payload.get("type") not in {"permission_ack", "error"}:
                raise RuntimeError(f"Unexpected permission response: {payload}")

    async def run_real_agent(self, agent: str, context: str) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, self.agent_launch_payload(agent, context))
            while True:
                payload = await self.recv_json(ws)
                if payload.get("type") in TERMINAL_TYPES:
                    if payload.get("type") in {"task_failed", "error"}:
                        raise RuntimeError(f"Real-agent scenario ended with failure: {payload}")
                    return

    async def run_foreground_cli(self, agent: str) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, self.foreground_cli_command_payload(agent))
            launched = None
            created = None
            last_payload = None
            deadline = time.monotonic() + self.timeout

            while launched is None or created is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(
                        "Timed out waiting for foreground CLI launch and managed session; "
                        f"launched={launched is not None}; "
                        f"created={created is not None}; "
                        f"last_payload={self._payload_summary(last_payload)}"
                    )
                try:
                    payload = await self.recv_json_with_timeout(ws, remaining)
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        "Timed out waiting for foreground CLI launch and managed session; "
                        f"launched={launched is not None}; "
                        f"created={created is not None}; "
                        f"last_payload={self._payload_summary(last_payload)}"
                    )
                last_payload = payload
                if payload.get("type") == "error":
                    raise RuntimeError(f"Local API error while waiting for foreground CLI: {payload}")
                if payload.get("type") != "event":
                    continue

                event = payload.get("event") or {}
                event_payload = event.get("payload") or {}
                if event.get("type") == "agent.cli.launched":
                    if event_payload.get("agent") != agent:
                        raise RuntimeError(f"Foreground CLI launch returned wrong agent: {payload}")
                    if not event_payload.get("frontend_pid"):
                        raise RuntimeError(f"Foreground CLI launch did not include frontend_pid: {payload}")
                    if not event_payload.get("foreground_launch_id"):
                        raise RuntimeError(f"Foreground CLI launch did not include foreground_launch_id: {payload}")
                    if event_payload.get("launch_surface") != "foreground_cli":
                        raise RuntimeError(f"Foreground CLI launch returned wrong launch_surface: {payload}")
                    launched = event_payload
                elif event.get("type") == "agent.session.created":
                    if event_payload.get("agent") != agent:
                        continue
                    if event_payload.get("launch_surface") != "foreground_cli":
                        continue
                    if launched is None:
                        continue
                    if event_payload.get("foreground_launch_id") != launched.get("foreground_launch_id"):
                        continue
                    launched_workspace = launched.get("workspace")
                    created_workspace = event_payload.get("workspace")
                    if launched_workspace and created_workspace and created_workspace != launched_workspace:
                        raise RuntimeError(f"Foreground CLI session workspace did not match launch: {payload}")
                    if not event_payload.get("frontend_pid"):
                        raise RuntimeError(f"Foreground CLI session did not include frontend_pid: {payload}")
                    created = event_payload

            await self.send(ws, self.session_close_command_payload(created["session_id"]))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out waiting for foreground CLI session close")
                payload = await self.recv_json_with_timeout(ws, remaining)
                if payload.get("type") == "error":
                    raise RuntimeError(f"Local API error while closing foreground CLI: {payload}")
                if payload.get("type") != "event":
                    continue
                event = payload.get("event") or {}
                if (
                    event.get("type") == "agent.session.closed"
                    and (event.get("payload") or {}).get("session_id") == created["session_id"]
                ):
                    return

    async def run_approval_real(
        self,
        agent: str,
        context: str,
        approved: bool,
        require_forwarded: bool,
        wait_for_hotkey_approval: bool = False,
    ) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, self.agent_launch_payload(agent, context))

            deadline = time.monotonic() + self.timeout
            session_id = None
            last_payload = None

            async def recv_for_stage(stage: str) -> Dict[str, Any]:
                nonlocal last_payload
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise self._approval_timeout_error(stage, session_id, last_payload)
                try:
                    payload = await self.recv_json_with_timeout(ws, remaining)
                except asyncio.TimeoutError:
                    raise self._approval_timeout_error(stage, session_id, last_payload)
                last_payload = payload
                return payload

            async def wait_for_session_type(expected_type: str, stage: str) -> Dict[str, Any]:
                while True:
                    payload = await recv_for_stage(stage)
                    if payload.get("type") == "error":
                        raise RuntimeError(f"Local API error while waiting for {expected_type}: {payload}")
                    if payload.get("session_id") == session_id and payload.get("type") in {"task_failed", "error"}:
                        raise RuntimeError(f"Session failed while waiting for {expected_type}: {payload}")
                    if payload.get("type") == expected_type and payload.get("session_id") == session_id:
                        return payload

            while True:
                launch_ack = await recv_for_stage("launch_ack")
                if launch_ack.get("type") == "error":
                    raise RuntimeError(f"Local API error while waiting for launch_ack: {launch_ack}")
                if launch_ack.get("type") == "task_update":
                    break
            session_id = launch_ack.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError(f"Launch ack did not include session_id: {launch_ack}")

            permission_request = await wait_for_session_type("permission_request", "permission_request")
            request_id = permission_request["request_id"]
            if not wait_for_hotkey_approval:
                await self.send(ws, {
                    "type": "permission_response",
                    "request_id": request_id,
                    "session_id": session_id,
                    "approved": approved,
                    "timestamp": now_ts(),
                })

            ack = await wait_for_session_type("permission_ack", "permission_ack")
            if ack.get("request_id") != request_id or ack.get("session_id") != session_id:
                raise RuntimeError(f"Permission ack did not match request/session: {ack}")
            if require_forwarded and not ack.get("forwarded"):
                raise RuntimeError(f"Permission was not forwarded: {ack}")
            evidence = ack.get("evidence") or {}
            if not evidence:
                raise RuntimeError(f"Permission ack did not include forwarding evidence: {ack}")
            if agent == "claude":
                if evidence.get("adapter") != "claude_agent_sdk" or not evidence.get("callback_returned"):
                    raise RuntimeError(f"Permission ack did not include SDK callback evidence: {ack}")
            elif agent == "codex":
                if (
                    evidence.get("adapter") != "codex_app_server"
                    or not evidence.get("response_written")
                    or not evidence.get("decision_delivered")
                ):
                    raise RuntimeError(f"Permission ack did not include Codex app-server evidence: {ack}")

            while True:
                payload = await recv_for_stage("task_completed/task_failed")
                payload_type = payload.get("type")
                if payload.get("session_id") != session_id:
                    continue
                if payload_type == "task_completed":
                    return
                if payload_type in {"task_failed", "error"}:
                    raise RuntimeError(f"Approval scenario ended with failure: {payload}")

    async def run_virtual_input(self, agent: str, context: str) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, {
                "type": "virtual_device_configure",
                "device_id": VIRTUAL_DEVICE_ID,
                "profile": self._virtual_input_profile(agent, context),
                "timestamp": now_ts(),
            })
            configured = await self.wait_for_type(ws, "virtual_device_configured")
            if configured.get("active_profile_id") != "profile_virtual_smoke":
                raise RuntimeError(f"Virtual device did not activate profile: {configured}")

            await self.send(ws, self._focus_command("cmd_virtual_focus_agent", {
                "instance_id": f"{agent}-default",
            }))
            await self.wait_for_type(ws, "event")

            launch_ack = await self._send_virtual_key(ws, "K_LAUNCH")
            session_id = self._single_event_payload(launch_ack, "agent.session.created").get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError(f"Virtual launch did not create a session: {launch_ack}")

            await self.send(ws, self._focus_command("cmd_virtual_focus_session", {
                "session_id": session_id,
            }))
            await self.wait_for_type(ws, "event")

            tool_ack = await self._send_virtual_key(ws, "K_TOOL_1")
            self._single_event_payload(tool_ack, "keyboard.tool.changed")

            interrupt_ack = await self._send_virtual_key(ws, "K_ESC")
            self._single_event_payload(interrupt_ack, "agent.run.interrupted")

            close_ack = await self._send_virtual_key(ws, "K_DELETE")
            self._single_event_payload(close_ack, "agent.session.closed")

            await self.send(ws, {
                "type": "command",
                "command": {
                    "command_id": "cmd_virtual_snapshot",
                    "type": "system.snapshot.request",
                    "source": {"kind": self.client_kind, "client_id": self.client_id},
                    "payload": {},
                },
                "timestamp": now_ts(),
            })
            snapshot_payload = await self.wait_for_type(ws, "snapshot")
            snapshot = snapshot_payload.get("snapshot") or {}
            self._assert_virtual_snapshot(snapshot, session_id)

    async def _send_virtual_key(self, ws, key_id: str, **extra: Any) -> Dict[str, Any]:
        payload = {
            "type": "virtual_input",
            "device_id": VIRTUAL_DEVICE_ID,
            "key_id": key_id,
            "event_type": "press",
            "timestamp": now_ts(),
        }
        payload.update(extra)
        await self.send(ws, payload)
        return await self.wait_for_type(ws, "virtual_input_ack")

    def _focus_command(self, command_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "command",
            "command": {
                "command_id": command_id,
                "type": "agent.focus.set",
                "source": {"kind": self.client_kind, "client_id": self.client_id},
                "target": {"device_id": VIRTUAL_DEVICE_ID},
                "payload": payload,
            },
            "timestamp": now_ts(),
        }

    @staticmethod
    def _single_event_payload(payload: Dict[str, Any], expected_type: str) -> Dict[str, Any]:
        events = payload.get("events") or []
        if len(events) != 1 or events[0].get("type") != expected_type:
            raise RuntimeError(f"Expected {expected_type} from virtual input, got: {payload}")
        return events[0].get("payload") or {}

    @staticmethod
    def _virtual_input_profile(agent: str, context: str) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "id": "profile_virtual_smoke",
            "name": "Virtual Smoke",
            "target_device_family": "simulated",
            "layers": [
                {
                    "id": "layer_fn",
                    "priority": 10,
                    "activation": {"type": "hold_key", "key": "K_FN"},
                    "keymap": {
                        "K_ENTER": {
                            "type": "agent.permission.respond",
                            "target": "focused_permission",
                            "approved": True,
                        }
                    },
                }
            ],
            "keymap": {
                "bindings": {
                    "K_LAUNCH": {
                        "type": "agent.session.launch_or_resume",
                        "target": "focused_agent",
                        "agent": agent,
                        "context": context,
                    },
                    "K_ESC": {
                        "type": "agent.run.interrupt",
                        "target": "focused_run",
                    },
                    "K_DELETE": {
                        "type": "agent.session.close",
                        "target": "focused_session",
                    },
                    "K_TOOL_1": {
                        "type": "keyboard.tool.switch",
                        "target": {"tool_id": "permissions"},
                    },
                }
            },
        }

    @staticmethod
    def _assert_virtual_snapshot(snapshot: Dict[str, Any], session_id: str) -> None:
        profiles = snapshot.get("profiles") or {}
        focus = snapshot.get("focus") or {}
        active_tools = snapshot.get("active_tools") or {}
        sessions = snapshot.get("sessions") or {}
        devices = snapshot.get("devices") or {}
        device = devices.get(VIRTUAL_DEVICE_ID) or {}
        if profiles.get("active_profile_id") != "profile_virtual_smoke":
            raise RuntimeError(f"Snapshot missing active virtual profile: {snapshot}")
        if focus.get(VIRTUAL_DEVICE_ID, {}).get("target", {}).get("session_id") != session_id:
            raise RuntimeError(f"Snapshot missing virtual focus: {snapshot}")
        if active_tools.get(VIRTUAL_DEVICE_ID) != "permissions":
            raise RuntimeError(f"Snapshot missing virtual active tool: {snapshot}")
        if session_id not in sessions:
            raise RuntimeError(f"Snapshot missing launched session: {snapshot}")
        if not device.get("supports_agent_slots") or not device.get("supports_config_sync"):
            raise RuntimeError(f"Snapshot missing virtual device state: {snapshot}")


async def wait_for_service_hello(client: LocalApiSmokeClient, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            async with websockets.connect(client.url) as ws:
                await client.hello(ws)
                return True
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    if last_error:
        raise RuntimeError(f"Local Core service did not accept hello before timeout: {last_error}")
    raise RuntimeError("Local Core service did not accept hello before timeout")


async def ensure_local_core_service(
    client: LocalApiSmokeClient,
    auto_start: bool,
    config: str,
    workspace: str,
    service_start_timeout: float,
) -> subprocess.Popen:
    if not auto_start:
        return None
    try:
        await wait_for_service_hello(client, min(1.0, service_start_timeout))
        if not client.json_log:
            print("Local Core service is already running")
        return None
    except Exception:
        pass

    process = start_local_core_service(config, workspace)
    try:
        await wait_for_service_hello(client, service_start_timeout)
        if not client.json_log:
            print(f"Started Local Core service with config {config}")
        return process
    except BaseException:
        stop_spawned_service(process)
        raise


def stop_spawned_service(process: subprocess.Popen) -> None:
    if process is None or process.poll() is not None:
        return
    if sys.platform.startswith("win"):
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True)
            process.wait(timeout=5)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def amain() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the Local Core Service MVP WebSocket API")
    parser.add_argument("--url", default="ws://127.0.0.1:8765", help="Local Core Service WebSocket URL")
    parser.add_argument(
        "--scenario",
        choices=("basic", "permission", "real-agent", "approval-real", "virtual-input", "foreground-cli"),
        default="basic",
    )
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help="Prompt used by the real-agent smoke scenario")
    parser.add_argument("--timeout", type=float, default=10.0, help="Receive timeout in seconds")
    parser.add_argument("--json-log", action="store_true", help="Print each send/receive as JSON")
    parser.add_argument("--workspace", default=None, help="Workspace path to include in agent launch payloads")
    parser.add_argument(
        "--auto-start-service",
        action="store_true",
        help="Start Local Core service when the URL is not already accepting hello",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config path for auto-started Local Core service")
    parser.add_argument(
        "--service-start-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for an auto-started Local Core service to accept hello",
    )
    parser.add_argument("--request-id", default="req_1", help="Permission request id to approve or reject")
    parser.add_argument("--approved", default="true", help="Permission decision for the permission scenario")
    parser.add_argument("--decision", choices=("approve", "deny"), help="Permission decision alias for approval scenarios")
    parser.add_argument("--token", default="", help="Launch token for auth-enabled Local API")
    parser.add_argument("--client-kind", default=None, help="Local API client kind for hello")
    parser.add_argument("--client-id", default=None, help="Local API client id for hello")
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        default=None,
        help="Capability to advertise in hello; repeatable",
    )
    parser.add_argument(
        "--require-forwarded",
        action="store_true",
        help="Fail approval-real unless permission_ack.forwarded is true",
    )
    parser.add_argument(
        "--wait-for-hotkey-approval",
        action="store_true",
        help="In approval-real, wait for another client or hotkey to submit the permission response",
    )
    args = parser.parse_args()

    if args.scenario == "virtual-input":
        client_kind = args.client_kind or "device-transport"
        client_id = args.client_id or VIRTUAL_DEVICE_ID
        capabilities = args.capabilities or ["agent:launch", "permission:respond:low_risk", "session:list"]
    else:
        client_kind = args.client_kind or "desktop-ui"
        client_id = args.client_id or "local-api-smoke"
        capabilities = args.capabilities or ["agent:launch", "permission:respond", "session:list"]
    client = LocalApiSmokeClient(
        args.url,
        args.timeout,
        args.json_log,
        args.token,
        client_kind,
        client_id,
        capabilities,
        workspace=args.workspace,
    )
    context = args.context
    if args.scenario == "approval-real" and context == DEFAULT_CONTEXT:
        context = (
            DEFAULT_CODEX_APPROVAL_CONTEXT
            if args.agent == "codex"
            else DEFAULT_CLAUDE_APPROVAL_CONTEXT
        )

    service_process = await ensure_local_core_service(
        client,
        args.auto_start_service,
        args.config,
        args.workspace,
        args.service_start_timeout,
    )
    try:
        if args.scenario == "basic":
            await client.run_basic()
        elif args.scenario == "permission":
            approved = args.decision == "approve" if args.decision else parse_bool(args.approved)
            await client.run_permission(args.request_id, approved)
        elif args.scenario == "approval-real":
            approved = args.decision == "approve" if args.decision else parse_bool(args.approved)
            await client.run_approval_real(
                args.agent,
                context,
                approved,
                args.require_forwarded,
                wait_for_hotkey_approval=args.wait_for_hotkey_approval,
            )
        elif args.scenario == "virtual-input":
            await client.run_virtual_input(args.agent, context)
        elif args.scenario == "foreground-cli":
            await client.run_foreground_cli(args.agent)
        else:
            await client.run_real_agent(args.agent, context)
        if not args.json_log:
            print(f"Smoke scenario completed: {args.scenario}")
    finally:
        stop_spawned_service(service_process)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
