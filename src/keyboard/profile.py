"""Profile and binding models from the keyboard architecture docs."""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_ACTION_PREFIXES = (
    "hid.",
    "layer.",
    "macro.",
    "profile.",
    "screen.",
    "agent.",
    "device.",
)
SUPPORTED_TRIGGER_SOURCES = {"key", "encoder", "screen_button", "system"}
SUPPORTED_TRIGGER_EVENTS = {
    "press",
    "release",
    "hold",
    "tap",
    "double_tap",
    "rotate_left",
    "rotate_right",
}
SUPPORTED_AGENT_TARGETS = {
    "focused_agent",
    "focused_session",
    "focused_run",
    "focused_permission",
    "workspace_default",
    "preferred_instance",
}
REQUIRED_FEATURE_BY_ACTION_PREFIX = {
    "hid.": "hid",
    "layer.": "layers",
    "macro.": "macros",
    "profile.": "profiles",
    "screen.": "screen",
    "agent.": "agent_bindings",
    "device.": "device",
}


class ProfileValidationError(ValueError):
    """Raised when a profile cannot be accepted by the core."""


@dataclass(frozen=True)
class BindingTrigger:
    source: str
    event: str
    key: Optional[str] = None
    layer: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"source": self.source, "event": self.event}
        if self.key:
            data["key"] = self.key
        if self.layer:
            data["layer"] = self.layer
        return data


@dataclass(frozen=True)
class KeyboardAction:
    type: str
    target: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = {"type": self.type, **self.payload}
        if self.target:
            data["target"] = self.target
        return data


@dataclass(frozen=True)
class AgentBinding:
    id: str
    trigger: BindingTrigger
    action: KeyboardAction
    safety: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "trigger": self.trigger.to_dict(),
            "action": self.action.to_dict(),
            "safety": dict(self.safety),
        }


@dataclass(frozen=True)
class MagneticConfig:
    unit: str = "mm"
    default: Dict[str, Any] = field(default_factory=dict)
    per_key: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit": self.unit,
            "default": dict(self.default),
            "per_key": {key: dict(value) for key, value in self.per_key.items()},
        }


@dataclass
class Profile:
    id: str
    name: str
    target_device_family: str
    schema_version: str = SUPPORTED_SCHEMA_VERSION
    version: int = 1
    tags: List[str] = field(default_factory=list)
    keymap: Dict[str, Any] = field(default_factory=dict)
    layers: List[Dict[str, Any]] = field(default_factory=list)
    macros: List[Dict[str, Any]] = field(default_factory=list)
    magnetic_config: MagneticConfig = field(default_factory=MagneticConfig)
    screen_layout: Dict[str, Any] = field(default_factory=dict)
    agent_bindings: List[AgentBinding] = field(default_factory=list)
    profile_policy: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "target_device_family": self.target_device_family,
            "tags": list(self.tags),
            "keymap": dict(self.keymap),
            "layers": list(self.layers),
            "macros": list(self.macros),
            "magnetic_config": self.magnetic_config.to_dict(),
            "screen_layout": dict(self.screen_layout),
            "agent_bindings": [binding.to_dict() for binding in self.agent_bindings],
            "profile_policy": dict(self.profile_policy),
            "metadata": dict(self.metadata),
        }


@dataclass
class AppConfig:
    active_profile_id: Optional[str] = None
    schema_version: str = SUPPORTED_SCHEMA_VERSION
    profiles: List[Profile] = field(default_factory=list)
    known_devices: List[Dict[str, Any]] = field(default_factory=list)
    agent_instance_presets: List[Dict[str, Any]] = field(default_factory=list)
    workspace_bindings: List[Dict[str, Any]] = field(default_factory=list)
    approval_policies: List[Dict[str, Any]] = field(default_factory=list)
    global_approval_policy_id: str = "policy_standard"
    ui_preferences: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "active_profile_id": self.active_profile_id,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "known_devices": list(self.known_devices),
            "agent_instance_presets": list(self.agent_instance_presets),
            "workspace_bindings": list(self.workspace_bindings),
            "approval_policies": list(self.approval_policies),
            "global_approval_policy_id": self.global_approval_policy_id,
            "ui_preferences": dict(self.ui_preferences),
        }


def validate_profile(
    profile: Profile,
    device_capabilities: Optional[Any] = None,
    layout_keys: Optional[Iterable[str]] = None,
) -> None:
    if profile.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ProfileValidationError(f"unsupported schema_version: {profile.schema_version}")
    if not profile.id:
        raise ProfileValidationError("profile id is required")
    if not profile.name:
        raise ProfileValidationError("profile name is required")
    if not profile.target_device_family:
        raise ProfileValidationError("target_device_family is required")
    if profile.magnetic_config.unit != "mm":
        raise ProfileValidationError("magnetic_config.unit must be mm")

    if device_capabilities and profile.target_device_family != device_capabilities.device_family:
        raise ProfileValidationError(
            f"target device family {profile.target_device_family} is incompatible with "
            f"{device_capabilities.device_family}"
        )
    if device_capabilities and profile.agent_bindings and not device_capabilities.supports_agent_slots:
        raise ProfileValidationError("device capability does not support agent slots")
    if device_capabilities and profile.agent_bindings and not device_capabilities.supports_config_sync:
        raise ProfileValidationError("device capability does not support config sync")

    known_keys = set(layout_keys or [])
    layer_ids = set()
    for layer in profile.layers:
        if not isinstance(layer, dict):
            raise ProfileValidationError("layer must be an object")
        layer_id = layer.get("id")
        if not isinstance(layer_id, str) or not layer_id:
            raise ProfileValidationError("layer id is required")
        if layer_id in layer_ids:
            raise ProfileValidationError(f"duplicate layer id: {layer_id}")
        layer_ids.add(layer_id)
        activation = layer.get("activation") or {}
        activation_key = activation.get("key") if isinstance(activation, dict) else None
        if activation_key:
            _validate_key_reference(activation_key, known_keys)

    _validate_screen_layout(profile, device_capabilities)
    for binding in profile.agent_bindings:
        if not binding.id:
            raise ProfileValidationError("agent binding id is required")
        if binding.trigger.source not in SUPPORTED_TRIGGER_SOURCES:
            raise ProfileValidationError(f"unsupported trigger source: {binding.trigger.source}")
        if binding.trigger.event not in SUPPORTED_TRIGGER_EVENTS:
            raise ProfileValidationError(f"unsupported trigger event: {binding.trigger.event}")
        if binding.trigger.key:
            _validate_key_reference(binding.trigger.key, known_keys)
        if not binding.action.type.startswith(SUPPORTED_ACTION_PREFIXES):
            raise ProfileValidationError(f"unsupported action type: {binding.action.type}")
        if binding.trigger.layer and binding.trigger.layer not in layer_ids:
            raise ProfileValidationError(f"unknown layer reference: {binding.trigger.layer}")
        if binding.action.type.startswith("agent."):
            if binding.action.target not in SUPPORTED_AGENT_TARGETS:
                raise ProfileValidationError(f"unsupported agent target: {binding.action.target}")
        _validate_action_capability(binding.action.type, device_capabilities)
        _validate_binding_safety(binding)


def _validate_key_reference(key_id: str, known_keys: set) -> None:
    if known_keys and key_id not in known_keys:
        raise ProfileValidationError(f"unknown key reference: {key_id}")


def _validate_action_capability(action_type: str, device_capabilities: Optional[Any]) -> None:
    if not device_capabilities:
        return
    features = set(device_capabilities.supported_profile_features or set())
    for prefix, feature in REQUIRED_FEATURE_BY_ACTION_PREFIX.items():
        if action_type.startswith(prefix) and feature not in features:
            raise ProfileValidationError(f"device capability missing profile feature: {feature}")


def _validate_binding_safety(binding: AgentBinding) -> None:
    if (
        binding.action.type == "agent.permission.respond"
        and binding.safety.get("allow_high_risk") is True
        and binding.safety.get("requires_screen_confirmation") is not True
    ):
        raise ProfileValidationError("high risk permission responses require screen confirmation")


def _validate_screen_layout(profile: Profile, device_capabilities: Optional[Any]) -> None:
    if not device_capabilities:
        return
    supported_widgets = set(device_capabilities.supported_screen_widgets or set())
    for page in profile.screen_layout.get("pages", []):
        if not isinstance(page, dict):
            raise ProfileValidationError("screen layout page must be an object")
        for widget in page.get("widgets", []):
            widget_type = widget.get("type") if isinstance(widget, dict) else None
            if supported_widgets and widget_type not in supported_widgets:
                raise ProfileValidationError(f"unsupported screen widget: {widget_type}")


def profile_from_dict(data: Dict[str, Any]) -> Profile:
    schema_version = data.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ProfileValidationError(f"unsupported schema_version: {schema_version}")

    magnetic = data.get("magnetic_config") or {}
    bindings = []
    for item in data.get("agent_bindings", []):
        trigger_data = item.get("trigger") or {}
        action_data = dict(item.get("action") or {})
        action_type = action_data.pop("type", "")
        action_target = action_data.pop("target", None)
        bindings.append(AgentBinding(
            id=item.get("id", ""),
            trigger=BindingTrigger(
                source=trigger_data.get("source", ""),
                event=trigger_data.get("event", ""),
                key=trigger_data.get("key"),
                layer=trigger_data.get("layer"),
            ),
            action=KeyboardAction(
                type=action_type,
                target=action_target,
                payload=action_data,
            ),
            safety=dict(item.get("safety") or {}),
        ))

    profile = Profile(
        id=data.get("id", ""),
        name=data.get("name", ""),
        target_device_family=data.get("target_device_family", ""),
        schema_version=schema_version,
        version=int(data.get("version", 1)),
        tags=list(data.get("tags", [])),
        keymap=dict(data.get("keymap") or {}),
        layers=list(data.get("layers", [])),
        macros=list(data.get("macros", [])),
        magnetic_config=MagneticConfig(
            unit=magnetic.get("unit", "mm"),
            default=dict(magnetic.get("default") or {}),
            per_key={key: dict(value) for key, value in (magnetic.get("per_key") or {}).items()},
        ),
        screen_layout=dict(data.get("screen_layout") or {}),
        agent_bindings=bindings,
        profile_policy=dict(data.get("profile_policy") or {}),
        metadata=dict(data.get("metadata") or {}),
    )
    validate_profile(profile)
    return profile


def app_config_from_dict(data: Dict[str, Any]) -> AppConfig:
    schema_version = data.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ProfileValidationError(f"unsupported schema_version: {schema_version}")
    return AppConfig(
        active_profile_id=data.get("active_profile_id"),
        schema_version=schema_version,
        profiles=[profile_from_dict(item) for item in data.get("profiles", [])],
        known_devices=list(data.get("known_devices", [])),
        agent_instance_presets=list(data.get("agent_instance_presets", [])),
        workspace_bindings=list(data.get("workspace_bindings", [])),
        approval_policies=list(data.get("approval_policies", [])),
        global_approval_policy_id=data.get("global_approval_policy_id", "policy_standard"),
        ui_preferences=dict(data.get("ui_preferences") or {}),
    )


def export_profile_json(profile: Profile) -> str:
    validate_profile(profile)
    return json.dumps(profile.to_dict(), ensure_ascii=False, sort_keys=True)


def import_profile_json(raw: str) -> Profile:
    return profile_from_dict(json.loads(raw))


def export_app_config_json(config: AppConfig) -> str:
    parsed = app_config_from_dict(config.to_dict())
    return json.dumps(parsed.to_dict(), ensure_ascii=False, sort_keys=True)


def import_app_config_json(raw: str) -> AppConfig:
    return app_config_from_dict(json.loads(raw))
