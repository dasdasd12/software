"""In-memory event bus for the Local Core Service scaffold."""

from dataclasses import replace
from typing import Callable, List

from .envelopes import EventEnvelope

EventSubscriber = Callable[[EventEnvelope], None]


class EventBus:
    """Service-local ordered event publisher.

    This is deliberately small: it gives new domain modules a single event
    boundary without committing the product to a storage or transport choice.
    """

    def __init__(self) -> None:
        self._seq = 0
        self._events: List[EventEnvelope] = []
        self._subscribers: List[EventSubscriber] = []

    @property
    def last_seq(self) -> int:
        return self._seq

    def subscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(self, event: EventEnvelope) -> EventEnvelope:
        self._seq += 1
        published = replace(event, seq=self._seq)
        self._events.append(published)
        for subscriber in list(self._subscribers):
            subscriber(published)
        return published

    def events_after(self, seq: int) -> List[EventEnvelope]:
        return [event for event in self._events if event.seq > seq]
