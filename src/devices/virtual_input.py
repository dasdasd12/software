"""Virtual device ingress helpers for simulated input frames."""

from typing import Any, Iterable, Optional, Tuple

from keyboard import KeyboardInputEvent

from .device_transport import DeviceFrame, DeviceTransportError
from .protocol_codec import DeviceProtocolCodec
from .slot_mapper import DeviceSlotMapper


INPUT_EVENT_FRAME_TYPE = "INPUT_EVENT"
ERROR_RESPONSE_FRAME_TYPE = "ERROR_RESP"


def decode_input_event(
    frame: DeviceFrame,
    codec: DeviceProtocolCodec,
    mapper: Optional[DeviceSlotMapper] = None,
) -> KeyboardInputEvent:
    """Decode an INPUT_EVENT frame into a keyboard input event."""
    if frame.frame_type != INPUT_EVENT_FRAME_TYPE:
        raise DeviceTransportError(
            code="UNKNOWN_FRAME_TYPE",
            message=f"unsupported device frame type: {frame.frame_type}",
            transport_kind="virtual_input",
            device_id=frame.device_id,
            frame_type=frame.frame_type,
            recoverable=True,
        )
    if mapper is not None and frame.generation != mapper.generation:
        raise DeviceTransportError(
            code="UNKNOWN_SLOT_GENERATION",
            message="slot generation mismatch",
            transport_kind="device_slot_mapper",
            device_id=frame.device_id,
            frame_type=frame.frame_type,
            recoverable=True,
            details={
                "expected_generation": mapper.generation,
                "received_generation": frame.generation,
            },
        )

    payload = codec.decode_message(frame)
    key_id = _required_str(payload, "key_id", frame)
    event_type = _required_str(payload, "event_type", frame)
    return KeyboardInputEvent(
        device_id=frame.device_id,
        key_id=key_id,
        event_type=event_type,
        active_layers=_tuple_of_strings(payload.get("active_layers", ()), "active_layers", frame),
        modifiers=_tuple_of_strings(payload.get("modifiers", ()), "modifiers", frame),
        timestamp=_optional_int(payload.get("timestamp"), "timestamp", frame),
        sequence=_optional_int(payload.get("sequence"), "sequence", frame),
    )


def error_frame_for_exception(
    error: DeviceTransportError,
    codec: DeviceProtocolCodec,
    frame: Optional[DeviceFrame] = None,
    *,
    device_id: Optional[str] = None,
    generation: Optional[int] = None,
) -> DeviceFrame:
    """Encode a structured recoverable transport error as ERROR_RESP."""
    response_device_id = device_id or error.device_id or (frame.device_id if frame else "unknown-device")
    response_generation = generation if generation is not None else (frame.generation if frame else 0)
    return codec.encode_message(
        frame_type=ERROR_RESPONSE_FRAME_TYPE,
        payload=error.to_dict(),
        device_id=response_device_id,
        generation=response_generation,
    )


def unknown_frame_error(frame: DeviceFrame) -> DeviceTransportError:
    return DeviceTransportError(
        code="UNKNOWN_FRAME_TYPE",
        message=f"unsupported device frame type: {frame.frame_type}",
        transport_kind="virtual_input",
        device_id=frame.device_id,
        frame_type=frame.frame_type,
        recoverable=True,
    )


def _required_str(payload: dict, field: str, frame: DeviceFrame) -> str:
    value = payload.get(field)
    if isinstance(value, str) and value:
        return value
    raise DeviceTransportError(
        code="INVALID_INPUT_EVENT",
        message=f"input event {field} is required",
        transport_kind="virtual_input",
        device_id=frame.device_id,
        frame_type=frame.frame_type,
        recoverable=True,
        details={"field": field},
    )


def _tuple_of_strings(value: Any, field: str, frame: DeviceFrame) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        raise DeviceTransportError(
            code="INVALID_INPUT_EVENT",
            message=f"input event {field} must be an array of strings",
            transport_kind="virtual_input",
            device_id=frame.device_id,
            frame_type=frame.frame_type,
            recoverable=True,
            details={"field": field},
        )
    items = tuple(value)
    result = tuple(item for item in items if isinstance(item, str) and item)
    if len(result) != len(items):
        raise DeviceTransportError(
            code="INVALID_INPUT_EVENT",
            message=f"input event {field} must contain only strings",
            transport_kind="virtual_input",
            device_id=frame.device_id,
            frame_type=frame.frame_type,
            recoverable=True,
            details={"field": field},
        )
    return result


def _optional_int(value: Any, field: str, frame: DeviceFrame) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DeviceTransportError(
            code="INVALID_INPUT_EVENT",
            message=f"input event {field} must be an integer",
            transport_kind="virtual_input",
            device_id=frame.device_id,
            frame_type=frame.frame_type,
            recoverable=True,
            details={"field": field},
        ) from exc
