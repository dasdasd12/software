import asyncio
from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from core import CommandRouter, EventBus, EventEnvelope  # noqa: E402
from devices import DeviceProtocolCodec  # noqa: E402
from devices.command_adapter import VirtualDeviceCommandAdapter  # noqa: E402
from keyboard.profile import AgentBinding, BindingTrigger, KeyboardAction, Profile  # noqa: E402


def _profile_with_enter_launch() -> Profile:
    return Profile(
        id="profile_dev",
        name="Developer",
        target_device_family="simulated",
        agent_bindings=[
            AgentBinding(
                id="launch_enter",
                trigger=BindingTrigger(source="key", event="press", key="K_ENTER"),
                action=KeyboardAction(
                    type="agent.session.launch_or_resume",
                    target="focused_session",
                    payload={"session_id": "new"},
                ),
            )
        ],
    )


def test_input_event_resolves_profile_binding_and_dispatches_async_command():
    async def run():
        codec = DeviceProtocolCodec()
        event_bus = EventBus()
        router = CommandRouter(event_bus)
        dispatched = []

        async def handler(command):
            dispatched.append(command)
            return EventEnvelope(
                seq=0,
                type="agent.session.created",
                payload={"session_id": command.payload["session_id"]},
            )

        router.register("agent.session.launch_or_resume", handler)
        adapter = VirtualDeviceCommandAdapter(
            active_profile_provider=lambda device_id: _profile_with_enter_launch(),
            router=router,
            codec=codec,
        )
        frame = codec.encode_message(
            frame_type="INPUT_EVENT",
            payload={"key_id": "K_ENTER", "event_type": "press", "sequence": 7},
            device_id="kbd_01",
        )

        result = await adapter.handle_frame(frame)

        assert result.error_frame is None
        assert result.input_event.key_id == "K_ENTER"
        assert [command.type for command in result.commands] == ["agent.session.launch_or_resume"]
        assert dispatched[0].source.kind == "device-transport"
        assert dispatched[0].source.device_id == "kbd_01"
        assert dispatched[0].payload["binding_id"] == "launch_enter"
        assert dispatched[0].payload["sequence"] == 7
        assert [event.type for event in result.events] == ["agent.session.created"]

    asyncio.run(run())


def test_no_matching_binding_returns_ack_empty_result_without_error():
    async def run():
        codec = DeviceProtocolCodec()
        event_bus = EventBus()
        router = CommandRouter(event_bus)
        adapter = VirtualDeviceCommandAdapter(
            active_profile_provider=lambda device_id: _profile_with_enter_launch(),
            router=router,
            codec=codec,
        )
        frame = codec.encode_message(
            frame_type="INPUT_EVENT",
            payload={"key_id": "K_ESC", "event_type": "press"},
            device_id="kbd_01",
        )

        result = await adapter.handle_frame(frame)

        assert result.error_frame is None
        assert result.ack_frame is not None
        assert result.ack_frame.frame_type == "ACK_RESP"
        assert result.commands == []
        assert result.events == []

    asyncio.run(run())
