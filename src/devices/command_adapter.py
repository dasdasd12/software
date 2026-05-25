"""Adapt virtual device input frames to keyboard commands."""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from core import CommandEnvelope, CommandRouter, EventEnvelope
from keyboard import BindingResolver, KeyboardInputEvent, Profile, command_from_resolved_action

from .device_transport import DeviceFrame, DeviceTransportError
from .protocol_codec import DeviceProtocolCodec
from .slot_mapper import DeviceSlotMapper
from .virtual_input import (
    INPUT_EVENT_FRAME_TYPE,
    decode_input_event,
    error_frame_for_exception,
)

ActiveProfileProvider = Callable[[str], Optional[Profile]]
CommandDispatcher = Callable[[CommandEnvelope], Awaitable[EventEnvelope]]


@dataclass(frozen=True)
class VirtualDeviceCommandResult:
    input_event: Optional[KeyboardInputEvent] = None
    commands: List[CommandEnvelope] = field(default_factory=list)
    events: List[EventEnvelope] = field(default_factory=list)
    ack_frame: Optional[DeviceFrame] = None
    error_frame: Optional[DeviceFrame] = None

    @property
    def response_frames(self) -> List[DeviceFrame]:
        return [frame for frame in (self.error_frame, self.ack_frame) if frame is not None]


class VirtualDeviceCommandAdapter:
    """Resolve INPUT_EVENT frames through the active profile and dispatch commands."""

    def __init__(
        self,
        active_profile_provider: ActiveProfileProvider,
        router: CommandRouter,
        codec: Optional[DeviceProtocolCodec] = None,
        slot_mapper: Optional[DeviceSlotMapper] = None,
        command_dispatcher: Optional[CommandDispatcher] = None,
    ) -> None:
        self._active_profile_provider = active_profile_provider
        self._router = router
        self._codec = codec or DeviceProtocolCodec()
        self._slot_mapper = slot_mapper
        self._command_dispatcher = command_dispatcher

    def set_slot_mapper(self, slot_mapper: DeviceSlotMapper) -> None:
        self._slot_mapper = slot_mapper

    async def handle_frame(
        self,
        frame: DeviceFrame,
        command_dispatcher: Optional[CommandDispatcher] = None,
    ) -> VirtualDeviceCommandResult:
        try:
            if frame.frame_type != INPUT_EVENT_FRAME_TYPE:
                raise DeviceTransportError(
                    code="UNKNOWN_FRAME_TYPE",
                    message=f"unsupported device frame type: {frame.frame_type}",
                    transport_kind="virtual_device_command_adapter",
                    device_id=frame.device_id,
                    frame_type=frame.frame_type,
                    recoverable=True,
                )
            event = decode_input_event(frame, self._codec, self._slot_mapper)
        except DeviceTransportError as exc:
            return VirtualDeviceCommandResult(
                error_frame=error_frame_for_exception(
                    exc,
                    self._codec,
                    frame,
                    generation=self._slot_mapper.generation if self._slot_mapper else frame.generation,
                )
            )

        profile = self._active_profile_provider(frame.device_id)
        if profile is None:
            return VirtualDeviceCommandResult(input_event=event, ack_frame=self._ack_frame(frame, 0))

        resolved_actions = BindingResolver(profile).resolve(event)
        commands = [
            command_from_resolved_action(action, event)
            for action in resolved_actions
        ]
        events = []
        dispatcher = command_dispatcher or self._command_dispatcher
        for command in commands:
            if dispatcher is not None:
                events.append(await dispatcher(command))
            else:
                events.append(await self._router.dispatch_async(command))

        return VirtualDeviceCommandResult(
            input_event=event,
            commands=commands,
            events=events,
            ack_frame=self._ack_frame(frame, len(events)),
        )

    def _ack_frame(self, frame: DeviceFrame, dispatched: int) -> DeviceFrame:
        return self._codec.encode_message(
            frame_type="ACK_RESP",
            payload={"status": "ok", "dispatched": dispatched},
            device_id=frame.device_id,
            generation=self._slot_mapper.generation if self._slot_mapper else frame.generation,
        )
