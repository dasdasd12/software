"""Compile keyboard profiles into the offline device subset."""

from typing import Any, Dict, Iterable, Optional

from .lighting import compile_lighting_payload
from .profile import (
    KeyboardAction,
    Profile,
    is_offline_profile_action,
    is_service_required_profile_action,
    iter_keymap_actions,
    iter_layer_actions,
    profile_action_to_dict,
    validate_profile,
)


def compile_profile_for_device(profile: Profile, device_capabilities: Optional[Any] = None) -> Dict[str, Any]:
    validate_profile(profile, device_capabilities=device_capabilities)
    keymap = _compile_keymap(profile.keymap)
    offline: Dict[str, Any] = {
        "hid": _compile_hid_bindings(keymap),
        "keymap": keymap,
        "layers": _compile_layers(profile.layers),
        "macros": [dict(item) for item in profile.macros],
    }
    lighting = compile_lighting_payload(profile.lighting_config)
    if lighting is not None:
        offline["lighting"] = lighting

    return {
        "schema_version": profile.schema_version,
        "profile_id": profile.id,
        "version": profile.version,
        "target_device_family": profile.target_device_family,
        "offline": offline,
        "service_required_actions": _compile_service_required_actions(profile),
    }


def _compile_keymap(keymap: Dict[str, Any]) -> Dict[str, Any]:
    compiled: Dict[str, Any] = {}
    for key_id, action in iter_keymap_actions(keymap):
        if is_offline_profile_action(action):
            compiled[key_id] = profile_action_to_dict(action)
    return compiled


def _compile_hid_bindings(keymap: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key_id: dict(action)
        for key_id, action in keymap.items()
        if str(action.get("type", "")).startswith("hid.")
    }


def _compile_layers(layers: Iterable[Dict[str, Any]]) -> list:
    compiled_layers = []
    for layer in layers:
        compiled_keymap = {}
        for key_id, action in iter_layer_actions(layer):
            if is_offline_profile_action(action):
                compiled_keymap[key_id] = profile_action_to_dict(action)
        compiled_layers.append({
            "id": layer["id"],
            "activation": dict(layer.get("activation") or {}),
            "keymap": compiled_keymap,
        })
    return compiled_layers


def _compile_service_required_actions(profile: Profile) -> list:
    service_required = []
    for key_id, action in iter_keymap_actions(profile.keymap):
        if is_service_required_profile_action(action):
            service_required.append(_service_required_action(action, key_id=key_id))
    for layer in profile.layers:
        layer_id = layer["id"]
        for key_id, action in iter_layer_actions(layer):
            if is_service_required_profile_action(action):
                service_required.append(_service_required_action(action, key_id=key_id, layer_id=layer_id))
    for binding in profile.agent_bindings:
        if binding.action.type.startswith("agent."):
            service_required.append({
                "binding_id": binding.id,
                "action_type": binding.action.type,
                "target": binding.action.target,
            })
    return service_required


def _service_required_action(
    action: KeyboardAction,
    key_id: Optional[str] = None,
    layer_id: Optional[str] = None,
) -> Dict[str, Any]:
    reserved_fields = {"action_type", "target", "key_id", "layer_id"}
    data: Dict[str, Any] = {
        key: value
        for key, value in action.payload.items()
        if key not in reserved_fields
    }
    data.update({
        "action_type": action.type,
        "target": action.target,
    })
    if layer_id is not None:
        data["layer_id"] = layer_id
    if key_id is not None:
        data["key_id"] = key_id
    return data
