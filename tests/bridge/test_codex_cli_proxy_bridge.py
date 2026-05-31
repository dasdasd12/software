import asyncio
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT_DIR / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from server import BridgeServer  # noqa: E402
from session_manager import AgentType  # noqa: E402


class AsyncCaptureQueue:
    def __init__(self):
        self.items = []
        self._queue = asyncio.Queue()

    async def put(self, item):
        self.items.append(item)
        await self._queue.put(item)

    def put_nowait(self, item):
        self.items.append(item)
        self._queue.put_nowait(item)

    async def next_json(self):
        return json.loads(await asyncio.wait_for(self._queue.get(), timeout=1.0))


def make_server():
    return BridgeServer({
        "server": {"host": "127.0.0.1", "port": 0},
        "agents": {"claude": {"enabled": False}, "codex": {"enabled": False}},
        "session": {"cache_size": 50, "cleanup_after_hours": 24},
        "unifier": {"max_delta_size": 2048, "permission_timeout_sec": 30},
        "logging": {"console": False},
    })


def register_codex_native_session(server):
    session = server.session_mgr.create(AgentType.CODEX)
    session.launch_surface = "foreground_cli"
    session.control_mode = "native_cli"
    session.frontend_pid = 2468
    server.agent_commands._foreground_hook_tokens_by_session_id[session.session_id] = "hook-token"
    server.agent_commands._foreground_root_pids_by_session_id[session.session_id] = 2468
    return session


async def wait_for_type(queue, expected_type):
    for _ in range(20):
        payload = await queue.next_json()
        if payload.get("type") == expected_type:
            return payload
        if payload.get("type") == "event" and isinstance(payload.get("event"), dict):
            if payload["event"].get("type") == expected_type:
                return payload["event"]
    raise AssertionError(f"did not receive {expected_type}")


def test_codex_cli_proxy_permission_round_trips_to_native_response():
    async def run():
        server = make_server()
        session = register_codex_native_session(server)
        hook_queue = AsyncCaptureQueue()
        desktop_queue = AsyncCaptureQueue()
        server.connected_clients.add(desktop_queue)
        server.register_client_identity(
            hook_queue,
            "agent-hook",
            f"codex-cli-proxy:{session.session_id}",
            {"codex:hook"},
        )
        server.register_client_identity(
            desktop_queue,
            "desktop-ui",
            "desktop",
            {"permission:respond"},
        )
        native_request = {
            "id": "jsonrpc_1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": "thread_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "command": "python -c \"print('codex')\"",
                "cwd": "C:/project",
            },
        }

        request_task = asyncio.create_task(server._cmd_codex_rpc_request({
            "type": "codex_rpc_request",
            "session_id": session.session_id,
            "request": native_request,
        }, hook_queue))
        permission = await wait_for_type(desktop_queue, "permission_request")
        assert permission["agent"] == "codex"
        assert permission["native"]["adapter"] == "codex_cli_proxy"

        response_task = asyncio.create_task(server._cmd_permission_response({
            "type": "permission_response",
            "session_id": session.session_id,
            "request_id": "jsonrpc_1",
            "approved": True,
        }, desktop_queue))
        result = await wait_for_type(hook_queue, "codex_rpc_result")
        assert result["native_response"] == {
            "id": "jsonrpc_1",
            "result": {"decision": "accept"},
        }
        await server._cmd_codex_rpc_delivered({
            "type": "codex_rpc_delivered",
            "session_id": session.session_id,
            "request_id": "jsonrpc_1",
            "response_written": True,
        }, hook_queue)
        await response_task
        await request_task
        ack = await wait_for_type(desktop_queue, "permission_ack")
        assert ack["forwarded"] is True
        assert ack["evidence"]["adapter"] == "codex_cli_proxy"
        assert ack["evidence"]["response_written"] is True

    asyncio.run(run())


def test_codex_cli_proxy_user_input_round_trips_to_native_response():
    async def run():
        server = make_server()
        session = register_codex_native_session(server)
        hook_queue = AsyncCaptureQueue()
        desktop_queue = AsyncCaptureQueue()
        server.connected_clients.add(desktop_queue)
        server.register_client_identity(
            hook_queue,
            "agent-hook",
            f"codex-cli-proxy:{session.session_id}",
            {"codex:hook"},
        )
        server.register_client_identity(
            desktop_queue,
            "desktop-ui",
            "desktop",
            {"permission:respond"},
        )
        native_request = {
            "id": "jsonrpc_2",
            "method": "item/tool/requestUserInput",
            "params": {
                "threadId": "thread_1",
                "turnId": "turn_1",
                "itemId": "item_2",
                "questions": [{
                    "id": "choice",
                    "header": "Mode",
                    "question": "Pick one",
                    "options": [{"label": "A", "description": "Use A"}],
                }],
            },
        }

        request_task = asyncio.create_task(server._cmd_codex_rpc_request({
            "type": "codex_rpc_request",
            "session_id": session.session_id,
            "request": native_request,
        }, hook_queue))
        interaction = await wait_for_type(desktop_queue, "interaction_request")
        assert interaction["agent"] == "codex"
        assert interaction["interaction_type"] == "request_user_input"
        assert interaction["questions"][0]["id"] == "choice"
        server._sync_runtime_state()
        snapshot = server.runtime.snapshot().to_dict()
        assert snapshot["interactions"][0]["request_id"] == "jsonrpc_2"
        assert snapshot["interactions"][0]["agent"] == "codex"
        assert snapshot["interactions"][0]["native"]["adapter"] == "codex_cli_proxy"
        assert snapshot["interactions"][0]["native"]["jsonrpc_id"] == "jsonrpc_2"
        assert snapshot["interactions"][0]["questions"][0]["id"] == "choice"

        response_task = asyncio.create_task(server._cmd_interaction_response({
            "type": "interaction_response",
            "session_id": session.session_id,
            "request_id": "jsonrpc_2",
            "approved": True,
            "answers": {"choice": ["A"]},
        }, desktop_queue))
        result = await wait_for_type(hook_queue, "codex_rpc_result")
        assert result["native_response"] == {
            "id": "jsonrpc_2",
            "result": {"answers": {"choice": {"answers": ["A"]}}},
        }
        await server._cmd_codex_rpc_delivered({
            "type": "codex_rpc_delivered",
            "session_id": session.session_id,
            "request_id": "jsonrpc_2",
            "response_written": True,
        }, hook_queue)
        await response_task
        await request_task
        ack = await wait_for_type(desktop_queue, "interaction_ack")
        assert ack["forwarded"] is True
        assert ack["evidence"]["adapter"] == "codex_cli_proxy"
        server._sync_runtime_state()
        assert server.runtime.snapshot().to_dict()["interactions"] == []

    asyncio.run(run())
