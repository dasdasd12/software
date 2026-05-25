"""Thin virtual device session surface for simulated transports."""

from dataclasses import dataclass, field
from typing import List, Optional

from core import Snapshot

from .command_adapter import VirtualDeviceCommandAdapter, VirtualDeviceCommandResult
from .device_transport import DeviceFrame, DeviceTransport, DeviceTransportError
from .manager import DeviceManager
from .projection_runtime import DeviceProjectionRuntime
from .protocol_codec import DeviceProtocolCodec
from .slot_mapper import DeviceSlotMapper
from .virtual_input import (
    INPUT_EVENT_FRAME_TYPE,
    decode_input_event,
    error_frame_for_exception,
    unknown_frame_error,
)


@dataclass(frozen=True)
class VirtualDeviceSessionResult:
    response_frames: List[DeviceFrame] = field(default_factory=list)
    command_result: Optional[VirtualDeviceCommandResult] = None


class VirtualDeviceSession:
    """Orchestrates virtual device connect, snapshot send, and input handling."""

    def __init__(
        self,
        device_id: str,
        transport: Optional[DeviceTransport] = None,
        codec: Optional[DeviceProtocolCodec] = None,
        slot_mapper: Optional[DeviceSlotMapper] = None,
        command_adapter: Optional[VirtualDeviceCommandAdapter] = None,
        projection_runtime: Optional[DeviceProjectionRuntime] = None,
        device_manager: Optional[DeviceManager] = None,
    ) -> None:
        self.device_id = device_id
        self.transport = transport
        self.codec = codec or DeviceProtocolCodec()
        self.command_adapter = command_adapter
        if projection_runtime is not None:
            self.projection_runtime = projection_runtime
            self.slot_mapper = slot_mapper or projection_runtime.slot_mapper
        else:
            self.slot_mapper = slot_mapper or DeviceSlotMapper(device_id=device_id)
            self.projection_runtime = DeviceProjectionRuntime(
                device_id=device_id,
                codec=self.codec,
                slot_mapper=self.slot_mapper,
            )
        self.device_manager = device_manager
        if self.command_adapter is not None:
            adapter_mapper = getattr(self.command_adapter, "_slot_mapper", None)
            if adapter_mapper is None:
                self.command_adapter.set_slot_mapper(self.slot_mapper)

    async def connect(self, snapshot: Optional[Snapshot] = None) -> List[DeviceFrame]:
        if self.transport is not None:
            if not self.transport.get_status().is_open:
                await self.transport.open()
            if self.device_manager is not None:
                self.device_manager.register_transport(self.transport)
        return await self.send_snapshot(snapshot)

    async def send_snapshot(self, snapshot: Optional[Snapshot] = None) -> List[DeviceFrame]:
        frames = self.projection_runtime.snapshot_frames(snapshot)
        await self._send_frames(frames)
        return frames

    async def handle_frame(self, frame: DeviceFrame) -> VirtualDeviceSessionResult:
        if frame.frame_type != INPUT_EVENT_FRAME_TYPE:
            error_frame = error_frame_for_exception(
                unknown_frame_error(frame),
                self.codec,
                frame,
                generation=self.slot_mapper.generation,
            )
            await self._send_frames([error_frame])
            return VirtualDeviceSessionResult(response_frames=[error_frame])

        if self.command_adapter is not None:
            command_result = await self.command_adapter.handle_frame(frame)
            await self._send_frames(command_result.response_frames)
            return VirtualDeviceSessionResult(
                response_frames=command_result.response_frames,
                command_result=command_result,
            )

        try:
            decode_input_event(frame, self.codec, self.slot_mapper)
        except DeviceTransportError as exc:
            error_frame = error_frame_for_exception(
                exc,
                self.codec,
                frame,
                generation=self.slot_mapper.generation,
            )
            await self._send_frames([error_frame])
            return VirtualDeviceSessionResult(response_frames=[error_frame])

        ack_frame = self.codec.encode_message(
            frame_type="ACK_RESP",
            payload={"status": "ok", "dispatched": 0},
            device_id=frame.device_id,
            generation=self.slot_mapper.generation,
        )
        await self._send_frames([ack_frame])
        return VirtualDeviceSessionResult(response_frames=[ack_frame])

    async def _send_frames(self, frames: List[DeviceFrame]) -> None:
        if self.transport is None:
            return
        for frame in frames:
            await self.transport.send_frame(frame)
