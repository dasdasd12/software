"""Command routing scaffold for architecture-aligned domain modules."""

from typing import Callable, Dict

from .envelopes import CommandEnvelope, EventEnvelope
from .event_bus import EventBus

CommandHandler = Callable[[CommandEnvelope], EventEnvelope]


class CommandRouter:
    """Routes validated command envelopes to domain handlers."""

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._handlers: Dict[str, CommandHandler] = {}

    def register(self, command_type: str, handler: CommandHandler) -> None:
        if not command_type:
            raise ValueError("command_type is required")
        self._handlers[command_type] = handler

    def dispatch(self, command: CommandEnvelope) -> EventEnvelope:
        handler = self._handlers.get(command.type)
        if handler is None:
            raise KeyError(f"no handler registered for command type: {command.type}")
        event = handler(command)
        return self._event_bus.publish(event)
