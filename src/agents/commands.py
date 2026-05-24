"""Agent lifecycle command handlers."""

from typing import Any, Optional

from core import CommandEnvelope, CommandRouter, EventEnvelope

from .runtime import AgentLifecycleError, AgentRuntime


class AgentCommandService:
    """Owns lifecycle side effects for structured agent commands."""

    def __init__(self, runtime: AgentRuntime):
        self.runtime = runtime

    async def launch_or_resume(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._session_id(command) or "new"
        context = str(command.payload.get("context", ""))

        if session_id == "new":
            agent_key = self.runtime.resolve_agent_key(self._agent(command))
            controller = self.runtime.require_controller(agent_key)
            session = self.runtime.create_session(agent_key)
            session_id = session.session_id
            self.runtime.update_state(session_id, "SUBMITTED")
            self.runtime.persist(session_id)
            try:
                await controller.launch(session_id, context)
            except Exception as exc:
                self.runtime.update_state(session_id, "FAILED")
                self.runtime.persist(session_id)
                raise AgentLifecycleError("LAUNCH_FAILED", str(exc)) from exc
            self.runtime.persist(session_id)
            return self._event("agent.session.created", session_id)

        session = self.runtime.require_session(session_id)
        controller = self.runtime.require_controller(session.agent)
        self.runtime.update_state(session_id, "SUBMITTED")
        self.runtime.persist(session_id)
        try:
            await controller.resume(session_id)
        except Exception as exc:
            self.runtime.update_state(session_id, "FAILED")
            self.runtime.persist(session_id)
            raise AgentLifecycleError("LAUNCH_FAILED", str(exc)) from exc
        self.runtime.persist(session_id)
        return self._event("agent.session.state_changed", session_id)

    async def interrupt(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._required_session_id(command)
        session = self.runtime.require_session(session_id)
        controller = self.runtime.require_controller(session.agent)
        try:
            accepted = await controller.send_interrupt(session_id)
        except Exception as exc:
            raise AgentLifecycleError("INTERRUPT_FAILED", str(exc)) from exc
        if not accepted:
            raise AgentLifecycleError("INTERRUPT_FAILED", "interrupt was not accepted by controller")

        self.runtime.update_state(session_id, "CANCELLED")
        self.runtime.persist(session_id)
        return self._event("agent.run.interrupted", session_id, interrupted=True, accepted=bool(accepted))

    async def close_session(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._required_session_id(command)
        session = self.runtime.require_session(session_id)
        controller = self.runtime.require_controller(session.agent)
        try:
            accepted = await controller.terminate(session_id)
        except Exception as exc:
            raise AgentLifecycleError("TERMINATE_FAILED", str(exc)) from exc
        if not accepted:
            raise AgentLifecycleError("TERMINATE_FAILED", "terminate was not accepted by controller")

        self.runtime.update_state(session_id, "CANCELLED")
        self.runtime.persist(session_id)
        return self._event("agent.session.closed", session_id, closed=True, accepted=bool(accepted))

    def _event(self, event_type: str, session_id: str, **extra: Any) -> EventEnvelope:
        payload = self.runtime.session_payload(session_id, **extra)
        return EventEnvelope(
            seq=0,
            type=event_type,
            target={"session_id": session_id, "agent": payload.get("agent")},
            payload=payload,
        )

    @staticmethod
    def _agent(command: CommandEnvelope) -> Optional[str]:
        target = AgentCommandService._target(command)
        for container in (command.payload, target):
            value = container.get("agent") or container.get("provider_id")
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _session_id(command: CommandEnvelope) -> Optional[str]:
        target = AgentCommandService._target(command)
        for container in (target, command.payload):
            value = container.get("session_id")
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _target(command: CommandEnvelope) -> dict:
        if command.target is None:
            return {}
        if not isinstance(command.target, dict):
            raise AgentLifecycleError("INVALID_COMMAND", "command target must be an object")
        return command.target

    def _required_session_id(self, command: CommandEnvelope) -> str:
        session_id = self._session_id(command)
        if not session_id:
            raise AgentLifecycleError("SESSION_NOT_FOUND", "session_id is required")
        return session_id


def register_agent_lifecycle_handlers(
    router: CommandRouter,
    service: AgentCommandService,
) -> None:
    router.register("agent.session.launch_or_resume", service.launch_or_resume)
    router.register("agent.run.interrupt", service.interrupt)
    router.register("agent.session.close", service.close_session)
