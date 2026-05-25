import asyncio
from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from core import EventEnvelope, Snapshot  # noqa: E402
from devices import DeviceProtocolCodec, SimulatedTransport  # noqa: E402
from devices.projection_runtime import DeviceProjectionRuntime  # noqa: E402
from devices.session import VirtualDeviceSession  # noqa: E402
from keyboard import NotificationQueue, PermissionRequest, ScreenFocus  # noqa: E402


def test_device_connect_sends_snapshot_frames_in_expected_order():
    async def run():
        codec = DeviceProtocolCodec()
        transport = SimulatedTransport(device_id="kbd_01")
        notifications = NotificationQueue()
        notifications.enqueue_permission(PermissionRequest(
            permission_id="perm_01",
            priority=80,
            risk="high",
            session_id="sess_01",
        ))
        runtime = DeviceProjectionRuntime(
            device_id="kbd_01",
            codec=codec,
            focus=ScreenFocus(device_id="kbd_01", mode="session", session_id="sess_01"),
            notifications=notifications,
            active_profile={"id": "profile_dev", "name": "Developer"},
        )
        session = VirtualDeviceSession(
            device_id="kbd_01",
            transport=transport,
            codec=codec,
            projection_runtime=runtime,
        )
        snapshot = Snapshot(
            last_event_seq=5,
            sessions={"sess_01": {"title": "Build"}},
            notifications=[{"notification_id": "note_01", "level": "info", "message": "Ready"}],
        )

        sent = await session.connect(snapshot=snapshot)

        frame_types = [frame.frame_type for frame in sent]
        assert frame_types[0] == "DEVICE_SNAPSHOT_BEGIN"
        assert frame_types[1] == "SLOT_MAP_BEGIN"
        assert "SLOT_MAP_ITEM" in frame_types
        assert frame_types.index("SLOT_MAP_END") < frame_types.index("PROFILE_SUMMARY_SET")
        assert frame_types.index("PROFILE_SUMMARY_SET") < frame_types.index("SCREEN_FOCUS_SET")
        assert frame_types.index("SCREEN_FOCUS_SET") < frame_types.index("NOTIFICATION_PUSH")
        assert frame_types.index("NOTIFICATION_PUSH") < frame_types.index("PERMISSION_REQUEST_PUSH")
        assert frame_types[-1] == "DEVICE_SNAPSHOT_END"
        assert transport.get_status().queued_frames == len(sent)

    asyncio.run(run())


def test_permission_event_projects_incremental_permission_request_push():
    codec = DeviceProtocolCodec()
    runtime = DeviceProjectionRuntime(device_id="kbd_01", codec=codec)
    event = EventEnvelope(
        seq=9,
        type="agent.permission.requested",
        payload={
            "permission_id": "native_perm_01",
            "priority": 90,
            "risk": "high",
            "session_id": "sess_01",
        },
    )

    frames = runtime.event_frames(event)

    assert [frame.frame_type for frame in frames] == ["PERMISSION_REQUEST_PUSH"]
    payload = codec.decode_message(frames[0])
    assert payload["permission_slot_id"] == 1
    assert payload["priority"] == 90
    assert payload["risk"] == "high"
    assert runtime.slot_mapper.snapshot().permission_slots[1] == "native_perm_01"
