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
    command_from_resolved_action,
    validate_profile,
)


def _profile():
    return Profile(
        id="profile_dev",
        name="Developer",
        target_device_family="ai_keyboard_ch32h417",
        layers=[
            {
                "id": "layer_fn",
                "priority": 10,
                "activation": {"type": "hold_key", "key": "K_FN"},
                "keymap": {
                    "K_ESC": {
                        "type": "agent.run.interrupt",
                        "target": {"selector": "focused_run"},
                        "reason": "manual",
                    }
                },
            }
        ],
        agent_bindings=[
            AgentBinding(
                id="approve_permission",
                trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
                action=KeyboardAction(
                    type="agent.permission.respond",
                    target="focused_permission",
                    payload={
                        "decision": "approve",
                        "profile_id": "spoofed_profile",
                        "binding_id": "spoofed_binding",
                        "key_id": "K_A",
                        "layer_id": "spoofed_layer",
                    },
                ),
            )
        ],
    )


def _event(key_id):
    return KeyboardInputEvent(
        device_id="kbd_01",
        key_id=key_id,
        event_type="press",
        active_layers=("layer_fn",),
        modifiers=("fn",),
        timestamp=123,
        sequence=7,
    )


def test_action_command_factory_preserves_symbolic_target_and_protects_metadata_fields():
    resolved = BindingResolver(_profile()).resolve(_event("K_ENTER"))[0]

    command = command_from_resolved_action(resolved, _event("K_ENTER"))

    assert command.type == "agent.permission.respond"
    assert command.source.kind == "device-transport"
    assert command.source.device_id == "kbd_01"
    assert command.target == "focused_permission"
    assert command.payload["decision"] == "approve"
    assert command.payload["profile_id"] == "profile_dev"
    assert command.payload["binding_id"] == "approve_permission"
    assert command.payload["key_id"] == "K_ENTER"
    assert command.payload["layer_id"] == "layer_fn"
    assert command.payload["event_type"] == "press"
    assert command.payload["active_layers"] == ["layer_fn"]
    assert command.payload["modifiers"] == ["fn"]
    assert command.payload["sequence"] == 7


def test_action_command_factory_preserves_dict_target_from_layer_keymap_action():
    resolved = BindingResolver(_profile()).resolve(_event("K_ESC"))[0]

    command = command_from_resolved_action(resolved, _event("K_ESC"))

    assert command.type == "agent.run.interrupt"
    assert command.source.kind == "device-transport"
    assert command.source.device_id == "kbd_01"
    assert command.target == {"selector": "focused_run"}
    assert command.payload["reason"] == "manual"
    assert command.payload["profile_id"] == "profile_dev"
    assert command.payload["binding_id"] == "layer:layer_fn:K_ESC"
    assert command.payload["key_id"] == "K_ESC"
    assert command.payload["layer_id"] == "layer_fn"


def test_profile_validation_accepts_dict_symbolic_agent_target():
    validate_profile(_profile())


def test_virtual_input_binding_can_resolve_tool_switch_command_without_metadata_spoofing():
    profile = Profile(
        id="profile_tools",
        name="Tools",
        target_device_family="ai_keyboard_ch32h417",
        keymap={
            "bindings": {
                "K_TOOL_1": {
                    "type": "keyboard.tool.switch",
                    "target": {"tool_id": "permissions"},
                    "device_id": "spoofed_device",
                    "profile_id": "spoofed_profile",
                    "binding_id": "spoofed_binding",
                },
                "K_TOOL_2": {
                    "type": "keyboard.tool.next",
                    "target": {"device_id": "spoofed_target_device"},
                },
            },
        },
    )
    validate_profile(profile)

    switch_action = BindingResolver(profile).resolve(_event("K_TOOL_1"))[0]
    next_action = BindingResolver(profile).resolve(_event("K_TOOL_2"))[0]

    switch_command = command_from_resolved_action(switch_action, _event("K_TOOL_1"))
    next_command = command_from_resolved_action(next_action, _event("K_TOOL_2"))

    assert switch_command.type == "keyboard.tool.switch"
    assert switch_command.source.kind == "device-transport"
    assert switch_command.source.device_id == "kbd_01"
    assert switch_command.target == {"tool_id": "permissions"}
    assert switch_command.payload["profile_id"] == "profile_tools"
    assert switch_command.payload["binding_id"] == "keymap:K_TOOL_1"
    assert switch_command.payload["key_id"] == "K_TOOL_1"
    assert "device_id" not in switch_command.payload

    assert next_command.type == "keyboard.tool.next"
    assert next_command.source.kind == "device-transport"
    assert next_command.source.device_id == "kbd_01"
    assert next_command.target == {"device_id": "spoofed_target_device"}
