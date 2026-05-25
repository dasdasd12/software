"""Command routing scaffold for architecture-aligned domain modules."""

import inspect
from typing import Awaitable, Callable, Dict, Optional, Union

from .envelopes import CommandEnvelope, EventEnvelope
from .event_bus import EventBus
from .state_store import StateStore

CommandResult = Union[EventEnvelope, Awaitable[EventEnvelope]]
CommandHandler = Callable[[CommandEnvelope], CommandResult]


class CommandRouter:
    """Routes validated command envelopes to domain handlers."""

    def __init__(self, event_bus: EventBus, state_store: Optional[StateStore] = None):
        self._event_bus = event_bus
        self._state_store = state_store
        self._handlers: Dict[str, CommandHandler] = {}

    def register(self, command_type: str, handler: CommandHandler) -> None:
        if not command_type:
            raise ValueError("command_type is required")
        self._handlers[command_type] = handler

    def dispatch(self, command: CommandEnvelope) -> EventEnvelope:
        event = self._dispatch_sync(command)
        return self._publish(event)

    async def dispatch_async(self, command: CommandEnvelope) -> EventEnvelope:
        handler = self._handler_for(command)
        event = handler(command)
        if inspect.isawaitable(event):
            event = await event
        return self._publish(event)

    def _dispatch_sync(self, command: CommandEnvelope) -> EventEnvelope:
        handler = self._handler_for(command)
        event = handler(command)
        if inspect.isawaitable(event):
            self._close_awaitable(event)
            raise TypeError(
                f"handler for command type {command.type} returned an awaitable; "
                "use dispatch_async() for async handlers"
            )
        return event

    def _handler_for(self, command: CommandEnvelope) -> CommandHandler:
        handler = self._handlers.get(command.type)
        if handler is None:
            raise KeyError(f"no handler registered for command type: {command.type}")
        return handler

    def _publish(self, event: EventEnvelope) -> EventEnvelope:
        if event.type == "command.target.unresolved":
            return event
        if self._state_store is not None:
            self._state_store.apply_event(event)
        return self._event_bus.publish(event)

    @staticmethod
    def _close_awaitable(value: Awaitable[EventEnvelope]) -> None:
        close = getattr(value, "close", None)
        if callable(close):
            close()
