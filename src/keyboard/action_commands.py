"""Convert resolved keyboard actions into core command envelopes."""

from typing import Any, Dict

from core import CommandEnvelope, CommandSource

from .bindings import ResolvedKeyboardAction
from .input import KeyboardInputEvent


PROTECTED_PAYLOAD_FIELDS = {
    "profile_id",
    "binding_id",
    "key_id",
    "layer_id",
    "event_type",
    "active_layers",
    "modifiers",
    "sequence",
    "input_timestamp",
}


def command_from_resolved_action(
    action: ResolvedKeyboardAction,
    event: KeyboardInputEvent,
) -> CommandEnvelope:
    payload: Dict[str, Any] = {
        key: value
        for key, value in action.action.payload.items()
        if key not in PROTECTED_PAYLOAD_FIELDS
    }
    payload.update({
        "profile_id": action.profile_id,
        "binding_id": action.binding_id,
        "key_id": action.key_id,
        "event_type": event.event_type,
        "active_layers": list(event.active_layers),
        "modifiers": list(event.modifiers),
    })
    if action.layer_id is not None:
        payload["layer_id"] = action.layer_id
    if event.sequence is not None:
        payload["sequence"] = event.sequence
    if event.timestamp is not None:
        payload["input_timestamp"] = event.timestamp

    return CommandEnvelope(
        type=action.action.type,
        source=CommandSource(kind="device-transport", device_id=event.device_id),
        target=action.action.target,
        payload=payload,
    )
