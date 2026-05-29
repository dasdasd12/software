"""Agent command handlers."""

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from core import CommandEnvelope, CommandRouter, EventEnvelope

from .runtime import AgentLifecycleError, AgentRuntime


PermissionResponder = Callable[[CommandEnvelope], Awaitable[Dict[str, Any]]]


class AgentCommandService:
    """Owns agent side effects for structured agent commands."""

    def __init__(
        self,
        runtime: AgentRuntime,
        permission_responder: Optional[PermissionResponder] = None,
        foreground_cli_launcher: Optional[Any] = None,
        workspace_resolver: Optional[Callable[[Optional[str]], str]] = None,
    ):
        self.runtime = runtime
        self._permission_responder = permission_responder
        self._foreground_cli_launcher = foreground_cli_launcher
        self._workspace_resolver = workspace_resolver or self._default_workspace_resolver

    async def launch_or_resume(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._session_id(command) or "new"
        context = str(command.payload.get("context", ""))
        workspace = self._workspace(command)

        if session_id == "new":
            agent_key = self.runtime.resolve_agent_key(self._agent(command))
            controller = self.runtime.require_controller(agent_key)
            session = self.runtime.create_session(agent_key)
            self._apply_launch_metadata(session, command.payload)
            session_id = session.session_id
            self.runtime.update_state(session_id, "SUBMITTED")
            self.runtime.persist(session_id)
            try:
                if workspace is None:
                    await controller.launch(session_id, context)
                else:
                    await controller.launch(session_id, context, workspace=workspace)
            except Exception as exc:
                self.runtime.update_state(session_id, "FAILED")
                self.runtime.persist(session_id)
                raise AgentLifecycleError("LAUNCH_FAILED", str(exc)) from exc
            self.runtime.persist(session_id)
            return self._event("agent.session.created", session_id)

        session = self.runtime.require_session(session_id)
        self._apply_launch_metadata(session, command.payload)
        controller = self.runtime.require_controller(session.agent)
        self.runtime.update_state(session_id, "SUBMITTED")
        self.runtime.persist(session_id)
        try:
            if workspace is None:
                await controller.resume(session_id)
            else:
                await controller.resume(session_id, workspace=workspace)
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

    async def send_input(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._required_session_id(command)
        text = command.payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AgentLifecycleError("INVALID_COMMAND", "text is required")

        session = self.runtime.require_session(session_id)
        try:
            controller = self.runtime.require_controller(session.agent)
        except AgentLifecycleError as exc:
            if exc.code == "AGENT_UNAVAILABLE":
                raise AgentLifecycleError(
                    "INPUT_UNAVAILABLE",
                    "controller does not accept session input",
                ) from exc
            raise

        send_input = getattr(controller, "send_input", None)
        if not callable(send_input):
            raise AgentLifecycleError(
                "INPUT_UNAVAILABLE",
                "controller does not accept session input",
            )

        try:
            accepted = await send_input(session_id, text)
        except Exception as exc:
            raise AgentLifecycleError("INPUT_FAILED", str(exc)) from exc
        if not accepted:
            raise AgentLifecycleError(
                "INPUT_REJECTED",
                "session input was not accepted by controller",
            )

        self.runtime.persist(session_id)
        return self._event("agent.session.input.accepted", session_id, accepted=True)

    async def launch_foreground_cli(self, command: CommandEnvelope) -> EventEnvelope:
        if self._foreground_cli_launcher is None:
            raise AgentLifecycleError(
                "FOREGROUND_CLI_UNAVAILABLE",
                "foreground CLI launcher is not configured",
            )

        agent_key = self.runtime.resolve_agent_key(self._agent(command))
        self.runtime.require_controller(agent_key)
        agent = self.runtime.agent_value(agent_key)
        workspace = self._workspace_resolver(self._workspace(command))
        try:
            process = self._foreground_cli_launcher.launch(agent, workspace)
        except Exception as exc:
            raise AgentLifecycleError("FOREGROUND_CLI_LAUNCH_FAILED", str(exc)) from exc

        frontend_pid = getattr(process, "pid", None)
        return EventEnvelope(
            seq=0,
            type="agent.cli.launched",
            target={"agent": agent},
            payload={
                "agent": agent,
                "workspace": workspace,
                "frontend_pid": frontend_pid,
                "launch_surface": "foreground_cli",
                "control_mode": "managed_native",
            },
        )

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

    async def respond_permission(self, command: CommandEnvelope) -> EventEnvelope:
        if self._permission_responder is None:
            raise AgentLifecycleError(
                "PERMISSION_UNAVAILABLE",
                "permission response handling is not configured",
            )
        ack = await self._permission_responder(command)
        return EventEnvelope(
            seq=0,
            type="agent.permission.resolved",
            target={
                "permission_id": ack.get("request_id"),
                "session_id": ack.get("session_id"),
            },
            payload=ack,
        )

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
    def _workspace(command: CommandEnvelope) -> Optional[str]:
        value = command.payload.get("workspace")
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

    @staticmethod
    def _default_workspace_resolver(workspace: Optional[str]) -> str:
        return str(Path(workspace or ".").resolve())

    @staticmethod
    def _apply_launch_metadata(session: Any, payload: Dict[str, Any]) -> None:
        launch_surface = payload.get("launch_surface")
        if launch_surface == "foreground_cli" and hasattr(session, "launch_surface"):
            session.launch_surface = launch_surface

        control_mode = payload.get("control_mode")
        if control_mode == "managed_native" and hasattr(session, "control_mode"):
            session.control_mode = control_mode

        frontend_pid = payload.get("frontend_pid")
        if type(frontend_pid) is int and hasattr(session, "frontend_pid"):
            session.frontend_pid = frontend_pid


def register_agent_lifecycle_handlers(
    router: CommandRouter,
    service: AgentCommandService,
) -> None:
    router.register("agent.session.launch_or_resume", service.launch_or_resume)
    router.register("agent.cli.launch_foreground", service.launch_foreground_cli)
    router.register("agent.session.input", service.send_input)
    router.register("agent.run.interrupt", service.interrupt)
    router.register("agent.session.close", service.close_session)
    router.register("agent.permission.respond", service.respond_permission)
