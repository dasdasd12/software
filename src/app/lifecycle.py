"""Runtime composition scaffold.

The current executable entry point remains src/bridge/server.py. This module is
the future composition root described by the architecture docs.
"""

from dataclasses import dataclass
from typing import Optional

from agents import AgentRegistry
from agents.commands import AgentCommandService, register_agent_lifecycle_handlers
from core import CommandEnvelope, CommandRouter, EventBus, EventEnvelope, Snapshot, StateStore
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
    agent_commands: Optional[AgentCommandService] = None

    def snapshot(self) -> Snapshot:
        return self.state_store.snapshot(last_event_seq=self.event_bus.last_seq)

    def configure_agent_commands(self, service: AgentCommandService) -> None:
        self.agent_commands = service
        register_agent_lifecycle_handlers(self.command_router, service)


def build_runtime() -> LocalCoreRuntime:
    event_bus = EventBus()
    state_store = StateStore()
    command_router = CommandRouter(event_bus, state_store=state_store)
    _register_system_handlers(command_router)
    return LocalCoreRuntime(
        event_bus=event_bus,
        state_store=state_store,
        command_router=command_router,
        agent_registry=AgentRegistry(),
        device_manager=DeviceManager(),
        health_reporter=HealthReporter(),
    )


def _register_system_handlers(command_router: CommandRouter) -> None:
    def snapshot_requested(command: CommandEnvelope) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="system.snapshot.generated",
            payload={"command_id": command.command_id},
        )

    def notification_create(command: CommandEnvelope) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="notification.created",
            payload=dict(command.payload),
        )

    command_router.register("system.snapshot.request", snapshot_requested)
    command_router.register("notification.create", notification_create)
