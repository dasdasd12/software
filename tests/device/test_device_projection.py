import asyncio
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from core import Snapshot  # noqa: E402
from devices import (  # noqa: E402
    DeviceManager,
    DeviceProtocolCodec,
    DeviceSlotMapper,
    DeviceTransportError,
    SimulatedTransport,
    project_device_snapshot_frames,
    project_slot_snapshot_frames,
)
from keyboard import FocusManager, NotificationQueue, PermissionRequest, ScreenFocus  # noqa: E402


def test_simulator_negotiation_registers_extended_capabilities():
    async def run():
        transport = SimulatedTransport(
            device_id="kbd_01",
            device_family="ai_keyboard_ch32h417",
            firmware_version="0.2.0",
            supported_profile_features={"hid", "layers", "agent_bindings"},
            supported_screen_widgets={"agent_session_card", "notification_strip"},
            supports_agent_slots=True,
            supports_config_sync=True,
        )
        manager = DeviceManager()

        record = await manager.negotiate_transport(transport)

        assert record.capabilities.device_family == "ai_keyboard_ch32h417"
        assert record.capabilities.firmware_version == "0.2.0"
        assert "SLOT_MAP_ITEM" in record.capabilities.supported_message_types
        assert record.capabilities.supports_agent_slots is True
        assert record.status.is_open is True

    asyncio.run(run())


def test_slot_snapshot_projection_emits_begin_items_and_end_frames():
    codec = DeviceProtocolCodec()
    mapper = DeviceSlotMapper(device_id="kbd_01")
    mapper.assign_agent("codex-software")
    mapper.assign_session("sess_01")
    mapper.assign_run("run_01")
    mapper.assign_permission("perm_01")
    mapper.assign_notification("note_01")

    frames = project_slot_snapshot_frames(codec, mapper.snapshot())

    assert [frame.frame_type for frame in frames] == [
        "SLOT_MAP_BEGIN",
        "SLOT_MAP_ITEM",
        "SLOT_MAP_ITEM",
        "SLOT_MAP_ITEM",
        "SLOT_MAP_ITEM",
        "SLOT_MAP_ITEM",
        "SLOT_MAP_END",
    ]
    assert frames[0].generation == mapper.generation
    assert codec.decode_message(frames[1]) == {
        "slot_kind": "agent",
        "slot_id": 1,
        "value": "codex-software",
    }
    assert codec.decode_message(frames[-1]) == {"generation": mapper.generation}


def test_slot_generation_mismatch_raises_structured_error():
    mapper = DeviceSlotMapper(device_id="kbd_01")
    slot_id = mapper.assign_session("sess_01")

    with pytest.raises(DeviceTransportError) as exc_info:
        mapper.resolve_session(slot_id, mapper.generation - 1)

    error = exc_info.value
    assert error.code == "UNKNOWN_SLOT_GENERATION"
    assert error.device_id == "kbd_01"
    assert error.to_dict()["recoverable"] is True
    assert error.to_dict()["expected_generation"] == mapper.generation


def test_device_snapshot_projection_sends_slot_focus_and_pending_summaries():
    codec = DeviceProtocolCodec()
    mapper = DeviceSlotMapper(device_id="kbd_01")
    focus_manager = FocusManager()
    notifications = NotificationQueue()

    focus_manager.set_focus(ScreenFocus(
        device_id="kbd_01",
        mode="session",
        instance_id="codex-software",
        session_id="sess_01",
        run_id="run_01",
    ))
    notifications.enqueue_permission(PermissionRequest(
        permission_id="perm_01",
        priority=50,
        instance_id="codex-software",
        session_id="sess_01",
        run_id="run_01",
        risk="low",
    ))
    snapshot = Snapshot(
        last_event_seq=12,
        sessions={"sess_01": {"title": "Build"}},
        runs={"run_01": {"state": "running"}},
        notifications=[{"notification_id": "note_01", "level": "info", "message": "Ready"}],
    )

    frames = project_device_snapshot_frames(
        codec=codec,
        device_id="kbd_01",
        snapshot=snapshot,
        mapper=mapper,
        focus=focus_manager.get_focus("kbd_01"),
        notifications=notifications,
        active_profile={"id": "profile_dev", "name": "Developer"},
    )

    frame_types = [frame.frame_type for frame in frames]
    assert frame_types[0] == "DEVICE_SNAPSHOT_BEGIN"
    assert "SCREEN_FOCUS_SET" in frame_types
    assert "NOTIFICATION_PUSH" in frame_types
    assert "PERMISSION_REQUEST_PUSH" in frame_types
    focus_payload = codec.decode_message(frames[frame_types.index("SCREEN_FOCUS_SET")])
    assert focus_payload["session_slot_id"] == mapper.assign_session("sess_01")
    assert focus_payload["permission_slot_id"] == mapper.assign_permission("perm_01")
    generations = {frame.generation for frame in frames}
    assert generations == {mapper.generation}
    slot_payloads = [codec.decode_message(frame) for frame in frames if frame.frame_type == "SLOT_MAP_ITEM"]
    assert {
        "slot_kind": "permission",
        "slot_id": mapper.assign_permission("perm_01"),
        "value": "perm_01",
    } in slot_payloads
    assert frames[-1].frame_type == "DEVICE_SNAPSHOT_END"
