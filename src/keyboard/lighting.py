"""Lighting profile model and device payload helpers."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


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
    return LightingLayer(
        id=data.get("id", ""),
        effect=data.get("effect", "static"),
        color=data.get("color"),
        speed=data.get("speed"),
        per_key={key: dict(value) for key, value in (data.get("per_key") or {}).items()},
    )


def lighting_config_from_dict(data: Optional[Dict[str, Any]]) -> Optional[LightingConfig]:
    if data is None:
        return None
    return LightingConfig(
        enabled=bool(data.get("enabled", True)),
        brightness=int(data.get("brightness", 100)),
        layers=[lighting_layer_from_dict(item) for item in data.get("layers", [])],
    )


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
