import json
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


def test_create_persists_readable_session_json(tmpdir):
    persist_dir = Path(str(tmpdir))
    manager = SessionManager(max_sessions=50, persist_dir=str(persist_dir))

    session = manager.create(AgentType.CODEX)
    path = persist_dir / "sessions.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data == [session.to_dict()]
    assert set(data[0].keys()) == {"session_id", "agent", "state", "created_at", "updated_at"}


def test_new_manager_restores_persisted_session_metadata(tmpdir):
    persist_dir = Path(str(tmpdir))
    manager = SessionManager(max_sessions=50, persist_dir=str(persist_dir))
    session = manager.create(AgentType.CLAUDE)
    manager.update_state(session.session_id, AgentState.WORKING)

    restored = SessionManager(max_sessions=50, persist_dir=str(persist_dir))
    restored_session = restored.get(session.session_id)

    assert restored_session is not None
    assert restored_session.agent == AgentType.CLAUDE
    assert restored_session.state == AgentState.WORKING
    assert restored_session.created_at == int(session.created_at)
    assert restored_session.updated_at >= int(session.updated_at)


def test_transient_session_fields_are_not_persisted(tmpdir):
    persist_dir = Path(str(tmpdir))
    manager = SessionManager(max_sessions=50, persist_dir=str(persist_dir))
    session = manager.create(AgentType.CODEX)

    manager.update_delta(session.session_id, "stream text")
    manager.set_process_pid(session.session_id, 12345)
    manager.update_state(session.session_id, AgentState.RUNNING)

    raw = json.loads((persist_dir / "sessions.json").read_text(encoding="utf-8"))
    restored = SessionManager(max_sessions=50, persist_dir=str(persist_dir)).get(session.session_id)

    assert "last_delta" not in raw[0]
    assert "process_pid" not in raw[0]
    assert restored is not None
    assert restored.last_delta == ""
    assert restored.process_pid is None


def test_corrupt_persistence_file_warns_and_starts_empty(tmpdir, capsys):
    persist_dir = Path(str(tmpdir))
    persist_dir.mkdir(parents=True, exist_ok=True)
    (persist_dir / "sessions.json").write_text("{bad json", encoding="utf-8")

    manager = SessionManager(max_sessions=50, persist_dir=str(persist_dir))
    captured = capsys.readouterr()

    assert manager.count() == 0
    assert "load warning" in captured.out


def test_load_skips_bad_session_entries_and_keeps_valid_entries(tmpdir, capsys):
    persist_dir = Path(str(tmpdir))
    persist_dir.mkdir(parents=True, exist_ok=True)
    path = persist_dir / "sessions.json"
    path.write_text(json.dumps([
        {
            "session_id": "sess_good",
            "agent": "codex",
            "state": "WORKING",
            "created_at": 10,
            "updated_at": 20,
            "last_delta": "not restored",
            "process_pid": 999,
        },
        {
            "session_id": "sess_bad_agent",
            "agent": "unknown",
            "state": "WORKING",
            "created_at": 30,
            "updated_at": 40,
        },
        {
            "session_id": "sess_bad_state",
            "agent": "claude",
            "state": "NOT_A_STATE",
            "created_at": 50,
            "updated_at": 60,
        },
        {
            "session_id": "sess_old_format",
            "agent": "claude",
        },
    ]), encoding="utf-8")

    manager = SessionManager(max_sessions=50, persist_dir=str(persist_dir))
    captured = capsys.readouterr()

    assert manager.count() == 2
    assert manager.get("sess_good").state == AgentState.WORKING
    assert manager.get("sess_good").last_delta == ""
    assert manager.get("sess_good").process_pid is None
    assert manager.get("sess_old_format").state == AgentState.IDLE
    assert "skipped session entry" in captured.out

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert {item["session_id"] for item in persisted} == {"sess_good", "sess_old_format"}


def test_load_enforces_max_sessions_and_keeps_most_recent(tmpdir):
    persist_dir = Path(str(tmpdir))
    persist_dir.mkdir(parents=True, exist_ok=True)
    (persist_dir / "sessions.json").write_text(json.dumps([
        {
            "session_id": "sess_old",
            "agent": "codex",
            "state": "IDLE",
            "created_at": 1,
            "updated_at": 1,
        },
        {
            "session_id": "sess_mid",
            "agent": "claude",
            "state": "WORKING",
            "created_at": 2,
            "updated_at": 2,
        },
        {
            "session_id": "sess_new",
            "agent": "codex",
            "state": "RUNNING",
            "created_at": 3,
            "updated_at": 3,
        },
    ]), encoding="utf-8")

    manager = SessionManager(max_sessions=2, persist_dir=str(persist_dir))
    session_ids = {session.session_id for session in manager.list_all()}
    persisted_ids = {
        item["session_id"]
        for item in json.loads((persist_dir / "sessions.json").read_text(encoding="utf-8"))
    }

    assert session_ids == {"sess_mid", "sess_new"}
    assert persisted_ids == session_ids


def test_gc_updates_persisted_session_file(tmpdir):
    persist_dir = Path(str(tmpdir))
    manager = SessionManager(max_sessions=50, persist_dir=str(persist_dir), cleanup_after_hours=1)
    stale = manager.create(AgentType.CODEX)
    active = manager.create(AgentType.CLAUDE)

    manager.update_state(stale.session_id, AgentState.COMPLETED)
    manager.update_state(active.session_id, AgentState.WORKING)
    with manager._lock:
        manager._sessions[stale.session_id].updated_at = time.time() - 7200

    assert manager.gc() == 1
    data = json.loads((persist_dir / "sessions.json").read_text(encoding="utf-8"))

    assert {item["session_id"] for item in data} == {active.session_id}
