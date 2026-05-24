from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from app import build_runtime  # noqa: E402
from core import CommandEnvelope, CommandSource, EventEnvelope  # noqa: E402
from core.target_resolution import TargetResolver  # noqa: E402
from keyboard import ScreenFocus  # noqa: E402


def _command(
    command_type: str,
    *,
    device_id: str = "kbd_01",
    target=None,
    payload=None,
    command_id: str = "cmd_focus",
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


def test_focus_set_stores_focus_per_device_and_emits_changed_event():
    runtime = build_runtime()

    event = runtime.command_router.dispatch(_command(
        "agent.focus.set",
        target={"device_id": "kbd_01"},
        payload={
            "mode": "session",
            "instance_id": "codex-software",
            "session_id": "sess_01",
        },
    ))
    runtime.command_router.dispatch(_command(
        "agent.focus.set",
        device_id="kbd_02",
        target={"device_id": "kbd_02"},
        payload={
            "mode": "instance",
            "instance_id": "claude-hardware",
        },
        command_id="cmd_focus_2",
    ))

    assert event.type == "agent.focus.changed"
    assert event.target == {"device_id": "kbd_01"}
    assert event.payload["device_id"] == "kbd_01"
    assert event.payload["target"]["session_id"] == "sess_01"

    snapshot = runtime.snapshot().to_dict()
    assert snapshot["focus"]["kbd_01"]["mode"] == "session"
    assert snapshot["focus"]["kbd_01"]["target"]["session_id"] == "sess_01"
    assert snapshot["focus"]["kbd_02"]["mode"] == "instance"
    assert snapshot["focus"]["kbd_02"]["target"]["instance_id"] == "claude-hardware"


def test_focus_next_session_cycles_per_device_without_affecting_other_devices():
    runtime = build_runtime()
    runtime.state_store.sessions = {
        "sess_01": {"session_id": "sess_01", "instance_id": "codex-software"},
        "sess_02": {"session_id": "sess_02", "instance_id": "codex-software"},
        "sess_03": {"session_id": "sess_03", "instance_id": "claude-hardware"},
    }

    runtime.command_router.dispatch(_command(
        "agent.focus.set",
        target={"device_id": "kbd_01"},
        payload={"mode": "session", "session_id": "sess_01"},
    ))
    runtime.command_router.dispatch(_command(
        "agent.focus.set",
        device_id="kbd_02",
        target={"device_id": "kbd_02"},
        payload={"mode": "session", "session_id": "sess_03"},
        command_id="cmd_focus_other",
    ))

    first = runtime.command_router.dispatch(_command(
        "agent.focus.next_session",
        target={"device_id": "kbd_01"},
        command_id="cmd_next_1",
    ))
    second = runtime.command_router.dispatch(_command(
        "agent.focus.next_session",
        target={"device_id": "kbd_01"},
        command_id="cmd_next_2",
    ))
    third = runtime.command_router.dispatch(_command(
        "agent.focus.next_session",
        target={"device_id": "kbd_01"},
        command_id="cmd_next_3",
    ))

    assert first.payload["target"]["session_id"] == "sess_02"
    assert second.payload["target"]["session_id"] == "sess_03"
    assert third.payload["target"]["session_id"] == "sess_01"

    snapshot = runtime.snapshot().to_dict()
    assert snapshot["focus"]["kbd_01"]["target"]["session_id"] == "sess_01"
    assert snapshot["focus"]["kbd_02"]["target"]["session_id"] == "sess_03"


def test_focused_permission_resolves_by_run_session_instance_then_priority():
    resolver = TargetResolver()
    permissions = [
        {"request_id": "perm_global", "priority": 500},
        {"request_id": "perm_instance", "instance_id": "codex-software", "priority": 100},
        {
            "request_id": "perm_session",
            "instance_id": "codex-software",
            "session_id": "sess_01",
            "priority": 10,
        },
        {
            "request_id": "perm_run",
            "instance_id": "codex-software",
            "session_id": "sess_01",
            "run_id": "run_01",
            "priority": 1,
        },
    ]

    run_result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="run",
            instance_id="codex-software",
            session_id="sess_01",
            run_id="run_01",
        ),
        permissions=permissions,
    )
    session_result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            instance_id="codex-software",
            session_id="sess_01",
        ),
        permissions=permissions,
    )
    instance_result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="instance",
            instance_id="codex-software",
        ),
        permissions=permissions,
    )
    global_result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(device_id="kbd_01"),
        permissions=permissions,
    )

    assert run_result.target["permission_id"] == "perm_run"
    assert session_result.target["permission_id"] == "perm_session"
    assert instance_result.target["permission_id"] == "perm_instance"
    assert global_result.target["permission_id"] == "perm_global"


def test_unresolved_focused_run_and_session_emit_structured_error_events():
    runtime = build_runtime()
    calls = []

    def downstream(command: CommandEnvelope) -> EventEnvelope:
        calls.append(command)
        return EventEnvelope(seq=0, type="downstream.called", payload={})

    runtime.keyboard_runtime.register_targeted_handlers(runtime.command_router, {
        "agent.run.interrupt": downstream,
        "agent.session.close": downstream,
    })

    run_event = runtime.command_router.dispatch(_command(
        "agent.run.interrupt",
        target="focused_run",
        command_id="cmd_unresolved_run",
    ))
    session_event = runtime.command_router.dispatch(_command(
        "agent.session.close",
        target={"selector": "focused_session"},
        command_id="cmd_unresolved_session",
    ))

    assert calls == []
    assert run_event.type == "command.target.unresolved"
    assert run_event.payload["code"] == "UNRESOLVED_TARGET"
    assert run_event.payload["selector"] == "focused_run"
    assert run_event.payload["command_id"] == "cmd_unresolved_run"
    assert "AttributeError" not in run_event.payload["message"]

    assert session_event.type == "command.target.unresolved"
    assert session_event.payload["code"] == "UNRESOLVED_TARGET"
    assert session_event.payload["selector"] == "focused_session"
    assert session_event.payload["command_id"] == "cmd_unresolved_session"
