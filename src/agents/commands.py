"""Agent command handlers."""

import secrets
from pathlib import Path
import os
import signal
import subprocess
from typing import Any, Awaitable, Callable, Dict, Optional
import uuid

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
        self._foreground_root_pids_by_launch_id: Dict[str, int] = {}
        self._foreground_root_pids_by_session_id: Dict[str, int] = {}
        self._foreground_registrations_by_launch_id: Dict[str, Dict[str, Any]] = {}
        self._foreground_hook_tokens_by_session_id: Dict[str, str] = {}
        self._foreground_exit_tokens_by_session_id: Dict[str, str] = {}

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
            return self._event("agent.session.created", session_id, **self._launch_event_extra(command, workspace))

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
        return self._event("agent.session.state_changed", session_id, **self._launch_event_extra(command, workspace))

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
        context = command.payload.get("context")
        if not isinstance(context, str):
            context = ""
        model = command.payload.get("model")
        if not isinstance(model, str):
            model = ""
        reasoning_effort = command.payload.get("reasoning_effort")
        if not isinstance(reasoning_effort, str):
            reasoning_effort = ""
        foreground_launch_id = "fg_%s" % uuid.uuid4().hex
        native_cli = bool(command.payload.get("native_cli", agent in {"claude", "codex"}))
        permission_mode = self._permission_mode(command.payload, native_cli=native_cli)
        if agent != "claude" and permission_mode == "plan":
            raise AgentLifecycleError(
                "INVALID_PERMISSION_MODE",
                "permission_mode=plan is only supported for native foreground Claude",
            )
        registration_token = "reg_%s" % secrets.token_urlsafe(24) if native_cli else None
        hook_token = "hook_%s" % secrets.token_urlsafe(24) if native_cli else None
        exit_token = "exit_%s" % secrets.token_urlsafe(24) if native_cli else None
        try:
            process = self._foreground_cli_launcher.launch(
                agent,
                workspace,
                foreground_launch_id,
                native_cli=native_cli,
                permission_mode=permission_mode,
                context=context,
                model=model,
                reasoning_effort=reasoning_effort,
                registration_token=registration_token,
                hook_token=hook_token,
                exit_token=exit_token,
            )
        except Exception as exc:
            raise AgentLifecycleError("FOREGROUND_CLI_LAUNCH_FAILED", str(exc)) from exc

        frontend_pid = getattr(process, "pid", None)
        if type(frontend_pid) is int:
            self._foreground_root_pids_by_launch_id[foreground_launch_id] = frontend_pid
        if native_cli:
            self._foreground_registrations_by_launch_id[foreground_launch_id] = {
                "registration_token": registration_token,
                "hook_token": hook_token,
                "exit_token": exit_token,
                "agent": agent,
                "workspace": workspace,
                "bootstrap_pid": frontend_pid if type(frontend_pid) is int else None,
            }
        return EventEnvelope(
            seq=0,
            type="agent.cli.launched",
            target={"agent": agent},
            payload={
                "agent": agent,
                "workspace": workspace,
                "frontend_pid": frontend_pid,
                "foreground_launch_id": foreground_launch_id,
                "launch_surface": "foreground_cli",
                "control_mode": "native_cli" if native_cli else "managed_native",
                "permission_mode": permission_mode,
            },
        )

    async def register_foreground_session(self, command: CommandEnvelope) -> EventEnvelope:
        agent_key = self.runtime.resolve_agent_key(self._agent(command))
        self.runtime.require_controller(agent_key)
        frontend_pid = command.payload.get("frontend_pid")
        if type(frontend_pid) is not int or frontend_pid <= 0:
            raise AgentLifecycleError(
                "FOREGROUND_CLI_REGISTRATION_DENIED",
                "foreground native CLI registration requires a valid frontend_pid",
            )
        registration = self._validated_foreground_registration(command)
        session = self.runtime.create_session(agent_key)
        self._apply_launch_metadata(session, command.payload, allow_native_cli=True)
        session_id = session.session_id
        foreground_launch_id = command.payload.get("foreground_launch_id")
        self._foreground_root_pids_by_launch_id.pop(str(foreground_launch_id), None)
        self._foreground_root_pids_by_session_id[session_id] = frontend_pid
        self._foreground_hook_tokens_by_session_id[session_id] = str(registration["hook_token"])
        self._foreground_exit_tokens_by_session_id[session_id] = str(registration["exit_token"])
        if hasattr(session, "frontend_pid"):
            session.frontend_pid = frontend_pid
        self.runtime.update_state(session_id, "WORKING")
        self.runtime.persist(session_id)
        return self._event("agent.session.created", session_id, **self._launch_event_extra(command, self._workspace(command)))

    async def close_session(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._required_session_id(command)
        session = self.runtime.require_session(session_id)
        if (
            getattr(session, "launch_surface", None) == "foreground_cli"
            and getattr(session, "control_mode", None) == "native_cli"
        ):
            root_pid = self._foreground_root_pids_by_session_id.pop(session_id, None)
            self._foreground_hook_tokens_by_session_id.pop(session_id, None)
            self._foreground_exit_tokens_by_session_id.pop(session_id, None)
            if root_pid is None:
                raise AgentLifecycleError(
                    "FOREGROUND_CLI_NOT_OWNED",
                    "native foreground session was not launched by Local API and cannot be terminated safely",
                )
            if root_pid is not None:
                self._terminate_process_tree(root_pid)
            self.runtime.update_state(session_id, "CANCELLED")
            self.runtime.persist(session_id)
            return self._event("agent.session.closed", session_id, closed=True, accepted=True)
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

    async def foreground_exited(self, command: CommandEnvelope) -> EventEnvelope:
        session_id = self._required_session_id(command)
        session = self.runtime.require_session(session_id)
        if (
            getattr(session, "launch_surface", None) != "foreground_cli"
            or getattr(session, "control_mode", None) != "native_cli"
        ):
            raise AgentLifecycleError(
                "FOREGROUND_CLI_NOT_OWNED",
                "only registered native foreground sessions can report foreground exit",
            )

        expected_exit_token = self._foreground_exit_tokens_by_session_id.get(session_id)
        supplied_exit_token = command.payload.get("foreground_exit_token")
        if not (
            isinstance(expected_exit_token, str)
            and expected_exit_token
            and isinstance(supplied_exit_token, str)
            and secrets.compare_digest(supplied_exit_token, expected_exit_token)
        ):
            raise AgentLifecycleError(
                "FOREGROUND_CLI_EXIT_DENIED",
                "foreground exit token is invalid",
            )

        root_pid = self._foreground_root_pids_by_session_id.get(session_id)
        if root_pid is None:
            raise AgentLifecycleError(
                "FOREGROUND_CLI_NOT_OWNED",
                "native foreground session was not launched by Local API",
            )

        self._foreground_root_pids_by_session_id.pop(session_id, None)
        self._foreground_hook_tokens_by_session_id.pop(session_id, None)
        self._foreground_exit_tokens_by_session_id.pop(session_id, None)

        exit_code = command.payload.get("exit_code")
        try:
            exit_code_int = int(exit_code)
        except (TypeError, ValueError, OverflowError):
            exit_code_int = None
        state = "COMPLETED" if exit_code_int == 0 else "FAILED"
        self.runtime.update_state(session_id, state)
        self.runtime.persist(session_id)
        return self._event(
            "agent.session.exited",
            session_id,
            exited=True,
            exit_code=exit_code_int,
        )

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

    def hook_token_for_session(self, session_id: str) -> Optional[str]:
        return self._foreground_hook_tokens_by_session_id.get(session_id)

    def cleanup_unregistered_foreground_launch(self, foreground_launch_id: str) -> bool:
        if not isinstance(foreground_launch_id, str) or not foreground_launch_id:
            return False
        registration = self._foreground_registrations_by_launch_id.pop(foreground_launch_id, None)
        root_pid = self._foreground_root_pids_by_launch_id.pop(foreground_launch_id, None)
        if root_pid is not None:
            self._terminate_process_tree(root_pid)
        return registration is not None or root_pid is not None

    def _validated_foreground_registration(self, command: CommandEnvelope) -> Dict[str, Any]:
        foreground_launch_id = command.payload.get("foreground_launch_id")
        registration_token = command.payload.get("foreground_registration_token")
        if not isinstance(foreground_launch_id, str) or not foreground_launch_id:
            raise AgentLifecycleError(
                "FOREGROUND_CLI_REGISTRATION_DENIED",
                "foreground native CLI registration requires a launch id",
            )
        registration = self._foreground_registrations_by_launch_id.get(foreground_launch_id)
        if registration is None:
            raise AgentLifecycleError(
                "FOREGROUND_CLI_REGISTRATION_DENIED",
                "foreground native CLI launch id is not recognized",
            )
        expected = registration.get("registration_token")
        if not (
            isinstance(registration_token, str)
            and isinstance(expected, str)
            and secrets.compare_digest(registration_token, expected)
        ):
            raise AgentLifecycleError(
                "FOREGROUND_CLI_REGISTRATION_DENIED",
                "foreground native CLI registration token is invalid",
            )
        agent = command.payload.get("agent")
        if agent != registration.get("agent"):
            raise AgentLifecycleError(
                "FOREGROUND_CLI_REGISTRATION_DENIED",
                "foreground native CLI registration agent does not match launch",
            )
        if not isinstance(registration.get("exit_token"), str) or not registration.get("exit_token"):
            raise AgentLifecycleError(
                "FOREGROUND_CLI_REGISTRATION_DENIED",
                "foreground native CLI exit token is missing",
            )
        return self._foreground_registrations_by_launch_id.pop(foreground_launch_id)

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
    def _permission_mode(payload: Dict[str, Any], native_cli: bool = True) -> str:
        value = payload.get("permission_mode")
        if value in {None, ""}:
            return "default"
        if value not in {"default", "plan"}:
            raise AgentLifecycleError(
                "INVALID_PERMISSION_MODE",
                "permission_mode must be one of: default, plan",
            )
        if value == "plan" and not native_cli:
            raise AgentLifecycleError(
                "INVALID_PERMISSION_MODE",
                "permission_mode=plan requires native foreground Claude",
            )
        return str(value)

    @staticmethod
    def _apply_launch_metadata(
        session: Any,
        payload: Dict[str, Any],
        allow_native_cli: bool = False,
    ) -> None:
        launch_surface = payload.get("launch_surface")
        if launch_surface == "foreground_cli" and hasattr(session, "launch_surface"):
            session.launch_surface = launch_surface

        control_mode = payload.get("control_mode")
        allowed_control_modes = {"managed_native"}
        if allow_native_cli:
            allowed_control_modes.add("native_cli")
        if control_mode in allowed_control_modes and hasattr(session, "control_mode"):
            session.control_mode = control_mode

        frontend_pid = payload.get("frontend_pid")
        if type(frontend_pid) is int and hasattr(session, "frontend_pid"):
            session.frontend_pid = frontend_pid

    @staticmethod
    def _terminate_process_tree(pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

    @staticmethod
    def _launch_event_extra(command: CommandEnvelope, workspace: Optional[str]) -> Dict[str, Any]:
        extra: Dict[str, Any] = {}
        foreground_launch_id = command.payload.get("foreground_launch_id")
        if isinstance(foreground_launch_id, str) and foreground_launch_id:
            extra["foreground_launch_id"] = foreground_launch_id
        if isinstance(workspace, str) and workspace:
            extra["workspace"] = workspace
        return extra


def register_agent_lifecycle_handlers(
    router: CommandRouter,
    service: AgentCommandService,
) -> None:
    router.register("agent.session.launch_or_resume", service.launch_or_resume)
    router.register("agent.session.register_foreground", service.register_foreground_session)
    router.register("agent.cli.launch_foreground", service.launch_foreground_cli)
    router.register("agent.session.input", service.send_input)
    router.register("agent.run.interrupt", service.interrupt)
    router.register("agent.session.close", service.close_session)
    router.register("agent.session.foreground_exited", service.foreground_exited)
    router.register("agent.permission.respond", service.respond_permission)
