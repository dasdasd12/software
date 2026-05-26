import asyncio
import json
import sys
from pathlib import Path

import pytest
import websockets


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
BRIDGE_DIR = ROOT_DIR / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(SRC_DIR))

import harness.hotkeys as hotkey_module  # noqa: E402
from harness.hotkeys import (  # noqa: E402
    DEFAULT_CAPABILITIES,
    DEFAULT_CLIENT_ID,
    DEFAULT_DEVICE_ID,
    HOTKEY_TO_KEY_ID,
    HotkeyHarness,
    HotkeyHarnessConfig,
    PynputDependencyError,
    PynputHotkeyEventSource,
    build_hello_message,
    build_virtual_profile,
)
from keyboard import DEFAULT_PHYSICAL_LAYOUT_ID, get_layout_keys, profile_from_dict  # noqa: E402
from server import LocalCoreServiceMVP  # noqa: E402
from session_manager import AgentType  # noqa: E402


class FakeWebSocket:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        if not self.incoming:
            raise AssertionError("unexpected recv")
        return json.dumps(self.incoming.pop(0))


class SerialCheckingWebSocket(FakeWebSocket):
    def __init__(self, incoming):
        super().__init__(incoming)
        self.active_recv = 0
        self.max_active_recv = 0

    async def recv(self):
        self.active_recv += 1
        self.max_active_recv = max(self.max_active_recv, self.active_recv)
        try:
            await asyncio.sleep(0.01)
            return await super().recv()
        finally:
            self.active_recv -= 1


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


async def send_command(ws, command):
    await ws.send(json.dumps({"type": "command", "command": command}))


async def send_virtual_key(ws, device_id, key_id):
    await ws.send(json.dumps({
        "type": "virtual_input",
        "device_id": device_id,
        "key_id": key_id,
        "event_type": "press",
    }))
    return await wait_for(ws, "virtual_input_ack")


def test_default_hotkeys_map_to_virtual_input_payloads():
    harness = HotkeyHarness(HotkeyHarnessConfig(device_id="kbd_hotkey_harness"))

    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+1"] == "K_CODEX_LAUNCH"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+2"] == "K_CLAUDE_LAUNCH"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+enter"] == "K_APPROVE"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+backspace"] == "K_DENY"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+esc"] == "K_INTERRUPT"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+q"] == "K_CLOSE"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+tab"] == "K_FOCUS_NEXT"
    assert HOTKEY_TO_KEY_ID["ctrl+alt+shift+t"] == "K_TOOL_NEXT"

    assert harness.message_for_hotkey("ctrl+alt+shift+enter") == {
        "type": "virtual_input",
        "device_id": "kbd_hotkey_harness",
        "key_id": "K_APPROVE",
        "event_type": "press",
    }


def test_harness_profile_uses_isolated_layout_for_synthetic_keys():
    synthetic_keys = {
        "K_CODEX_LAUNCH",
        "K_CLAUDE_LAUNCH",
        "K_APPROVE",
        "K_DENY",
        "K_INTERRUPT",
        "K_CLOSE",
        "K_FOCUS_NEXT",
        "K_TOOL_NEXT",
    }

    assert synthetic_keys.isdisjoint(get_layout_keys(DEFAULT_PHYSICAL_LAYOUT_ID))

    profile = build_virtual_profile()
    assert profile["keymap"]["physical_layout_id"] == "hotkey_harness_layout"
    assert synthetic_keys <= set(get_layout_keys("hotkey_harness_layout"))
    assert profile_from_dict(profile).id == "profile_local_hotkey_harness"


def test_hello_payload_uses_desktop_ui_identity_and_capabilities():
    payload = build_hello_message(HotkeyHarnessConfig(token="token-1"))

    assert payload["type"] == "hello"
    assert payload["client_kind"] == "desktop-ui"
    assert payload["client_id"] == "test-harness"
    assert payload["client_id"] == DEFAULT_CLIENT_ID
    assert payload["token"] == "token-1"
    assert set(payload["capabilities"]) == set(DEFAULT_CAPABILITIES)
    assert {"agent:launch", "permission:respond", "session:list"} <= set(payload["capabilities"])


def test_virtual_profile_contains_expected_command_action_bindings():
    profile = build_virtual_profile(
        codex_context="codex prompt",
        claude_context="claude prompt",
        workspace="C:/work",
    )

    bindings = profile["keymap"]["bindings"]
    assert bindings["K_CODEX_LAUNCH"] == {
        "type": "agent.session.launch_or_resume",
        "target": "focused_agent",
        "agent": "codex",
        "context": "codex prompt",
        "workspace": "C:/work",
    }
    assert bindings["K_CLAUDE_LAUNCH"] == {
        "type": "agent.session.launch_or_resume",
        "target": "focused_agent",
        "agent": "claude",
        "context": "claude prompt",
        "workspace": "C:/work",
    }
    assert bindings["K_APPROVE"] == {
        "type": "agent.permission.respond",
        "target": "focused_permission",
        "approved": True,
    }
    assert bindings["K_DENY"] == {
        "type": "agent.permission.respond",
        "target": "focused_permission",
        "approved": False,
    }
    assert bindings["K_INTERRUPT"]["type"] == "agent.run.interrupt"
    assert bindings["K_CLOSE"]["type"] == "agent.session.close"
    assert bindings["K_FOCUS_NEXT"]["type"] == "agent.focus.next_session"
    assert bindings["K_TOOL_NEXT"]["type"] == "keyboard.tool.next"

    parsed = profile_from_dict(profile)
    assert parsed.id == "profile_local_hotkey_harness"


def test_missing_pynput_listener_setup_reports_clear_dependency(monkeypatch):
    def missing_import(name, *args, **kwargs):
        if name.startswith("pynput"):
            raise ImportError("missing pynput")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", missing_import)

    with pytest.raises(PynputDependencyError) as exc:
        PynputHotkeyEventSource(lambda key_id: None).start()

    assert "pynput is required for global hotkeys" in str(exc.value)


def test_queued_hotkey_sender_serializes_sends_and_receives():
    async def run():
        ws = SerialCheckingWebSocket([
            {"type": "virtual_input_ack", "events": []},
            {"type": "virtual_input_ack", "events": []},
        ])
        harness = HotkeyHarness(HotkeyHarnessConfig())
        sender = hotkey_module.QueuedHotkeySender(harness, ws).start()

        sender.enqueue("K_APPROVE")
        sender.enqueue("K_DENY")
        await sender.drain()
        await sender.stop()

        return ws

    ws = asyncio.run(run())

    assert ws.max_active_recv == 1
    assert [message["key_id"] for message in ws.sent] == ["K_APPROVE", "K_DENY"]
    assert [message["sequence"] for message in ws.sent] == [1, 2]


def test_queued_hotkey_sender_reports_send_failures(capsys):
    class FailingHarness(HotkeyHarness):
        async def send_key_id(self, ws, key_id):
            raise RuntimeError("send exploded")

    async def run():
        sender = hotkey_module.QueuedHotkeySender(
            FailingHarness(HotkeyHarnessConfig()),
            FakeWebSocket([]),
        ).start()
        sender.enqueue("K_APPROVE")
        await sender.drain()
        await sender.stop()

    asyncio.run(run())

    assert "hotkey send failed for K_APPROVE: send exploded" in capsys.readouterr().err


def test_fake_websocket_sends_hello_configure_and_one_hotkey_then_focuses_created_session():
    async def run():
        ws = FakeWebSocket([
            {"type": "hello_ack"},
            {"type": "virtual_device_configured", "active_profile_id": "profile_local_hotkey_harness"},
            {
                "type": "virtual_input_ack",
                "events": [
                    {
                        "type": "agent.session.created",
                        "payload": {"session_id": "sess_1"},
                    }
                ],
            },
        ])
        harness = HotkeyHarness(HotkeyHarnessConfig())

        await harness.run_once(ws, "ctrl+alt+shift+1")

        return ws.sent

    sent = asyncio.run(run())

    assert [message["type"] for message in sent] == [
        "hello",
        "virtual_device_configure",
        "virtual_input",
        "command",
    ]
    assert sent[1]["device_id"] == DEFAULT_DEVICE_ID
    assert sent[2]["key_id"] == "K_CODEX_LAUNCH"
    assert sent[3]["command"]["type"] == "agent.focus.set"
    assert sent[3]["command"]["target"] == {"device_id": DEFAULT_DEVICE_ID}
    assert sent[3]["command"]["payload"] == {"session_id": "sess_1"}


def test_local_api_harness_profile_approves_and_denies_via_virtual_input():
    async def run_client(service, uri):
        device_id = DEFAULT_DEVICE_ID
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps(build_hello_message(HotkeyHarnessConfig())))
            assert (await recv_json(ws))["type"] == "hello_ack"

            await ws.send(json.dumps({
                "type": "virtual_device_configure",
                "device_id": device_id,
                "profile": build_virtual_profile(),
            }))
            assert (await wait_for(ws, "virtual_device_configured"))["active_profile_id"] == (
                "profile_local_hotkey_harness"
            )

            session = service.session_mgr.create(AgentType.CODEX)
            await send_command(ws, {
                "command_id": "cmd_focus_hotkey_session",
                "type": "agent.focus.set",
                "source": {"kind": "desktop-ui", "client_id": DEFAULT_CLIENT_ID},
                "target": {"device_id": device_id},
                "payload": {"session_id": session.session_id},
            })
            await wait_for(ws, "event")

            service._on_agent_event(service.unifier.encode_device_message({
                "type": "permission_request",
                "request_id": "perm_hotkey_approve",
                "session_id": session.session_id,
                "agent": "codex",
                "risk_level": "low",
                "tool": "shell",
                "description": "approve from harness",
                "timeout_sec": 30,
            }))
            approve_ack = await send_virtual_key(ws, device_id, "K_APPROVE")
            assert approve_ack["events"][0]["type"] == "agent.permission.resolved"

            service._on_agent_event(service.unifier.encode_device_message({
                "type": "permission_request",
                "request_id": "perm_hotkey_deny",
                "session_id": session.session_id,
                "agent": "codex",
                "risk_level": "low",
                "tool": "shell",
                "description": "deny from harness",
                "timeout_sec": 30,
            }))
            deny_ack = await send_virtual_key(ws, device_id, "K_DENY")
            assert deny_ack["events"][0]["type"] == "agent.permission.resolved"

            assert service.agents[AgentType.CODEX].permission_responses == [
                (session.session_id, "perm_hotkey_approve", True),
                (session.session_id, "perm_hotkey_deny", False),
            ]

    asyncio.run(with_local_api(run_client))
