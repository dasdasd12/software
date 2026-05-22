"""In-memory agent registry scaffold."""

from typing import Dict, Optional

from .identity import AgentInstance, AgentProvider, AgentRun, AgentSession


class AgentRegistry:
    """Owns provider, instance, session, and run metadata."""

    def __init__(self) -> None:
        self.providers: Dict[str, AgentProvider] = {}
        self.instances: Dict[str, AgentInstance] = {}
        self.sessions: Dict[str, AgentSession] = {}
        self.runs: Dict[str, AgentRun] = {}

    def register_provider(self, provider: AgentProvider) -> None:
        self.providers[provider.provider_id] = provider

    def register_instance(self, instance: AgentInstance) -> None:
        if instance.provider_id not in self.providers:
            raise KeyError(f"unknown provider: {instance.provider_id}")
        self.instances[instance.instance_id] = instance

    def add_session(self, session: AgentSession) -> None:
        if session.instance_id not in self.instances:
            raise KeyError(f"unknown instance: {session.instance_id}")
        self.sessions[session.session_id] = session

    def add_run(self, run: AgentRun) -> None:
        if run.session_id not in self.sessions:
            raise KeyError(f"unknown session: {run.session_id}")
        self.runs[run.run_id] = run
        self.sessions[run.session_id].active_run_id = run.run_id

    def get_session_instance(self, session_id: str) -> Optional[AgentInstance]:
        session = self.sessions.get(session_id)
        if not session:
            return None
        return self.instances.get(session.instance_id)
