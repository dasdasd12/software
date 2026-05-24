import json
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices import DeviceCapabilities  # noqa: E402
from keyboard import (  # noqa: E402
    AgentBinding,
    BindingTrigger,
    KeyboardAction,
    LightingConfig,
    LightingLayer,
    Profile,
    ProfileValidationError,
    compile_profile_for_device,
    export_profile_json,
    get_default_physical_layout,
    import_profile_json,
    validate_profile,
)
from keyboard.profile_service import ProfileService  # noqa: E402


def _capabilities(features):
    return DeviceCapabilities(
        device_id="kbd_01",
        transport_kind="simulated",
        protocol_version=1,
        max_payload_size=2048,
        supported_message_types={"PROFILE_SYNC_BEGIN", "PROFILE_SYNC_CHUNK", "PROFILE_SYNC_END"},
        device_family="ai_keyboard_ch32h417",
        supported_profile_features=set(features),
        supports_agent_slots=True,
        supports_config_sync=True,
    )


def _profile(**overrides):
    data = {
        "id": "profile_dev",
        "name": "Developer",
        "target_device_family": "ai_keyboard_ch32h417",
        "keymap": {
            "physical_layout_id": "ansi_75_ai_keyboard",
            "bindings": {
                "K_A": {"type": "hid.key", "usage": "KEY_A"},
                "K_MACRO_1": {"type": "macro.play", "macro_id": "build"},
                "K_LAUNCH": {"type": "profile.switch", "profile_id": "profile_ops"},
            },
        },
        "layers": [
            {
                "id": "layer_fn",
                "activation": {"type": "hold_key", "key": "K_FN"},
                "keymap": {
                    "K_ENTER": {"type": "hid.key", "usage": "KEY_ENTER"},
                    "K_TOOL_1": {"type": "profile.switch", "profile_id": "profile_ops"},
                },
            }
        ],
        "macros": [{"id": "build", "sequence": [{"type": "hid.key", "usage": "KEY_B"}]}],
        "agent_bindings": [
            AgentBinding(
                id="approve",
                trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
                action=KeyboardAction(
                    type="agent.permission.respond",
                    target="focused_permission",
                    payload={"decision": "approve"},
                ),
                safety={"allow_high_risk": False},
            )
        ],
        "lighting_config": LightingConfig(
            brightness=70,
            layers=[
                LightingLayer(
                    id="base",
                    effect="static",
                    color="#2ad4ff",
                    per_key={"K_ENTER": {"color": "#ffffff"}, "K_TOOL_1": {"color": "#ffcc00"}},
                )
            ],
        ),
    }
    data.update(overrides)
    return Profile(**data)


def test_default_physical_layout_contains_core_agent_keys():
    layout = get_default_physical_layout()

    assert layout.layout_id == "ansi_75_ai_keyboard"
    assert {
        "K_FN",
        "K_ENTER",
        "K_ESC",
        "K_TAB",
        "K_CAPS_LOCK",
        "K_SPACE",
        "K_BACKSPACE",
        "K_DELETE",
        "K_HOME",
        "K_END",
        "K_PAGE_UP",
        "K_PAGE_DOWN",
        "K_UP",
        "K_DOWN",
        "K_LEFT",
        "K_RIGHT",
        "K_LAUNCH",
        "K_TOOL_1",
        "K_TOOL_2",
    }.issubset(layout.key_ids)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda profile: profile.layers[0]["keymap"].__setitem__("K_UNKNOWN", {"type": "hid.key"}), "unknown key reference"),
        (
            lambda profile: profile.agent_bindings.__setitem__(
                0,
                AgentBinding(
                    id="bad",
                    trigger=BindingTrigger(source="key", key="K_UNKNOWN", event="press"),
                    action=KeyboardAction(type="agent.run.interrupt", target="focused_run"),
                ),
            ),
            "unknown key reference",
        ),
        (lambda profile: profile.magnetic_config.per_key.__setitem__("K_UNKNOWN", {"actuation": 1.2}), "unknown key reference"),
        (lambda profile: profile.lighting_config.layers[0].per_key.__setitem__("K_UNKNOWN", {"color": "#ff0000"}), "unknown key reference"),
    ],
)
def test_profile_validation_rejects_unknown_key_references(mutation, message):
    profile = _profile()
    mutation(profile)

    with pytest.raises(ProfileValidationError, match=message):
        validate_profile(profile)


@pytest.mark.parametrize("brightness", [-1, 101])
def test_lighting_brightness_must_be_between_zero_and_one_hundred(brightness):
    profile = _profile(lighting_config=LightingConfig(brightness=brightness))

    with pytest.raises(ProfileValidationError, match="brightness"):
        validate_profile(profile)


def test_device_without_lighting_feature_rejects_lighting_profile_sync():
    profile = _profile()
    service = ProfileService()
    service.upsert(profile)

    with pytest.raises(ProfileValidationError, match="lighting"):
        service.validate_for_sync("profile_dev", _capabilities({"hid", "layers", "macros", "profiles", "agent_bindings"}))


@pytest.mark.parametrize(
    "patch",
    [
        lambda profile: profile.keymap["bindings"].__setitem__("K_A", {"type": "unknown.action"}),
        lambda profile: profile.layers[0]["keymap"].__setitem__("K_ENTER", {"type": "unknown.action"}),
    ],
)
def test_profile_validation_rejects_unknown_keymap_and_layer_actions(patch):
    profile = _profile()
    patch(profile)

    with pytest.raises(ProfileValidationError, match="unsupported action type"):
        validate_profile(profile)


def test_keymap_agent_action_requires_device_agent_capability():
    profile = _profile(keymap={
        "physical_layout_id": "ansi_75_ai_keyboard",
        "bindings": {"K_A": {"type": "agent.run.interrupt", "target": "focused_run"}},
    }, agent_bindings=[])

    with pytest.raises(ProfileValidationError, match="agent_bindings"):
        compile_profile_for_device(profile, _capabilities({"hid", "layers", "macros", "profiles", "lighting"}))


def test_agent_actions_in_keymap_and_layers_are_service_required():
    profile = _profile(
        keymap={
            "physical_layout_id": "ansi_75_ai_keyboard",
            "bindings": {"K_ESC": {"type": "agent.run.interrupt", "target": "focused_run"}},
        },
        layers=[
            {
                "id": "layer_fn",
                "activation": {"type": "hold_key", "key": "K_FN"},
                "keymap": {
                    "K_ENTER": {
                        "type": "agent.permission.respond",
                        "target": "focused_permission",
                        "decision": "approve",
                        "risk_ack": "low",
                    }
                },
            }
        ],
        agent_bindings=[],
    )

    compiled = compile_profile_for_device(
        profile,
        _capabilities({"hid", "layers", "macros", "profiles", "lighting", "agent_bindings"}),
    )

    assert compiled["offline"]["keymap"] == {}
    assert compiled["offline"]["layers"][0]["keymap"] == {}
    assert compiled["service_required_actions"] == [
        {
            "key_id": "K_ESC",
            "action_type": "agent.run.interrupt",
            "target": "focused_run",
        },
        {
            "layer_id": "layer_fn",
            "key_id": "K_ENTER",
            "action_type": "agent.permission.respond",
            "target": "focused_permission",
            "decision": "approve",
            "risk_ack": "low",
        },
    ]


def test_profile_json_round_trip_preserves_lighting_and_key_bindings():
    profile = _profile()

    restored = import_profile_json(export_profile_json(profile))

    assert restored == profile
    payload = json.loads(export_profile_json(restored))
    assert payload["lighting_config"]["brightness"] == 70
    assert payload["lighting_config"]["layers"][0]["per_key"]["K_TOOL_1"]["color"] == "#ffcc00"
    assert payload["agent_bindings"][0]["trigger"]["key"] == "K_ENTER"
    assert payload["keymap"]["bindings"]["K_MACRO_1"]["macro_id"] == "build"


def test_lighting_json_rejects_non_object_config_without_attribute_error():
    payload = _profile().to_dict()
    payload["lighting_config"] = []

    with pytest.raises(ProfileValidationError, match="lighting_config"):
        import_profile_json(json.dumps(payload))


def test_lighting_json_rejects_non_boolean_enabled_value():
    payload = _profile().to_dict()
    payload["lighting_config"]["enabled"] = "false"

    with pytest.raises(ProfileValidationError, match="enabled"):
        import_profile_json(json.dumps(payload))


@pytest.mark.parametrize(
    "lighting_config",
    [
        {"per_key": {"K_A": 1}},
        {"layers": [{"id": "base", "per_key": {"K_A": 1}}]},
    ],
)
def test_lighting_json_rejects_bad_per_key_override_shapes(lighting_config):
    payload = _profile().to_dict()
    payload["lighting_config"] = lighting_config

    with pytest.raises(ProfileValidationError, match="per_key"):
        import_profile_json(json.dumps(payload))


def test_compiler_outputs_offline_subset_and_service_required_actions():
    profile = _profile()

    compiled = compile_profile_for_device(
        profile,
        _capabilities({"hid", "layers", "macros", "lighting", "agent_bindings", "profiles"}),
    )

    assert compiled["profile_id"] == "profile_dev"
    assert compiled["offline"]["hid"]["K_A"]["usage"] == "KEY_A"
    assert compiled["offline"]["keymap"]["K_MACRO_1"]["macro_id"] == "build"
    assert compiled["offline"]["keymap"]["K_LAUNCH"]["profile_id"] == "profile_ops"
    assert compiled["offline"]["layers"][0]["keymap"]["K_ENTER"]["usage"] == "KEY_ENTER"
    assert compiled["offline"]["layers"][0]["keymap"]["K_TOOL_1"]["profile_id"] == "profile_ops"
    assert compiled["offline"]["macros"] == profile.macros
    assert compiled["offline"]["lighting"]["brightness"] == 70
    assert compiled["service_required_actions"] == [
        {
            "binding_id": "approve",
            "action_type": "agent.permission.respond",
            "target": "focused_permission",
        }
    ]
