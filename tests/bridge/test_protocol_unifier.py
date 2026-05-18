import json
from pathlib import Path
import sys


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from protocol_unifier import ProtocolUnifier  # noqa: E402


def test_codex_agent_message_delta():
    unifier = ProtocolUnifier()
    event = unifier.codex_to_unified(
        json.dumps({"type": "item.agent_message", "item": {"text": "hello"}}),
        "sess_1",
    )

    assert event == {
        "type": "agent_message_delta",
        "session_id": "sess_1",
        "agent": "codex",
        "delta": "hello",
    }


def test_codex_turn_completed():
    unifier = ProtocolUnifier()
    event = unifier.codex_to_unified(
        json.dumps({"type": "turn.completed", "turn": {}}),
        "sess_1",
    )

    assert event["type"] == "task_completed"
    assert event["session_id"] == "sess_1"
    assert event["agent"] == "codex"


def test_claude_agent_message_delta():
    unifier = ProtocolUnifier()
    event = unifier.claude_to_unified(
        json.dumps({"type": "content_block_delta", "delta": {"text": "hi"}}),
        "sess_2",
    )

    assert event == {
        "type": "agent_message_delta",
        "session_id": "sess_2",
        "agent": "claude",
        "delta": "hi",
    }


def test_claude_error_becomes_task_failed():
    unifier = ProtocolUnifier()
    event = unifier.claude_to_unified(
        json.dumps({"type": "error", "code": "BAD", "message": "failed"}),
        "sess_2",
    )

    assert event == {
        "type": "task_failed",
        "session_id": "sess_2",
        "agent": "claude",
        "error_code": "BAD",
        "error_message": "failed",
    }
