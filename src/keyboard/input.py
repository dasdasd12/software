"""Transport-neutral keyboard input event models."""

from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class KeyboardInputEvent:
    device_id: str
    key_id: str
    event_type: str
    timestamp: Optional[int] = None
    active_layers: Tuple[str, ...] = field(default_factory=tuple)
    modifiers: Tuple[str, ...] = field(default_factory=tuple)
    sequence: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "active_layers", tuple(self.active_layers or ()))
        object.__setattr__(self, "modifiers", tuple(self.modifiers or ()))
