import asyncio
from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from agents import CodexAppServerPermissionAdapter  # noqa: E402


def test_command_execution_approval_emits_permission_request_and_forwards_accept():
    events = []
    writes = []
    adapter = CodexAppServerPermissionAdapter(events.append, writes.append)
    native = {
        "id": "jsonrpc_1",
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "thread_1",
            "turnId": "turn_1",
            "itemId": "item_1",
            "command": "python -c \"print('codex approval smoke')\"",
            "cwd": "C:/repo",
            "reason": "needs command approval",
        },
    }

    adapter.register_server_request("sess_1", native)

    assert events[0]["type"] == "permission_request"
    assert events[0]["request_id"] == "jsonrpc_1"
    assert events[0]["session_id"] == "sess_1"
    assert events[0]["agent"] == "codex"
    assert events[0]["tool"] == "shell"
    assert events[0]["risk_level"] == "high"

    result = asyncio.run(adapter.forward_permission_response("sess_1", "jsonrpc_1", True))

    assert writes == [{"id": "jsonrpc_1", "result": {"decision": "accept"}}]
    assert result["accepted"] is True
    assert result["forwarded"] is True
    assert result["evidence"]["adapter"] == "codex_app_server"
    assert result["evidence"]["native_channel"] == "item/commandExecution/requestApproval"
    assert result["evidence"]["jsonrpc_id"] == "jsonrpc_1"
    assert result["evidence"]["thread_id"] == "thread_1"
    assert result["evidence"]["turn_id"] == "turn_1"
    assert result["evidence"]["item_id"] == "item_1"
    assert result["evidence"]["command"] == "python -c \"print('codex approval smoke')\""
    assert result["evidence"]["cwd"] == "C:/repo"
    assert result["evidence"]["decision_delivered"] is True
    assert result["evidence"]["response_written"] is True


def test_command_execution_approval_forwards_decline():
    writes = []
    adapter = CodexAppServerPermissionAdapter(lambda event: None, writes.append)
    adapter.register_server_request("sess_1", {
        "id": "jsonrpc_2",
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "thread_1",
            "turnId": "turn_1",
            "itemId": "item_2",
            "command": "python -c \"print('deny')\"",
            "cwd": "C:/repo",
        },
    })

    result = asyncio.run(adapter.forward_permission_response("sess_1", "jsonrpc_2", False))

    assert writes == [{"id": "jsonrpc_2", "result": {"decision": "decline"}}]
    assert result["forwarded"] is True
    assert result["evidence"]["decision"] == "decline"


def test_legacy_exec_command_approval_forwards_approved_decision():
    writes = []
    adapter = CodexAppServerPermissionAdapter(lambda event: None, writes.append)
    adapter.register_server_request("sess_1", {
        "id": "legacy_1",
        "method": "execCommandApproval",
        "params": {
            "conversationId": "thread_legacy",
            "callId": "call_1",
            "approvalId": "approval_1",
            "command": ["python", "-c", "print('legacy')"],
            "cwd": "C:/repo",
            "reason": "legacy approval",
        },
    })

    result = asyncio.run(adapter.forward_permission_response("sess_1", "legacy_1", True))

    assert writes == [{"id": "legacy_1", "result": {"decision": "approved"}}]
    assert result["forwarded"] is True
    assert result["evidence"]["native_channel"] == "execCommandApproval"


def test_legacy_apply_patch_approval_forwards_denied_decision():
    events = []
    writes = []
    adapter = CodexAppServerPermissionAdapter(events.append, writes.append)
    adapter.register_server_request("sess_1", {
        "id": "legacy_patch_1",
        "method": "applyPatchApproval",
        "params": {
            "conversationId": "thread_legacy",
            "callId": "call_patch_1",
            "approvalId": "approval_patch_1",
            "filePath": "src/example.py",
        },
    })

    result = asyncio.run(adapter.forward_permission_response("sess_1", "legacy_patch_1", False))

    assert events[0]["tool"] == "file_change"
    assert events[0]["description"] == "src/example.py"
    assert writes == [{"id": "legacy_patch_1", "result": {"decision": "denied"}}]
    assert result["forwarded"] is True
    assert result["evidence"]["native_channel"] == "applyPatchApproval"
    assert result["evidence"]["decision"] == "denied"


def test_item_permissions_approval_maps_to_permission_request():
    events = []
    writes = []
    adapter = CodexAppServerPermissionAdapter(events.append, writes.append)
    adapter.register_server_request("sess_1", {
        "id": 4,
        "method": "item/permissions/requestApproval",
        "params": {
            "threadId": "thread_1",
            "turnId": "turn_1",
            "itemId": "item_permissions_1",
            "cwd": "C:/repo",
        },
    })

    result = asyncio.run(adapter.forward_permission_response("sess_1", "4", True))

    assert events[0]["tool"] == "permission"
    assert events[0]["native"]["jsonrpc_id"] == 4
    assert writes == [{"id": 4, "result": {"decision": "accept"}}]
    assert result["forwarded"] is True
    assert result["evidence"]["native_channel"] == "item/permissions/requestApproval"


def test_expired_codex_permission_declines_native_request():
    writes = []
    adapter = CodexAppServerPermissionAdapter(lambda event: None, writes.append)
    adapter.register_server_request("sess_1", {
        "id": "jsonrpc_expired",
        "method": "item/commandExecution/requestApproval",
        "params": {"command": "python -V"},
    })

    result = asyncio.run(adapter.expire_permission_request("sess_1", "jsonrpc_expired"))
    retry = asyncio.run(adapter.forward_permission_response("sess_1", "jsonrpc_expired", True))

    assert writes == [{"id": "jsonrpc_expired", "result": {"decision": "decline"}}]
    assert result["forwarded"] is True
    assert result["evidence"]["expired"] is True
    assert retry["forwarded"] is False


def test_native_writer_receives_session_when_supported():
    writes = []

    def writer(payload, session_id):
        writes.append((payload, session_id))

    adapter = CodexAppServerPermissionAdapter(lambda event: None, writer)
    adapter.register_server_request("sess_1", {
        "id": "jsonrpc_3",
        "method": "item/commandExecution/requestApproval",
        "params": {"command": "python -V"},
    })

    result = asyncio.run(adapter.forward_permission_response("sess_1", "jsonrpc_3", True))

    assert writes == [({"id": "jsonrpc_3", "result": {"decision": "accept"}}, "sess_1")]
    assert result["forwarded"] is True


def test_unknown_codex_permission_request_does_not_claim_forwarded():
    adapter = CodexAppServerPermissionAdapter(lambda event: None, lambda payload: None)

    result = asyncio.run(adapter.forward_permission_response("sess_1", "missing", True))

    assert result["accepted"] is True
    assert result["forwarded"] is False
    assert result["evidence"]["adapter"] == "codex_app_server"
    assert result["evidence"]["reason"] == "native_request_not_registered"
