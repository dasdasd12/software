"""Adapter boundary for agent lifecycle side effects."""

from typing import Any, Callable, Mapping, Optional


class AgentLifecycleError(Exception):
    """Domain error that can be serialized by Local API handlers."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class AgentRuntime:
    """Thin boundary over the existing session manager and agent controllers."""

    def __init__(
        self,
        session_manager: Any,
        controllers: Mapping[Any, Any],
        persist_session: Optional[Callable[[str], None]] = None,
    ):
        self.session_manager = session_manager
        self.controllers = controllers
        self._persist_session = persist_session or (lambda _session_id: None)

    def resolve_agent_key(self, agent: Any) -> Any:
        if agent is None:
            raise AgentLifecycleError("AGENT_NOT_FOUND", "agent is required")
        requested = str(agent)
        for key in self.controllers.keys():
            if requested in {self.agent_value(key), getattr(key, "name", "").lower(), str(key)}:
                return key
        raise AgentLifecycleError("AGENT_NOT_FOUND", f"{requested} is not configured")

    def require_controller(self, agent_key: Any) -> Any:
        controller = self.controllers.get(agent_key)
        if controller is None:
            raise AgentLifecycleError("AGENT_NOT_FOUND", f"{self.agent_value(agent_key)} is not configured")
        is_available = getattr(controller, "is_available", None)
        if callable(is_available) and not is_available():
            raise AgentLifecycleError(
                "AGENT_UNAVAILABLE",
                f"{self.agent_value(agent_key)} executable not found",
            )
        return controller

    def create_session(self, agent_key: Any) -> Any:
        return self.session_manager.create(agent_key)

    def require_session(self, session_id: str) -> Any:
        session = self.session_manager.get(session_id)
        if not session:
            raise AgentLifecycleError("SESSION_NOT_FOUND", f"Session {session_id} not found")
        return session

    def update_state(self, session_id: str, state_name: str) -> Any:
        session = self.require_session(session_id)
        self.session_manager.update_state(session_id, self._state_value(session, state_name))
        return self.require_session(session_id)

    def persist(self, session_id: str) -> None:
        self._persist_session(session_id)

    def session_payload(self, session_id: str, **extra: Any) -> dict:
        session = self.require_session(session_id)
        payload = session.to_dict()
        payload.update(extra)
        return payload

    @staticmethod
    def agent_value(agent_key: Any) -> str:
        value = getattr(agent_key, "value", None)
        return str(value if value is not None else agent_key)

    @staticmethod
    def _state_value(session: Any, state_name: str) -> Any:
        state_type = type(session.state)
        try:
            return state_type[state_name]
        except (KeyError, TypeError):
            return state_type(state_name)
