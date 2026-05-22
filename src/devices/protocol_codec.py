"""Temporary device protocol payload codec.

The product protocol will become compact and likely binary. This codec keeps
the boundary explicit today by wrapping bounded JSON payloads in DeviceFrame
objects instead of reusing Local API WebSocket messages directly.
"""

import json
from typing import Any, Dict

from .device_transport import DeviceFrame


class DeviceProtocolCodec:
    def encode_message(
        self,
        frame_type: str,
        payload: Dict[str, Any],
        device_id: str,
        generation: int = 0,
    ) -> DeviceFrame:
        return DeviceFrame(
            frame_type=frame_type,
            payload=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            generation=generation,
            device_id=device_id,
        )

    def decode_message(self, frame: DeviceFrame) -> Dict[str, Any]:
        payload = json.loads(frame.payload.decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("device protocol payload must be an object")
        return payload
