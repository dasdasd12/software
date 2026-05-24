"""Compile keyboard profiles into the offline device subset."""

from typing import Any, Dict, Iterable, Optional

from .lighting import compile_lighting_payload
from .profile import Profile, validate_profile


def compile_profile_for_device(profile: Profile, device_capabilities: Optional[Any] = None) -> Dict[str, Any]:
    validate_profile(profile, device_capabilities=device_capabilities)
    offline: Dict[str, Any] = {
        "hid": _compile_hid_bindings(profile.keymap),
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


def _compile_hid_bindings(keymap: Dict[str, Any]) -> Dict[str, Any]:
    compiled: Dict[str, Any] = {}
    for key_id, action in _iter_binding_items(keymap):
        if _action_type(action).startswith("hid."):
            compiled[key_id] = _compile_offline_action(action)
    return compiled


def _compile_layers(layers: Iterable[Dict[str, Any]]) -> list:
    compiled_layers = []
    for layer in layers:
        compiled_keymap = {}
        for key_id, action in _iter_layer_binding_items(layer):
            if not _action_type(action).startswith("agent."):
                compiled_keymap[key_id] = _compile_offline_action(action)
        compiled_layers.append({
            "id": layer["id"],
            "activation": dict(layer.get("activation") or {}),
            "keymap": compiled_keymap,
        })
    return compiled_layers


def _compile_service_required_actions(profile: Profile) -> list:
    service_required = []
    for binding in profile.agent_bindings:
        if binding.action.type.startswith("agent."):
            service_required.append({
                "binding_id": binding.id,
                "action_type": binding.action.type,
                "target": binding.action.target,
            })
    return service_required


def _iter_binding_items(keymap: Dict[str, Any]):
    for field in ("bindings", "keys"):
        value = keymap.get(field)
        if isinstance(value, dict):
            yield from value.items()
    reserved = {"physical_layout_id", "bindings", "keys"}
    for key_id, action in keymap.items():
        if key_id not in reserved:
            yield key_id, action


def _iter_layer_binding_items(layer: Dict[str, Any]):
    for field in ("keymap", "bindings", "keys"):
        value = layer.get(field)
        if isinstance(value, dict):
            yield from value.items()


def _action_type(action: Any) -> str:
    if isinstance(action, str):
        return action
    if isinstance(action, dict):
        return str(action.get("type", ""))
    return ""


def _compile_offline_action(action: Any) -> Any:
    if isinstance(action, dict):
        return dict(action)
    return {"type": action}
