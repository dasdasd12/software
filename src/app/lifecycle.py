"""Runtime composition scaffold.

The current executable entry point remains src/bridge/server.py. This module is
the future composition root described by the architecture docs.
"""

from dataclasses import dataclass

from agents import AgentRegistry
from core import CommandRouter, EventBus, Snapshot, StateStore
from devices import DeviceManager
from diagnostics import HealthReporter


@dataclass
class LocalCoreRuntime:
    event_bus: EventBus
    state_store: StateStore
    command_router: CommandRouter
    agent_registry: AgentRegistry
    device_manager: DeviceManager
    health_reporter: HealthReporter

    def snapshot(self) -> Snapshot:
        return self.state_store.snapshot(last_event_seq=self.event_bus.last_seq)


def build_runtime() -> LocalCoreRuntime:
    event_bus = EventBus()
    return LocalCoreRuntime(
        event_bus=event_bus,
        state_store=StateStore(),
        command_router=CommandRouter(event_bus),
        agent_registry=AgentRegistry(),
        device_manager=DeviceManager(),
        health_reporter=HealthReporter(),
    )
