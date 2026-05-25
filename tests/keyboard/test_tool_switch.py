from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from app import build_runtime  # noqa: E402
from core import CommandEnvelope, CommandSource  # noqa: E402


def _command(
    command_type: str,
    *,
    device_id: str = "kbd_01",
    target=None,
    payload=None,
    command_id: str = "cmd_tool",
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id,
        type=command_type,
        source=CommandSource(
            kind="keyboard-device",
            client_id=device_id,
            device_id=device_id,
        ),
        target=target,
        payload=dict(payload or {}),
    )


def test_tool_switch_accepts_known_tool_for_target_device_and_updates_snapshot():
    runtime = build_runtime()

    event = runtime.command_router.dispatch(_command(
        "keyboard.tool.switch",
        target={"device_id": "kbd_01", "tool_id": "permissions"},
    ))

    assert event.type == "keyboard.tool.changed"
    assert event.target == {"device_id": "kbd_01"}
    assert event.payload["device_id"] == "kbd_01"
    assert event.payload["tool_id"] == "permissions"
    assert event.payload["previous_tool_id"] is None

    snapshot = runtime.snapshot().to_dict()
    assert snapshot["active_tools"]["kbd_01"] == "permissions"
    assert snapshot["devices"]["kbd_01"]["active_tool_id"] == "permissions"


def test_tool_switch_accepts_payload_tool_and_source_device_fallback():
    runtime = build_runtime()

    event = runtime.command_router.dispatch(_command(
        "keyboard.tool.switch",
        payload={"tool_id": "session_list"},
    ))

    assert event.type == "keyboard.tool.changed"
    assert event.target == {"device_id": "kbd_01"}
    assert event.payload["device_id"] == "kbd_01"
    assert event.payload["tool_id"] == "session_list"


def test_tool_next_cycles_configured_tools_in_order_per_device():
    runtime = build_runtime()

    first = runtime.command_router.dispatch(_command(
        "keyboard.tool.next",
        target={"device_id": "kbd_01"},
        command_id="cmd_tool_next_1",
    ))
    second = runtime.command_router.dispatch(_command(
        "keyboard.tool.next",
        target={"device_id": "kbd_01"},
        command_id="cmd_tool_next_2",
    ))
    runtime.command_router.dispatch(_command(
        "keyboard.tool.switch",
        target={"device_id": "kbd_01", "tool_id": "device_status"},
        command_id="cmd_tool_switch_last",
    ))
    wrapped = runtime.command_router.dispatch(_command(
        "keyboard.tool.next",
        target={"device_id": "kbd_01"},
        command_id="cmd_tool_next_wrap",
    ))
    other_device = runtime.command_router.dispatch(_command(
        "keyboard.tool.next",
        device_id="kbd_02",
        command_id="cmd_tool_next_other",
    ))

    assert first.payload["tool_id"] == "agent_control"
    assert first.payload["previous_tool_id"] is None
    assert second.payload["tool_id"] == "session_list"
    assert second.payload["previous_tool_id"] == "agent_control"
    assert wrapped.payload["tool_id"] == "agent_control"
    assert wrapped.payload["previous_tool_id"] == "device_status"
    assert other_device.payload["device_id"] == "kbd_02"
    assert other_device.payload["tool_id"] == "agent_control"

    snapshot = runtime.snapshot().to_dict()
    assert snapshot["active_tools"] == {
        "kbd_01": "agent_control",
        "kbd_02": "agent_control",
    }


def test_unknown_tool_is_rejected_without_mutating_existing_state():
    runtime = build_runtime()
    runtime.command_router.dispatch(_command(
        "keyboard.tool.switch",
        target={"device_id": "kbd_01", "tool_id": "permissions"},
    ))
    before = runtime.snapshot().to_dict()

    event = runtime.command_router.dispatch(_command(
        "keyboard.tool.switch",
        target={"device_id": "kbd_01", "tool_id": "unknown_tool"},
        command_id="cmd_tool_reject",
    ))

    assert event.type == "keyboard.tool.rejected"
    assert event.target == {"device_id": "kbd_01"}
    assert event.payload["code"] == "unknown_tool"
    assert event.payload["command_id"] == "cmd_tool_reject"
    assert event.payload["device_id"] == "kbd_01"
    assert event.payload["tool_id"] == "unknown_tool"
    assert "unknown_tool" in event.payload["message"]
    assert runtime.snapshot().to_dict()["active_tools"] == before["active_tools"]
    assert runtime.snapshot().to_dict()["devices"] == before["devices"]
