"""Lighting profile model and device payload helpers."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


class LightingConfigParseError(ValueError):
    """Raised when lighting JSON cannot be parsed into the typed model."""


@dataclass(frozen=True)
class LightingLayer:
    id: str
    effect: str = "static"
    color: Optional[str] = None
    speed: Optional[int] = None
    per_key: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "effect": self.effect,
            "per_key": {key: dict(value) for key, value in self.per_key.items()},
        }
        if self.color is not None:
            data["color"] = self.color
        if self.speed is not None:
            data["speed"] = self.speed
        return data


@dataclass(frozen=True)
class LightingConfig:
    brightness: int = 100
    enabled: bool = True
    layers: List[LightingLayer] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "brightness": self.brightness,
            "layers": [layer.to_dict() for layer in self.layers],
        }


def lighting_layer_from_dict(data: Dict[str, Any]) -> LightingLayer:
    if not isinstance(data, dict):
        raise LightingConfigParseError("lighting_config.layers items must be objects")
    per_key = _parse_per_key(
        data["per_key"] if "per_key" in data else {},
        "lighting_config.layers.per_key",
    )
    return LightingLayer(
        id=data.get("id", ""),
        effect=data.get("effect", "static"),
        color=data.get("color"),
        speed=data.get("speed"),
        per_key=per_key,
    )


def lighting_config_from_dict(data: Optional[Dict[str, Any]]) -> Optional[LightingConfig]:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise LightingConfigParseError("lighting_config must be an object")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise LightingConfigParseError("lighting_config.enabled must be a boolean")
    if "per_key" in data:
        _parse_per_key(data["per_key"], "lighting_config.per_key")
    layers = data.get("layers", [])
    if not isinstance(layers, list):
        raise LightingConfigParseError("lighting_config.layers must be a list")
    try:
        brightness = int(data.get("brightness", 100))
    except (TypeError, ValueError) as exc:
        raise LightingConfigParseError("lighting_config.brightness must be an integer") from exc
    return LightingConfig(
        enabled=enabled,
        brightness=brightness,
        layers=[lighting_layer_from_dict(item) for item in layers],
    )


def _parse_per_key(data: Dict[str, Any], field_name: str) -> Dict[str, Dict[str, Any]]:
    if not isinstance(data, dict):
        raise LightingConfigParseError(f"{field_name} must be an object")
    parsed = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            raise LightingConfigParseError(f"{field_name} values must be objects")
        parsed[key] = dict(value)
    return parsed


def iter_lighting_key_references(config: Optional[LightingConfig]) -> Iterable[str]:
    if config is None:
        return []
    key_ids = []
    for layer in config.layers:
        key_ids.extend(layer.per_key.keys())
    return key_ids


def compile_lighting_payload(config: Optional[LightingConfig]) -> Optional[Dict[str, Any]]:
    if config is None:
        return None
    return config.to_dict()
