"""Transport-neutral JSON schemas for local API messages."""

import json
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class LocalApiEnvelope:
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, **self.payload}, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "LocalApiEnvelope":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("local API message must be an object")
        message_type = data.pop("type", None)
        if not isinstance(message_type, str) or not message_type:
            raise ValueError("local API message type is required")
        return cls(type=message_type, payload=data)


@dataclass(frozen=True)
class LocalApiError:
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "type": "error",
            "code": self.code,
            "message": self.message,
        }
