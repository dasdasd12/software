"""
Session Manager — Bridge Server

Maintains session state table, handles persistence, and performs LRU cleanup.
Matches device-side AGENT_SESSION_CACHE_MAX = 50.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional


class AgentType(Enum):
    CLAUDE = "claude"
    CODEX = "codex"


class AgentState(Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    RUNNING = "RUNNING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    WAITING_INPUT = "WAITING_INPUT"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    OFFLINE = "OFFLINE"


@dataclass
class Session:
    session_id: str
    agent: AgentType
    state: AgentState = AgentState.IDLE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    process_pid: Optional[int] = None
    # Transient: not persisted
    last_delta: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "agent": self.agent.value,
            "state": self.state.value,
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
        }

    @staticmethod
    def from_dict(data: dict) -> "Session":
        if not isinstance(data, dict):
            raise ValueError("session entry must be an object")

        session_id = data.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")

        try:
            agent = AgentType(data["agent"])
        except KeyError as exc:
            raise ValueError("agent is required") from exc
        except ValueError as exc:
            raise ValueError(f"unknown agent: {data.get('agent')}") from exc

        state_raw = data.get("state", AgentState.IDLE.value)
        try:
            state = AgentState(state_raw)
        except ValueError as exc:
            raise ValueError(f"unknown state: {state_raw}") from exc

        created_at = data.get("created_at", 0)
        updated_at = data.get("updated_at", created_at)
        try:
            created_at = float(created_at)
            updated_at = float(updated_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("created_at and updated_at must be numeric") from exc

        return Session(
            session_id=session_id,
            agent=agent,
            state=state,
            created_at=created_at,
            updated_at=updated_at,
        )


class SessionManager:
    """Thread-safe session manager with LRU eviction and optional disk persistence."""

    def __init__(
        self,
        max_sessions: int = 50,
        persist_dir: Optional[str] = None,
        cleanup_after_hours: int = 24,
    ):
        self._sessions: Dict[str, Session] = {}
        self._lock = Lock()
        self._max_sessions = max_sessions
        self._persist_dir = Path(persist_dir) if persist_dir else None
        self._cleanup_after_hours = cleanup_after_hours

        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    # ------------------------------------------------------------------ #
    #  CRUD
    # ------------------------------------------------------------------ #

    def create(self, agent: AgentType) -> Session:
        """Create a new session with a unique ID."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        session = Session(session_id=session_id, agent=agent)
        with self._lock:
            self._sessions[session_id] = session
            self._enforce_limit(locked=True)
        self._persist()
        return session

    def restore(self, session: Session) -> None:
        """Restore a previously persisted session into the in-memory cache."""
        with self._lock:
            self._sessions[session.session_id] = session
            self._enforce_limit(locked=True)

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def update_state(self, session_id: str, state: AgentState) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            sess.state = state
            sess.updated_at = time.time()
        self._persist()
        return True

    def update_delta(self, session_id: str, delta: str) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            sess.last_delta = delta
            sess.updated_at = time.time()
        return True

    def set_process_pid(self, session_id: str, pid: int) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            sess.process_pid = pid
        return True

    def delete(self, session_id: str) -> bool:
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
        if existed:
            self._persist()
        return existed

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def list_all(self) -> List[Session]:
        with self._lock:
            # Return sorted by updated_at descending (most recent first)
            return sorted(
                self._sessions.values(),
                key=lambda s: s.updated_at,
                reverse=True,
            )

    def list_by_agent(self, agent: AgentType) -> List[Session]:
        with self._lock:
            return sorted(
                [s for s in self._sessions.values() if s.agent == agent],
                key=lambda s: s.updated_at,
                reverse=True,
            )

    def get_latest(self, agent: Optional[AgentType] = None) -> Optional[Session]:
        sessions = self.list_by_agent(agent) if agent else self.list_all()
        return sessions[0] if sessions else None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ------------------------------------------------------------------ #
    #  Cleanup
    # ------------------------------------------------------------------ #

    def gc(self) -> int:
        """Remove stale completed/failed sessions and enforce LRU limit."""
        now = time.time()
        cutoff = now - (self._cleanup_after_hours * 3600)
        removed = 0

        with self._lock:
            # Phase 1: delete old terminal sessions
            terminal_states = {AgentState.COMPLETED, AgentState.FAILED,
                               AgentState.CANCELLED, AgentState.ERROR,
                               AgentState.TIMEOUT}
            to_remove = [
                sid for sid, s in self._sessions.items()
                if s.state in terminal_states and s.updated_at < cutoff
            ]
            for sid in to_remove:
                del self._sessions[sid]
                removed += 1

            # Phase 2: LRU eviction if still over limit
            self._enforce_limit(locked=True)

        if removed:
            self._persist()
        return removed

    def _enforce_limit(self, locked: bool = False) -> None:
        """Evict oldest sessions until under max_sessions."""
        def _do():
            while len(self._sessions) > self._max_sessions:
                oldest = min(self._sessions.values(), key=lambda s: s.updated_at)
                del self._sessions[oldest.session_id]

        if locked:
            _do()
        else:
            with self._lock:
                _do()

    # ------------------------------------------------------------------ #
    #  Persistence
    # ------------------------------------------------------------------ #

    def _persist(self) -> None:
        if not self._persist_dir:
            return
        try:
            path = self._persist_dir / "sessions.json"
            tmp_path = self._persist_dir / "sessions.json.tmp"
            with self._lock:
                data = [s.to_dict() for s in self._sessions.values()]
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(path)
        except Exception as exc:
            # Best-effort persistence; log but don't crash
            print(f"[SessionManager] persist warning: {exc}")

    def _load_from_disk(self) -> None:
        path = self._persist_dir / "sessions.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("sessions.json must contain a JSON array")

            skipped = 0
            with self._lock:
                for item in data:
                    try:
                        sess = Session.from_dict(item)
                    except Exception as exc:
                        skipped += 1
                        print(f"[SessionManager] load warning: skipped session entry: {exc}")
                        continue
                    self._sessions[sess.session_id] = sess
                before_limit = len(self._sessions)
                self._enforce_limit(locked=True)

            loaded = self.count()
            if before_limit != loaded:
                print(f"[SessionManager] load warning: trimmed {before_limit - loaded} sessions over cache limit")
                self._persist()
            if skipped:
                self._persist()
            print(f"[SessionManager] loaded {loaded} sessions from disk")
        except Exception as exc:
            print(f"[SessionManager] load warning: {exc}")
