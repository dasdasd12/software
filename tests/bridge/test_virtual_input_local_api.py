import asyncio
import json
import subprocess
import sys
from pathlib import Path

import websockets


ROOT_DIR = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT_DIR / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import LocalCoreServiceMVP  # noqa: E402
from session_manager import AgentType  # noqa: E402


class FakeProxy:
    def __init__(self):
        self.launched = []
        self.interrupted = []
        self.terminated = []
        self.permission_responses = []

    def is_available(self):
        return True

    async def launch(self, session_id, context=""):
        self.launched.append((session_id, context))
        return None

    async def resume(self, session_id):
        self.launched.append((session_id, "resume"))
        return None

    async def send_interrupt(self, session_id):
        self.interrupted.append(session_id)
        return True

    async def terminate(self, session_id):
        self.terminated.append(session_id)
        return True

    async def handle_permission_response(self, session_id, request_id, approved):
        self.permission_responses.append((session_id, request_id, approved))
        return {
            "accepted": True,
            "forwarded": False,
            "evidence": {"adapter": "fake", "request_id": request_id},
        }


def make_service():
    service = LocalCoreServiceMVP({
        "server": {"host": "127.0.0.1", "port": 0},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
        "security": {
            "client_capabilities": {
                "device-transport": [
                    "agent:launch",
                    "permission:respond:low_risk",
                    "session:list",
                ],
            },
        },
    })
    service.agents[AgentType.CODEX] = FakeProxy()
    return service


async def with_local_api(run_client):
    service = make_service()
    ws_server = await websockets.serve(service._handle_local_api_client, "127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    try:
        return await run_client(service, f"ws://127.0.0.1:{port}")
    finally:
        ws_server.close()
        await ws_server.wait_closed()


async def recv_json(ws, timeout=1.0):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def wait_for(ws, expected_type, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        payload = await recv_json(ws, timeout=deadline - asyncio.get_running_loop().time())
        if payload.get("type") == "error":
            if expected_type == "error":
                return payload
            raise AssertionError(payload)
        if payload.get("type") == expected_type:
            return payload
    raise AssertionError(f"timed out waiting for {expected_type}")


def virtual_profile():
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
                    "agent": "codex",
                    "context": "from virtual input",
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


async def send_command(ws, command):
    await ws.send(json.dumps({"type": "command", "command": command}))


async def send_virtual_key(ws, device_id, key_id, **extra):
    payload = {
        "type": "virtual_input",
        "device_id": device_id,
        "key_id": key_id,
        "event_type": "press",
    }
    payload.update(extra)
    await ws.send(json.dumps(payload))
    return await wait_for(ws, "virtual_input_ack")


def test_virtual_input_local_api_routes_key_sequence_and_snapshot_state():
    async def run_client(service, uri):
        device_id = "kbd_virtual_11"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "client_kind": "device-transport",
                "client_id": device_id,
                "capabilities": ["agent:launch", "permission:respond:low_risk", "session:list"],
            }))
            assert (await recv_json(ws))["type"] == "hello_ack"

            await ws.send(json.dumps({
                "type": "virtual_device_configure",
                "device_id": device_id,
                "profile": virtual_profile(),
            }))
            configure_ack = await wait_for(ws, "virtual_device_configured")
            assert configure_ack["active_profile_id"] == "profile_virtual_smoke"

            await send_command(ws, {
                "command_id": "cmd_focus_agent",
                "type": "agent.focus.set",
                "source": {"kind": "device-transport", "device_id": device_id},
                "target": {"device_id": device_id},
                "payload": {"instance_id": "codex-default"},
            })
            await wait_for(ws, "event")

            launch_ack = await send_virtual_key(ws, device_id, "K_LAUNCH")
            launch_events = launch_ack["events"]
            assert [event["type"] for event in launch_events] == ["agent.session.created"]
            session_id = launch_events[0]["payload"]["session_id"]
            assert service.agents[AgentType.CODEX].launched == [(session_id, "from virtual input")]

            await send_command(ws, {
                "command_id": "cmd_focus_session",
                "type": "agent.focus.set",
                "source": {"kind": "device-transport", "device_id": device_id},
                "target": {"device_id": device_id},
                "payload": {"session_id": session_id},
            })
            await wait_for(ws, "event")

            service._on_agent_event(service.unifier.encode_device_message({
                "type": "permission_request",
                "request_id": "perm_low_virtual",
                "session_id": session_id,
                "agent": "codex",
                "risk_level": "low",
                "tool": "shell",
                "description": "low risk virtual approval",
                "timeout_sec": 30,
            }))

            await send_command(ws, {
                "command_id": "cmd_snapshot_pending",
                "type": "system.snapshot.request",
                "source": {"kind": "device-transport", "device_id": device_id},
                "payload": {},
            })
            pending_snapshot = await wait_for(ws, "snapshot")
            snapshot = pending_snapshot["snapshot"]
            assert snapshot["profiles"]["active_profile_id"] == "profile_virtual_smoke"
            assert snapshot["profiles"]["active_profile_by_device"][device_id] == "profile_virtual_smoke"
            assert snapshot["focus"][device_id]["target"]["session_id"] == session_id
            assert session_id in snapshot["sessions"]
            assert snapshot["permissions"][0]["request_id"] == "perm_low_virtual"
            assert snapshot["devices"][device_id]["supports_agent_slots"] is True

            permission_ack = await send_virtual_key(
                ws,
                device_id,
                "K_ENTER",
                active_layers=["layer_fn"],
                modifiers=["fn"],
            )
            assert permission_ack["events"][0]["type"] == "agent.permission.resolved"
            assert service.agents[AgentType.CODEX].permission_responses == [
                (session_id, "perm_low_virtual", True)
            ]

            tool_ack = await send_virtual_key(ws, device_id, "K_TOOL_1")
            assert tool_ack["events"][0]["type"] == "keyboard.tool.changed"

            interrupt_ack = await send_virtual_key(ws, device_id, "K_ESC")
            assert interrupt_ack["events"][0]["type"] == "agent.run.interrupted"
            assert service.agents[AgentType.CODEX].interrupted == [session_id]

            close_ack = await send_virtual_key(ws, device_id, "K_DELETE")
            assert close_ack["events"][0]["type"] == "agent.session.closed"
            assert service.agents[AgentType.CODEX].terminated == [session_id]

            await send_command(ws, {
                "command_id": "cmd_snapshot_final",
                "type": "system.snapshot.request",
                "source": {"kind": "device-transport", "device_id": device_id},
                "payload": {},
            })
            final_snapshot = (await wait_for(ws, "snapshot"))["snapshot"]
            assert final_snapshot["active_tools"][device_id] == "permissions"
            assert final_snapshot["devices"][device_id]["active_tool_id"] == "permissions"
            assert final_snapshot["devices"][device_id]["is_open"] is True

    asyncio.run(with_local_api(run_client))


def test_virtual_input_device_client_without_launch_capability_cannot_launch_session():
    async def run_client(service, uri):
        device_id = "kbd_virtual_11"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "client_kind": "device-transport",
                "client_id": device_id,
                "capabilities": [],
            }))
            assert (await recv_json(ws))["type"] == "hello_ack"

            await ws.send(json.dumps({
                "type": "virtual_device_configure",
                "device_id": device_id,
                "profile": virtual_profile(),
            }))
            assert (await wait_for(ws, "virtual_device_configured"))["active_profile_id"] == "profile_virtual_smoke"

            await send_command(ws, {
                "command_id": "cmd_focus_agent_no_launch_cap",
                "type": "agent.focus.set",
                "source": {"kind": "device-transport", "device_id": device_id},
                "target": {"device_id": device_id},
                "payload": {"instance_id": "codex-default"},
            })
            await wait_for(ws, "event")

            await ws.send(json.dumps({
                "type": "virtual_input",
                "device_id": device_id,
                "key_id": "K_LAUNCH",
                "event_type": "press",
            }))
            error = await wait_for(ws, "error")

            assert error["code"] == "CAPABILITY_DENIED"
            assert service.session_mgr.list_all() == []
            assert service.agents[AgentType.CODEX].launched == []

    asyncio.run(with_local_api(run_client))


def test_virtual_input_device_client_cannot_send_for_another_device_id():
    async def run_client(service, uri):
        device_id = "kbd_virtual_11"
        client_id = "kbd_virtual_other"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "client_kind": "device-transport",
                "client_id": client_id,
                "capabilities": ["agent:launch", "permission:respond:low_risk", "session:list"],
            }))
            assert (await recv_json(ws))["type"] == "hello_ack"

            await ws.send(json.dumps({
                "type": "virtual_device_configure",
                "device_id": device_id,
                "profile": virtual_profile(),
            }))
            assert (await wait_for(ws, "virtual_device_configured"))["active_profile_id"] == "profile_virtual_smoke"

            await ws.send(json.dumps({
                "type": "virtual_input",
                "device_id": device_id,
                "key_id": "K_LAUNCH",
                "event_type": "press",
            }))
            error = await wait_for(ws, "error")

            assert error["code"] == "DEVICE_ID_MISMATCH"
            assert service.session_mgr.list_all() == []
            assert service.agents[AgentType.CODEX].launched == []

    asyncio.run(with_local_api(run_client))


def test_virtual_input_device_client_with_low_risk_capability_can_approve_low_risk_permission():
    async def run_client(service, uri):
        device_id = "kbd_virtual_11"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "client_kind": "device-transport",
                "client_id": device_id,
                "capabilities": ["permission:respond:low_risk"],
            }))
            assert (await recv_json(ws))["type"] == "hello_ack"

            await ws.send(json.dumps({
                "type": "virtual_device_configure",
                "device_id": device_id,
                "profile": virtual_profile(),
            }))
            assert (await wait_for(ws, "virtual_device_configured"))["active_profile_id"] == "profile_virtual_smoke"

            session = service.session_mgr.create(AgentType.CODEX)
            await send_command(ws, {
                "command_id": "cmd_focus_session_low_risk",
                "type": "agent.focus.set",
                "source": {"kind": "device-transport", "device_id": device_id},
                "target": {"device_id": device_id},
                "payload": {"session_id": session.session_id},
            })
            await wait_for(ws, "event")

            service._on_agent_event(service.unifier.encode_device_message({
                "type": "permission_request",
                "request_id": "perm_low_virtual_direct",
                "session_id": session.session_id,
                "agent": "codex",
                "risk_level": "low",
                "tool": "shell",
                "description": "low risk virtual approval",
                "timeout_sec": 30,
            }))

            permission_ack = await send_virtual_key(
                ws,
                device_id,
                "K_ENTER",
                active_layers=["layer_fn"],
                modifiers=["fn"],
            )

            assert permission_ack["events"][0]["type"] == "agent.permission.resolved"
            assert service.agents[AgentType.CODEX].permission_responses == [
                (session.session_id, "perm_low_virtual_direct", True)
            ]

    asyncio.run(with_local_api(run_client))


def test_virtual_input_device_client_cannot_approve_high_risk_permission():
    async def run_client(service, uri):
        device_id = "kbd_virtual_11"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "client_kind": "device-transport",
                "client_id": device_id,
                "capabilities": ["permission:respond:low_risk", "session:list"],
            }))
            assert (await recv_json(ws))["type"] == "hello_ack"

            await ws.send(json.dumps({
                "type": "virtual_device_configure",
                "device_id": device_id,
                "profile": virtual_profile(),
            }))
            assert (await wait_for(ws, "virtual_device_configured"))["active_profile_id"] == "profile_virtual_smoke"

            session = service.session_mgr.create(AgentType.CODEX)
            await send_command(ws, {
                "command_id": "cmd_focus_session_high_risk",
                "type": "agent.focus.set",
                "source": {"kind": "device-transport", "device_id": device_id},
                "target": {"device_id": device_id},
                "payload": {"session_id": session.session_id},
            })
            await wait_for(ws, "event")

            service._on_agent_event(service.unifier.encode_device_message({
                "type": "permission_request",
                "request_id": "perm_high_virtual",
                "session_id": session.session_id,
                "agent": "codex",
                "risk_level": "high",
                "tool": "shell",
                "description": "high risk virtual approval",
                "timeout_sec": 30,
            }))

            await ws.send(json.dumps({
                "type": "virtual_input",
                "device_id": device_id,
                "key_id": "K_ENTER",
                "event_type": "press",
                "active_layers": ["layer_fn"],
                "modifiers": ["fn"],
            }))
            error = await wait_for(ws, "error")

            assert error["code"] == "REQUIRE_DESKTOP_CONFIRM"
            assert service.agents[AgentType.CODEX].permission_responses == []
            assert service._find_pending_permission(
                "perm_high_virtual",
                session.session_id,
                None,
                None,
            )[1] is not None

    asyncio.run(with_local_api(run_client))


def test_local_api_smoke_exposes_virtual_input_scenario():
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "local-api-smoke.py"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "virtual-input" in result.stdout
