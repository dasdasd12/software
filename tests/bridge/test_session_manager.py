import threading
import time
from pathlib import Path
import sys


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from session_manager import AgentState, AgentType, SessionManager  # noqa: E402


def test_create_returns_without_deadlock():
    manager = SessionManager(max_sessions=50)
    result = []

    thread = threading.Thread(
        target=lambda: result.append(manager.create(AgentType.CODEX)),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert len(result) == 1
    assert result[0].agent == AgentType.CODEX
    assert result[0].state == AgentState.IDLE


def test_lru_limit_evicts_oldest_session():
    manager = SessionManager(max_sessions=2)

    first = manager.create(AgentType.CODEX)
    time.sleep(0.001)
    second = manager.create(AgentType.CLAUDE)
    time.sleep(0.001)
    third = manager.create(AgentType.CODEX)

    session_ids = {session.session_id for session in manager.list_all()}

    assert manager.count() == 2
    assert first.session_id not in session_ids
    assert second.session_id in session_ids
    assert third.session_id in session_ids


def test_gc_removes_old_terminal_sessions():
    manager = SessionManager(max_sessions=50, cleanup_after_hours=1)
    stale = manager.create(AgentType.CODEX)
    active = manager.create(AgentType.CLAUDE)

    manager.update_state(stale.session_id, AgentState.COMPLETED)
    manager.update_state(active.session_id, AgentState.WORKING)

    with manager._lock:
        manager._sessions[stale.session_id].updated_at = time.time() - 7200

    removed = manager.gc()
    session_ids = {session.session_id for session in manager.list_all()}

    assert removed == 1
    assert stale.session_id not in session_ids
    assert active.session_id in session_ids
