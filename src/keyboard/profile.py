"""Profile and binding models from the keyboard architecture docs."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
            "global_approval_policy_id": self.global_approval_policy_id,
            "ui_preferences": dict(self.ui_preferences),
        }


def validate_profile(profile: Profile) -> None:
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

    layer_ids = {layer.get("id") for layer in profile.layers if isinstance(layer, dict)}
    for binding in profile.agent_bindings:
        if not binding.id:
            raise ProfileValidationError("agent binding id is required")
        if not binding.action.type.startswith(SUPPORTED_ACTION_PREFIXES):
            raise ProfileValidationError(f"unsupported action type: {binding.action.type}")
        if binding.trigger.layer and binding.trigger.layer not in layer_ids:
            raise ProfileValidationError(f"unknown layer reference: {binding.trigger.layer}")
