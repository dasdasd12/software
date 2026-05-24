import asyncio
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from core import CommandEnvelope, CommandRouter, CommandSource, EventBus, EventEnvelope  # noqa: E402


def _command(command_type: str, command_id: str = "cmd_1") -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id,
        type=command_type,
        source=CommandSource(kind="test-client", client_id="pytest"),
    )


def test_dispatch_async_awaits_async_handler():
    calls = []
    router = CommandRouter(EventBus())

    async def handler(command: CommandEnvelope) -> EventEnvelope:
        calls.append(command.command_id)
        await asyncio.sleep(0)
        return EventEnvelope(
            seq=0,
            type="system.async_done",
            payload={"ok": True},
        )

    router.register("system.async", handler)

    event = asyncio.run(router.dispatch_async(_command("system.async")))

    assert calls == ["cmd_1"]
    assert event.type == "system.async_done"
    assert event.seq == 1


def test_dispatch_async_supports_sync_handler():
    router = CommandRouter(EventBus())

    def handler(command: CommandEnvelope) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="system.sync_done",
            payload={"command_id": command.command_id},
        )

    router.register("system.sync", handler)

    event = asyncio.run(router.dispatch_async(_command("system.sync")))

    assert event.type == "system.sync_done"
    assert event.payload == {"command_id": "cmd_1"}
    assert event.seq == 1


def test_dispatch_still_supports_sync_handler():
    router = CommandRouter(EventBus())

    def handler(command: CommandEnvelope) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="system.sync_done",
            payload={"command_id": command.command_id},
        )

    router.register("system.sync", handler)

    event = router.dispatch(_command("system.sync"))

    assert event.type == "system.sync_done"
    assert event.payload == {"command_id": "cmd_1"}
    assert event.seq == 1


def test_dispatch_rejects_async_handler_with_clear_error():
    router = CommandRouter(EventBus())

    async def handler(command: CommandEnvelope) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="system.async_done",
            payload={"command_id": command.command_id},
        )

    router.register("system.async", handler)

    with pytest.raises(TypeError, match="dispatch_async"):
        router.dispatch(_command("system.async"))


def test_unknown_command_raises_key_error_for_sync_and_async_dispatch():
    router = CommandRouter(EventBus())

    with pytest.raises(KeyError, match="no handler registered"):
        router.dispatch(_command("system.missing"))

    with pytest.raises(KeyError, match="no handler registered"):
        asyncio.run(router.dispatch_async(_command("system.missing")))
