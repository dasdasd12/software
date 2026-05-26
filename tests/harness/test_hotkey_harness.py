import asyncio
import json
import sys
from pathlib import Path

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

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
from keyboard import profile_from_dict  # noqa: E402


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
    assert bindings["K_APPROVE"]["target"] == "focused_permission"
    assert bindings["K_APPROVE"]["decision"] == "approve"
    assert bindings["K_DENY"]["target"] == "focused_permission"
    assert bindings["K_DENY"]["decision"] == "deny"
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
