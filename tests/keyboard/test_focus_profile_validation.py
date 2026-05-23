from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices import DeviceCapabilities  # noqa: E402
from keyboard import (  # noqa: E402
    AgentBinding,
    BindingTrigger,
    FocusManager,
    KeyboardAction,
    NotificationQueue,
    PermissionRequest,
    Profile,
    ProfileValidationError,
    ScreenFocus,
    validate_profile,
)


def test_focus_falls_back_from_missing_run_to_session_then_dashboard():
    manager = FocusManager()
    manager.set_focus(ScreenFocus(
        device_id="kbd_01",
        mode="run",
        instance_id="codex-software",
        session_id="sess_01",
        run_id="run_missing",
    ))

    session_focus = manager.resolve_focus(
        "kbd_01",
        existing_instances={"codex-software"},
        existing_sessions={"sess_01"},
        existing_runs=set(),
    )
    assert session_focus.mode == "session"
    assert session_focus.session_id == "sess_01"
    assert session_focus.run_id is None

    dashboard_focus = manager.resolve_focus(
        "kbd_01",
        existing_instances=set(),
        existing_sessions=set(),
        existing_runs=set(),
    )
    assert dashboard_focus.mode == "global_dashboard"
    assert dashboard_focus.instance_id is None


def test_focused_permission_resolution_prefers_run_then_session_then_instance_then_priority():
    queue = NotificationQueue()
    queue.enqueue_permission(PermissionRequest(
        permission_id="perm_instance",
        priority=100,
        instance_id="codex-software",
    ))
    queue.enqueue_permission(PermissionRequest(
        permission_id="perm_session",
        priority=10,
        instance_id="codex-software",
        session_id="sess_01",
    ))
    queue.enqueue_permission(PermissionRequest(
        permission_id="perm_run",
        priority=1,
        instance_id="codex-software",
        session_id="sess_01",
        run_id="run_01",
    ))
    queue.enqueue_permission(PermissionRequest(
        permission_id="perm_global",
        priority=200,
    ))

    assert queue.resolve_focused_permission(ScreenFocus(
        device_id="kbd_01",
        mode="run",
        instance_id="codex-software",
        session_id="sess_01",
        run_id="run_01",
    )).permission_id == "perm_run"

    queue.dismiss("perm_run")
    assert queue.resolve_focused_permission(ScreenFocus(
        device_id="kbd_01",
        mode="session",
        instance_id="codex-software",
        session_id="sess_01",
    )).permission_id == "perm_session"

    queue.dismiss("perm_session")
    assert queue.resolve_focused_permission(ScreenFocus(
        device_id="kbd_01",
        mode="instance",
        instance_id="codex-software",
    )).permission_id == "perm_instance"

    queue.dismiss("perm_instance")
    assert queue.resolve_focused_permission(ScreenFocus(
        device_id="kbd_01",
        mode="global_dashboard",
    )).permission_id == "perm_global"


def test_permission_request_creates_pending_notification():
    queue = NotificationQueue()

    queue.enqueue_permission(PermissionRequest(
        permission_id="perm_01",
        priority=20,
        session_id="sess_01",
        risk="medium",
    ))

    notifications = queue.pending_notifications()
    assert len(notifications) == 1
    assert notifications[0].notification_id == "perm_01"
    assert notifications[0].level == "permission"


def test_profile_validation_checks_layout_actions_safety_and_device_capabilities():
    capabilities = DeviceCapabilities(
        device_id="kbd_01",
        transport_kind="simulated",
        protocol_version=1,
        max_payload_size=1024,
        supported_message_types={"PROFILE_SYNC_BEGIN"},
        device_family="ai_keyboard_ch32h417",
        supported_profile_features={"hid", "layers", "agent_bindings"},
        supported_screen_widgets={"agent_session_card"},
        supports_agent_slots=True,
        supports_config_sync=True,
    )
    profile = Profile(
        id="profile_dev",
        name="Developer",
        target_device_family="ai_keyboard_ch32h417",
        keymap={"physical_layout_id": "ansi_75_ai_keyboard"},
        layers=[{"id": "layer_fn", "activation": {"type": "hold_key", "key": "K_FN"}}],
        screen_layout={"pages": [{"id": "main", "widgets": [{"id": "w1", "type": "agent_session_card"}]}]},
        agent_bindings=[AgentBinding(
            id="approve",
            trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
            action=KeyboardAction(type="agent.permission.respond", target="focused_permission", payload={"decision": "approve"}),
            safety={"allow_high_risk": False, "requires_screen_confirmation": True},
        )],
    )

    validate_profile(profile, device_capabilities=capabilities, layout_keys={"K_FN", "K_ENTER"})

    bad_key = Profile(
        id="bad_key",
        name="Bad Key",
        target_device_family="ai_keyboard_ch32h417",
        keymap={"physical_layout_id": "ansi_75_ai_keyboard"},
        layers=[{"id": "layer_fn"}],
        agent_bindings=[AgentBinding(
            id="approve",
            trigger=BindingTrigger(source="key", key="K_UNKNOWN", event="press", layer="layer_fn"),
            action=KeyboardAction(type="agent.permission.respond", target="focused_permission", payload={"decision": "approve"}),
            safety={"allow_high_risk": False},
        )],
    )
    with pytest.raises(ProfileValidationError, match="unknown key reference"):
        validate_profile(bad_key, device_capabilities=capabilities, layout_keys={"K_FN", "K_ENTER"})

    unsupported_widget = Profile(
        id="bad_widget",
        name="Bad Widget",
        target_device_family="ai_keyboard_ch32h417",
        screen_layout={"pages": [{"id": "main", "widgets": [{"id": "w1", "type": "notification_strip"}]}]},
    )
    with pytest.raises(ProfileValidationError, match="unsupported screen widget"):
        validate_profile(unsupported_widget, device_capabilities=capabilities)

    unsafe = Profile(
        id="unsafe",
        name="Unsafe",
        target_device_family="ai_keyboard_ch32h417",
        layers=[{"id": "layer_fn"}],
        agent_bindings=[AgentBinding(
            id="approve",
            trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
            action=KeyboardAction(type="agent.permission.respond", target="focused_permission", payload={"decision": "approve"}),
            safety={"allow_high_risk": True, "requires_screen_confirmation": False},
        )],
    )
    with pytest.raises(ProfileValidationError, match="high risk"):
        validate_profile(unsafe, device_capabilities=capabilities, layout_keys={"K_ENTER"})
