from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from keyboard import (  # noqa: E402
    AgentBinding,
    BindingResolver,
    BindingTrigger,
    KeyboardAction,
    KeyboardInputEvent,
    Profile,
)


def _profile(**overrides):
    data = {
        "id": "profile_dev",
        "name": "Developer",
        "target_device_family": "ai_keyboard_ch32h417",
        "keymap": {
            "physical_layout_id": "ansi_75_ai_keyboard",
            "bindings": {
                "K_LAUNCH": {
                    "type": "agent.session.launch_or_resume",
                    "target": "active_agent",
                    "session_id": "new",
                },
            },
        },
        "layers": [
            {
                "id": "layer_fn",
                "priority": 10,
                "activation": {"type": "hold_key", "key": "K_FN"},
                "keymap": {},
            },
            {
                "id": "layer_agent",
                "priority": 20,
                "activation": {"type": "hold_key", "key": "K_TOOL_1"},
                "keymap": {},
            },
        ],
        "agent_bindings": [
            AgentBinding(
                id="approve_permission",
                trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
                action=KeyboardAction(
                    type="agent.permission.respond",
                    target="focused_permission",
                    payload={"decision": "approve"},
                ),
            ),
            AgentBinding(
                id="interrupt_run",
                trigger=BindingTrigger(source="key", key="K_ESC", event="press", layer="layer_fn"),
                action=KeyboardAction(type="agent.run.interrupt", target="focused_run"),
            ),
        ],
    }
    data.update(overrides)
    return Profile(**data)


def _event(key_id, event_type="press", active_layers=("layer_fn",)):
    return KeyboardInputEvent(
        device_id="kbd_01",
        key_id=key_id,
        event_type=event_type,
        active_layers=active_layers,
        timestamp=123,
        sequence=7,
    )


def test_fn_enter_press_resolves_to_permission_approval_action():
    actions = BindingResolver(_profile()).resolve(_event("K_ENTER"))

    assert len(actions) == 1
    assert actions[0].binding_id == "approve_permission"
    assert actions[0].action.type == "agent.permission.respond"
    assert actions[0].action.target == "focused_permission"
    assert actions[0].action.payload == {"decision": "approve"}
    assert actions[0].key_id == "K_ENTER"
    assert actions[0].layer_id == "layer_fn"
    assert actions[0].profile_id == "profile_dev"


def test_fn_esc_press_resolves_to_interrupt_focused_run_action():
    actions = BindingResolver(_profile()).resolve(_event("K_ESC"))

    assert len(actions) == 1
    assert actions[0].binding_id == "interrupt_run"
    assert actions[0].action.type == "agent.run.interrupt"
    assert actions[0].action.target == "focused_run"


def test_launch_key_resolves_embedded_keymap_agent_action():
    actions = BindingResolver(_profile()).resolve(_event("K_LAUNCH", active_layers=()))

    assert len(actions) == 1
    assert actions[0].binding_id == "keymap:K_LAUNCH"
    assert actions[0].action.type == "agent.session.launch_or_resume"
    assert actions[0].action.target == "active_agent"
    assert actions[0].action.payload == {"session_id": "new"}
    assert actions[0].layer_id is None


def test_highest_priority_matching_layer_wins_and_order_is_deterministic():
    profile = _profile(
        agent_bindings=[
            AgentBinding(
                id="base_interrupt",
                trigger=BindingTrigger(source="key", key="K_ESC", event="press"),
                action=KeyboardAction(type="agent.run.interrupt", target="focused_session"),
            ),
            AgentBinding(
                id="fn_interrupt",
                trigger=BindingTrigger(source="key", key="K_ESC", event="press", layer="layer_fn"),
                action=KeyboardAction(type="agent.run.interrupt", target="focused_run"),
            ),
            AgentBinding(
                id="agent_close_first",
                trigger=BindingTrigger(source="key", key="K_ESC", event="press", layer="layer_agent"),
                action=KeyboardAction(type="agent.session.close", target="focused_session"),
            ),
            AgentBinding(
                id="agent_close_second",
                trigger=BindingTrigger(source="key", key="K_ESC", event="press", layer="layer_agent"),
                action=KeyboardAction(type="agent.session.close", target="focused_session"),
            ),
        ]
    )

    actions = BindingResolver(profile).resolve(_event("K_ESC", active_layers=("layer_fn", "layer_agent")))

    assert [action.binding_id for action in actions] == ["agent_close_first", "agent_close_second"]
    assert {action.layer_id for action in actions} == {"layer_agent"}


def test_release_does_not_trigger_press_binding():
    actions = BindingResolver(_profile()).resolve(_event("K_ENTER", event_type="release"))

    assert actions == []


def test_no_match_returns_empty_action_list():
    actions = BindingResolver(_profile()).resolve(_event("K_A", active_layers=()))

    assert actions == []
