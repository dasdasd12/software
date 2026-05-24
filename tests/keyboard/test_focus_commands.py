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


def test_focused_permission_global_fallback_never_selects_other_scoped_permission():
    resolver = TargetResolver()
    permissions = [
        {
            "request_id": "perm_other_session",
            "instance_id": "codex-software",
            "session_id": "sess_other",
            "priority": 100,
        },
        {"request_id": "perm_global", "priority": 1},
    ]

    result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            instance_id="codex-software",
            session_id="sess_focus",
        ),
        permissions=permissions,
    )

    assert result.resolved
    assert result.target["permission_id"] == "perm_global"


def test_focused_permission_unresolved_when_only_other_scoped_permissions_exist():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            instance_id="codex-software",
            session_id="sess_focus",
        ),
        permissions=[
            {
                "request_id": "perm_other_session",
                "instance_id": "codex-software",
                "session_id": "sess_other",
                "priority": 100,
            }
        ],
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_permission_rejects_run_id_collision_with_parent_scope_mismatch():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="run",
            instance_id="codex-software",
            session_id="sess_01",
            run_id="run_01",
        ),
        permissions=[
            {
                "request_id": "perm_wrong_parent",
                "instance_id": "claude-hardware",
                "session_id": "sess_other",
                "run_id": "run_01",
                "priority": 100,
            }
        ],
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_permission_rejects_session_id_match_with_instance_mismatch():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            instance_id="codex-software",
            session_id="sess_01",
        ),
        permissions=[
            {
                "request_id": "perm_wrong_instance",
                "instance_id": "claude-hardware",
                "session_id": "sess_01",
                "priority": 100,
            }
        ],
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_permission_resolves_valid_run_scope_with_matching_parents():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="run",
            instance_id="codex-software",
            session_id="sess_01",
            run_id="run_01",
        ),
        permissions=[
            {
                "request_id": "perm_run",
                "instance_id": "codex-software",
                "session_id": "sess_01",
                "run_id": "run_01",
                "priority": 100,
            }
        ],
    )

    assert result.resolved
    assert result.target["permission_id"] == "perm_run"


def test_focused_permission_resolves_valid_session_scope_with_matching_parent():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_permission",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            instance_id="codex-software",
            session_id="sess_01",
        ),
        permissions=[
            {
                "request_id": "perm_session",
                "instance_id": "codex-software",
                "session_id": "sess_01",
                "priority": 100,
            }
        ],
    )

    assert result.resolved
    assert result.target["permission_id"] == "perm_session"


def test_focused_run_requires_explicit_active_run_for_session_focus():
    resolver = TargetResolver()
    focus = ScreenFocus(device_id="kbd_01", mode="session", session_id="sess_01")
    runs = {
        "run_01": {"run_id": "run_01", "session_id": "sess_01"},
        "run_02": {"run_id": "run_02", "session_id": "sess_01"},
    }

    unresolved = resolver.resolve(
        "focused_run",
        focus=focus,
        sessions={"sess_01": {"session_id": "sess_01"}},
        runs=runs,
    )
    active = resolver.resolve(
        "focused_run",
        focus=focus,
        sessions={"sess_01": {"session_id": "sess_01", "active_run_id": "run_02"}},
        runs=runs,
    )

    assert not unresolved.resolved
    assert active.resolved
    assert active.target["run_id"] == "run_02"


def test_focused_run_rejects_run_parent_mismatch():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_run",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="run",
            instance_id="codex-software",
            session_id="sess_01",
            run_id="run_01",
        ),
        sessions={"sess_01": {"session_id": "sess_01", "instance_id": "codex-software"}},
        runs={
            "run_01": {
                "run_id": "run_01",
                "instance_id": "claude-hardware",
                "session_id": "sess_other",
            }
        },
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_session_rejects_instance_mismatch():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_session",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            instance_id="codex-software",
            session_id="sess_01",
        ),
        sessions={
            "sess_01": {
                "session_id": "sess_01",
                "instance_id": "claude-hardware",
            }
        },
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_run_rejects_active_run_from_another_session():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_run",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="session",
            session_id="sess_01",
        ),
        sessions={"sess_01": {"session_id": "sess_01", "active_run_id": "run_other"}},
        runs={"run_other": {"run_id": "run_other", "session_id": "sess_other"}},
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_run_does_not_fallback_to_active_run_when_explicit_run_conflicts():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_run",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="run",
            session_id="sess_01",
            run_id="run_conflict",
        ),
        sessions={"sess_01": {"session_id": "sess_01", "active_run_id": "run_active"}},
        runs={
            "run_conflict": {"run_id": "run_conflict", "session_id": "sess_other"},
            "run_active": {"run_id": "run_active", "session_id": "sess_01"},
        },
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_focused_session_rejects_conflicting_explicit_run_child():
    resolver = TargetResolver()

    result = resolver.resolve(
        "focused_session",
        focus=ScreenFocus(
            device_id="kbd_01",
            mode="run",
            instance_id="codex-software",
            session_id="sess_01",
            run_id="run_conflict",
        ),
        sessions={
            "sess_01": {
                "session_id": "sess_01",
                "instance_id": "codex-software",
            }
        },
        runs={
            "run_conflict": {
                "run_id": "run_conflict",
                "instance_id": "codex-software",
                "session_id": "sess_other",
            }
        },
    )

    assert not result.resolved
    assert result.code == "UNRESOLVED_TARGET"


def test_empty_snapshot_contains_focus_map():
    runtime = build_runtime()

    assert runtime.snapshot().to_dict()["focus"] == {}


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


def test_symbolic_target_fallback_syncs_snapshot_and_emits_focus_changed():
    runtime = build_runtime()
    runtime.state_store.sessions = {
        "sess_01": {"session_id": "sess_01", "instance_id": "codex-software"},
    }
    calls = []

    def downstream(command: CommandEnvelope) -> EventEnvelope:
        calls.append(command)
        return EventEnvelope(seq=0, type="downstream.called", payload={})

    runtime.keyboard_runtime.register_targeted_handlers(runtime.command_router, {
        "agent.session.close": downstream,
    })
    runtime.command_router.dispatch(_command(
        "agent.focus.set",
        target={"device_id": "kbd_01"},
        payload={
            "mode": "run",
            "instance_id": "codex-software",
            "session_id": "sess_01",
            "run_id": "run_missing",
        },
    ))
    after_initial_focus_seq = runtime.event_bus.last_seq

    event = runtime.command_router.dispatch(_command(
        "agent.session.close",
        target="focused_session",
        command_id="cmd_close_fallback_session",
    ))

    assert event.type == "downstream.called"
    assert calls[0].target == {"session_id": "sess_01", "instance_id": "codex-software"}

    focus = runtime.snapshot().to_dict()["focus"]["kbd_01"]
    assert focus["mode"] == "session"
    assert focus["target"]["session_id"] == "sess_01"
    assert focus["target"]["run_id"] is None

    fallback_events = [
        item
        for item in runtime.event_bus.events_after(after_initial_focus_seq)
        if item.type == "agent.focus.changed"
    ]
    assert len(fallback_events) == 1
    assert fallback_events[0].payload["mode"] == "session"
    assert fallback_events[0].payload["target"]["run_id"] is None
