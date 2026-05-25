import asyncio
from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices import DeviceProtocolCodec, DeviceSlotMapper  # noqa: E402
from devices.session import VirtualDeviceSession  # noqa: E402
from devices.virtual_input import decode_input_event  # noqa: E402


def test_input_event_frame_decodes_to_keyboard_press_event():
    codec = DeviceProtocolCodec()
    frame = codec.encode_message(
        frame_type="INPUT_EVENT",
        payload={
            "key_id": "K_ENTER",
            "event_type": "press",
            "active_layers": ["fn"],
            "modifiers": ["shift"],
            "timestamp": 123456,
            "sequence": 42,
        },
        device_id="kbd_01",
        generation=0,
    )

    event = decode_input_event(frame, codec)

    assert event.device_id == "kbd_01"
    assert event.key_id == "K_ENTER"
    assert event.event_type == "press"
    assert event.active_layers == ("fn",)
    assert event.modifiers == ("shift",)
    assert event.timestamp == 123456
    assert event.sequence == 42


def test_slot_generation_mismatch_returns_recoverable_error_frame():
    async def run():
        codec = DeviceProtocolCodec()
        mapper = DeviceSlotMapper(device_id="kbd_01")
        mapper.assign_session("sess_01")
        session = VirtualDeviceSession(device_id="kbd_01", codec=codec, slot_mapper=mapper)
        frame = codec.encode_message(
            frame_type="INPUT_EVENT",
            payload={"key_id": "K_ENTER", "event_type": "press"},
            device_id="kbd_01",
            generation=mapper.generation - 1,
        )

        result = await session.handle_frame(frame)

        assert [item.frame_type for item in result.response_frames] == ["ERROR_RESP"]
        payload = codec.decode_message(result.response_frames[0])
        assert payload["code"] == "UNKNOWN_SLOT_GENERATION"
        assert payload["recoverable"] is True
        assert payload["expected_generation"] == mapper.generation
        assert payload["received_generation"] == mapper.generation - 1

    asyncio.run(run())


def test_unknown_frame_returns_structured_error_frame_without_escaping():
    async def run():
        codec = DeviceProtocolCodec()
        session = VirtualDeviceSession(device_id="kbd_01", codec=codec)
        frame = codec.encode_message(
            frame_type="FUTURE_FRAME",
            payload={"value": 1},
            device_id="kbd_01",
        )

        result = await session.handle_frame(frame)

        assert [item.frame_type for item in result.response_frames] == ["ERROR_RESP"]
        payload = codec.decode_message(result.response_frames[0])
        assert payload["code"] == "UNKNOWN_FRAME_TYPE"
        assert payload["recoverable"] is True
        assert payload["frame_type"] == "FUTURE_FRAME"
        assert payload["device_id"] == "kbd_01"

    asyncio.run(run())
