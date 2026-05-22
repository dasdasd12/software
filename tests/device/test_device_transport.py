import asyncio
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices.device_transport import (  # noqa: E402
    DeviceFrame,
    DeviceTransportError,
    SimulatedTransport,
)


def test_device_frame_encode_decode_round_trip_preserves_generation():
    frame = DeviceFrame(
        frame_type="UNKNOWN_FUTURE_FRAME",
        payload=b"hello",
        protocol_version=1,
        generation=7,
        device_id="kbd_01",
    )

    decoded = DeviceFrame.decode(frame.encode())

    assert decoded == frame


def test_simulated_transport_round_trip_and_status():
    async def run():
        transport = SimulatedTransport(device_id="kbd_01", max_payload_size=32)
        assert transport.get_status().is_open is False

        await transport.open()
        capabilities = transport.get_capabilities()
        assert capabilities.device_id == "kbd_01"
        assert capabilities.transport_kind == "simulated"
        assert capabilities.max_payload_size == 32
        assert "HEARTBEAT" in capabilities.supported_message_types

        frame = DeviceFrame(frame_type="HEARTBEAT", payload=b"ping", device_id="kbd_01")
        await transport.send_frame(frame)
        assert transport.get_status().queued_frames == 1
        assert await transport.read_frame() == frame
        assert transport.get_status().queued_frames == 0

        await transport.close()
        assert transport.get_status().is_open is False

    asyncio.run(run())


def test_simulated_transport_rejects_payload_over_boundary():
    async def run():
        transport = SimulatedTransport(max_payload_size=4)
        await transport.open()

        with pytest.raises(DeviceTransportError) as exc_info:
            await transport.send_frame(DeviceFrame(frame_type="DIAGNOSTIC_LOG", payload=b"12345"))

        error = exc_info.value
        assert error.code == "PAYLOAD_TOO_LARGE"
        assert error.frame_type == "DIAGNOSTIC_LOG"
        assert error.to_dict()["recoverable"] is True

    asyncio.run(run())


def test_simulated_transport_rejects_closed_operations():
    async def run():
        transport = SimulatedTransport(device_id="kbd_01")

        with pytest.raises(DeviceTransportError) as send_error:
            await transport.send_frame(DeviceFrame(frame_type="HEARTBEAT"))
        assert send_error.value.code == "TRANSPORT_CLOSED"

        with pytest.raises(DeviceTransportError) as read_error:
            await transport.read_frame()
        assert read_error.value.code == "TRANSPORT_CLOSED"
        assert read_error.value.to_dict()["device_id"] == "kbd_01"

    asyncio.run(run())
