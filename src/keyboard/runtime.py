"""Keyboard focus command handlers and symbolic target wrappers."""

from dataclasses import replace
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional, Union

from core import CommandEnvelope, CommandRouter, EventBus, EventEnvelope, StateStore
from core.target_resolution import TargetResolution, TargetResolver, symbolic_selector

from .focus import FocusManager, ScreenFocus
from .tool_state import ToolStateManager, ToolSwitchResult


CommandResult = Union[EventEnvelope, Awaitable[EventEnvelope]]
CommandHandler = Callable[[CommandEnvelope], CommandResult]


class KeyboardRuntime:
    """Runtime command surface for keyboard-owned focus state."""

    def __init__(
        self,
        state_store: StateStore,
        event_bus: Optional[EventBus] = None,
        focus_manager: Optional[FocusManager] = None,
        tool_state: Optional[ToolStateManager] = None,
        target_resolver: Optional[TargetResolver] = None,
    ) -> None:
        self.state_store = state_store
        self.event_bus = event_bus
        self.focus_manager = focus_manager or FocusManager()
        self.tool_state = tool_state or ToolStateManager()
        self.target_resolver = target_resolver or TargetResolver()

    def register_focus_handlers(self, router: CommandRouter) -> None:
        router.register("agent.focus.set", self.set_focus)
        router.register("agent.focus.next_session", self.next_session)

    def register_tool_handlers(self, router: CommandRouter) -> None:
        router.register("keyboard.tool.switch", self.switch_tool)
        router.register("keyboard.tool.next", self.next_tool)

    def register_targeted_handlers(
        self,
        router: CommandRouter,
        handlers: Mapping[str, CommandHandler],
    ) -> None:
        for command_type, handler in handlers.items():
            router.register(command_type, self._with_resolved_target(handler))

    def set_focus(self, command: CommandEnvelope) -> EventEnvelope:
        raw_focus = self.focus_manager.set_focus(self._focus_from_command(command))
        focus = self._resolve_focus_from_state(
            raw_focus.device_id,
            fallback_missing=False,
            fallback_to_dashboard=False,
        )
        if not self._focus_ancestry_is_safe(focus):
            focus = self.focus_manager.set_focus(raw_focus)
        return self._focus_changed_event(focus)

    def next_session(self, command: CommandEnvelope) -> EventEnvelope:
        device_id = self._device_id(command)
        focus = self.focus_manager.next_session(device_id, self._session_records())
        if focus is None:
            return self._unresolved_event(
                command,
                TargetResolution.unresolved("focused_session", "no sessions are available"),
            )
        return self._focus_changed_event(focus)

    def switch_tool(self, command: CommandEnvelope) -> EventEnvelope:
        device_id = self._device_id(command)
        tool_id = self._tool_id(command)
        if tool_id is None:
            return self._tool_rejected_event(
                command,
                device_id=device_id,
                tool_id=None,
                code="missing_tool",
                message="keyboard.tool.switch requires a tool_id",
            )
        if tool_id not in self.tool_state.configured_tools(device_id):
            return self._tool_rejected_event(
                command,
                device_id=device_id,
                tool_id=tool_id,
                code="unknown_tool",
                message=f"unknown tool for device {device_id}: {tool_id}",
            )
        return self._tool_changed_event(
            self.tool_state.switch(device_id, tool_id),
            command=command,
        )

    def next_tool(self, command: CommandEnvelope) -> EventEnvelope:
        device_id = self._device_id(command)
        return self._tool_changed_event(
            self.tool_state.next(device_id),
            command=command,
        )

    def _with_resolved_target(self, handler: CommandHandler) -> CommandHandler:
        def wrapped(command: CommandEnvelope) -> CommandResult:
            selector = symbolic_selector(command.target)
            if selector is None:
                return handler(command)

            resolution = self._resolve_symbolic_command_target(command, selector)
            if not resolution.resolved:
                return self._unresolved_event(command, resolution)

            return handler(replace(command, target=resolution.target))

        return wrapped

    def _resolve_symbolic_command_target(
        self,
        command: CommandEnvelope,
        selector: str,
    ) -> TargetResolution:
        device_id = self._device_id(command)
        previous_focus = self.focus_manager.get_focus(device_id)
        focus = self._resolve_focus_from_state(device_id)
        focus_changed = self._focus_identity(previous_focus) != self._focus_identity(focus)
        if not self._focus_ancestry_is_safe(focus):
            self.focus_manager.set_focus(previous_focus)
            return TargetResolution.unresolved(selector, "focused target conflicts with parent focus scope")

        resolution = self.target_resolver.resolve(
            selector,
            focus=focus,
            instances=self.state_store.agents,
            sessions=self.state_store.sessions,
            runs=self.state_store.runs,
            permissions=self.state_store.permissions.values(),
        )
        if focus_changed:
            if not resolution.resolved:
                self.focus_manager.set_focus(previous_focus)
                return resolution
            self._publish_focus_changed(focus)
        return resolution

    def _resolve_focus_from_state(
        self,
        device_id: str,
        *,
        fallback_missing: bool = True,
        fallback_to_dashboard: bool = True,
    ) -> ScreenFocus:
        return self.focus_manager.resolve_focus(
            device_id,
            existing_instances=self.state_store.agents,
            existing_sessions=self.state_store.sessions,
            existing_runs=self.state_store.runs,
            fallback_missing=fallback_missing,
            fallback_to_dashboard=fallback_to_dashboard,
        )

    def _focus_from_command(self, command: CommandEnvelope) -> ScreenFocus:
        target = self._target_mapping(command)
        payload = command.payload
        instance_id = self._first_str(payload, target, key="instance_id")
        session_id = self._first_str(payload, target, key="session_id")
        run_id = self._first_str(payload, target, key="run_id")
        mode = self._first_str(payload, target, key="mode") or self._mode_for(
            instance_id=instance_id,
            session_id=session_id,
            run_id=run_id,
        )
        return ScreenFocus(
            device_id=self._device_id(command),
            mode=mode,
            instance_id=instance_id,
            session_id=session_id,
            run_id=run_id,
            selected_notification_id=self._first_str(payload, target, key="selected_notification_id"),
        )

    def _focus_changed_event(self, focus: ScreenFocus) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="agent.focus.changed",
            target={"device_id": focus.device_id},
            payload=focus.to_dict(),
        )

    def _tool_changed_event(
        self,
        result: ToolSwitchResult,
        *,
        command: CommandEnvelope,
    ) -> EventEnvelope:
        return EventEnvelope(
            seq=0,
            type="keyboard.tool.changed",
            target={"device_id": result.device_id},
            payload={
                "command_id": command.command_id,
                "device_id": result.device_id,
                "tool_id": result.tool_id,
                "previous_tool_id": result.previous_tool_id,
                "configured_tools": list(self.tool_state.configured_tools(result.device_id)),
            },
        )

    def _tool_rejected_event(
        self,
        command: CommandEnvelope,
        *,
        device_id: str,
        tool_id: Optional[str],
        code: str,
        message: str,
    ) -> EventEnvelope:
        payload = {
            "code": code,
            "command_id": command.command_id,
            "command_type": command.type,
            "device_id": device_id,
            "message": message,
        }
        if tool_id is not None:
            payload["tool_id"] = tool_id
        return EventEnvelope(
            seq=0,
            type="keyboard.tool.rejected",
            target={"device_id": device_id},
            payload=payload,
        )

    def _publish_focus_changed(self, focus: ScreenFocus) -> None:
        event = self._focus_changed_event(focus)
        self.state_store.apply_event(event)
        if self.event_bus is not None:
            self.event_bus.publish(event)

    def _unresolved_event(
        self,
        command: CommandEnvelope,
        resolution: TargetResolution,
    ) -> EventEnvelope:
        device_id = self._device_id(command)
        message = resolution.reason or f"could not resolve target selector: {resolution.selector}"
        return EventEnvelope(
            seq=0,
            type="command.target.unresolved",
            target={"device_id": device_id, "selector": resolution.selector},
            payload={
                "code": resolution.code,
                "command_id": command.command_id,
                "command_type": command.type,
                "device_id": device_id,
                "selector": resolution.selector,
                "message": message,
            },
        )

    def _device_id(self, command: CommandEnvelope) -> str:
        target = self._target_mapping(command)
        return (
            self._first_str(target, command.payload, key="device_id")
            or command.source.device_id
            or command.source.client_id
            or "default"
        )

    def _tool_id(self, command: CommandEnvelope) -> Optional[str]:
        target = self._target_mapping(command)
        return self._first_str(target, command.payload, key="tool_id")

    def _session_records(self) -> list:
        records = []
        for session_id, value in self.state_store.sessions.items():
            if isinstance(value, Mapping):
                record = dict(value)
            else:
                to_dict = getattr(value, "to_dict", None)
                record = dict(to_dict()) if callable(to_dict) else {}
            record.setdefault("session_id", session_id)
            records.append(record)
        return records

    @staticmethod
    def _target_mapping(command: CommandEnvelope) -> Dict[str, Any]:
        return dict(command.target) if isinstance(command.target, Mapping) else {}

    @staticmethod
    def _first_str(*containers: Mapping[str, Any], key: str) -> Optional[str]:
        for container in containers:
            value = container.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _mode_for(
        *,
        instance_id: Optional[str],
        session_id: Optional[str],
        run_id: Optional[str],
    ) -> str:
        if run_id:
            return "run"
        if session_id:
            return "session"
        if instance_id:
            return "instance"
        return "global_dashboard"

    @staticmethod
    def _focus_identity(focus: ScreenFocus) -> tuple:
        return (
            focus.device_id,
            focus.mode,
            focus.instance_id,
            focus.session_id,
            focus.run_id,
            focus.selected_notification_id,
        )

    def _focus_ancestry_is_safe(self, focus: ScreenFocus) -> bool:
        if focus.run_id:
            run = self._record_by_id(self.state_store.runs, focus.run_id, "run_id")
            if run and not self._record_matches_focus_parents(
                run,
                focus,
                parent_fields=("session_id", "instance_id"),
            ):
                return False
        if focus.session_id:
            session = self._record_by_id(self.state_store.sessions, focus.session_id, "session_id")
            if session and not self._record_matches_focus_parents(
                session,
                focus,
                parent_fields=("instance_id",),
            ):
                return False
        return True

    def _record_by_id(
        self,
        records: Mapping[str, Any],
        record_id: str,
        id_field: str,
    ) -> Dict[str, Any]:
        value = records.get(record_id)
        if isinstance(value, Mapping):
            record = dict(value)
        else:
            to_dict = getattr(value, "to_dict", None)
            record = dict(to_dict()) if callable(to_dict) else {}
        if value is not None:
            record.setdefault(id_field, record_id)
        return record

    @staticmethod
    def _record_matches_focus_parents(
        record: Mapping[str, Any],
        focus: ScreenFocus,
        *,
        parent_fields: tuple,
    ) -> bool:
        for field in parent_fields:
            focus_value = getattr(focus, field, None)
            record_value = record.get(field)
            if focus_value and record_value and record_value != focus_value:
                return False
        return True
