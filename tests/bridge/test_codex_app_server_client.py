import asyncio
import json
from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from agents.codex_app_server import CodexAppServerClient  # noqa: E402


class FakeWriter:
    def __init__(self):
        self.writes = []
        self.drains = 0

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        self.drains += 1


def decode_write(writer, index=-1):
    return json.loads(writer.writes[index].decode("utf-8"))


def test_jsonrpc_request_response_id_correlation():
    async def run():
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer)
        reader_task = asyncio.create_task(client.read_loop())
        request_task = asyncio.create_task(client.send_request("initialize", {"hello": True}))

        await asyncio.sleep(0)
        sent = decode_write(writer)
        assert sent["method"] == "initialize"
        assert sent["params"] == {"hello": True}
        assert "id" in sent

        reader.feed_data((json.dumps({"id": sent["id"], "result": {"ok": True}}) + "\n").encode("utf-8"))
        result = await request_task
        reader.feed_eof()
        await reader_task
        assert result == {"ok": True}

    asyncio.run(run())


def test_server_request_callback_receives_command_approval_request():
    async def run():
        requests = []
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer, on_server_request=requests.append)
        reader_task = asyncio.create_task(client.read_loop())

        message = {
            "id": "approval_1",
            "method": "item/commandExecution/requestApproval",
            "params": {
                "threadId": "thread_1",
                "turnId": "turn_1",
                "itemId": "item_1",
                "command": "python -c \"print(1)\"",
                "cwd": "C:/repo",
            },
        }
        reader.feed_data((json.dumps(message) + "\n").encode("utf-8"))
        await asyncio.sleep(0)
        reader.feed_eof()
        await reader_task

        assert requests == [message]

    asyncio.run(run())


def test_server_notification_callback_receives_thread_started():
    async def run():
        notifications = []
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer, on_notification=notifications.append)
        reader_task = asyncio.create_task(client.read_loop())

        message = {"method": "thread/started", "params": {"thread": {"id": "thread_1"}}}
        reader.feed_data((json.dumps(message) + "\n").encode("utf-8"))
        await asyncio.sleep(0)
        reader.feed_eof()
        await reader_task

        assert notifications == [message]

    asyncio.run(run())


def test_send_response_writes_newline_delimited_json():
    async def run():
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer)

        await client.send_response("approval_1", {"decision": "accept"})

        assert writer.writes[0].endswith(b"\n")
        assert decode_write(writer) == {"id": "approval_1", "result": {"decision": "accept"}}
        assert writer.drains == 1

    asyncio.run(run())


def test_initialize_payload_matches_codex_app_server_schema():
    async def run():
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer)
        reader_task = asyncio.create_task(client.read_loop())
        init_task = asyncio.create_task(client.initialize())

        await asyncio.sleep(0)
        sent = decode_write(writer)
        assert sent["method"] == "initialize"
        assert sent["params"]["clientInfo"] == {
            "name": "ai-keyboard-local-core",
            "title": None,
            "version": "1.0",
        }
        reader.feed_data((json.dumps({"id": sent["id"], "result": {"ok": True}}) + "\n").encode("utf-8"))
        await init_task
        reader.feed_eof()
        await reader_task
        assert decode_write(writer, 1)["method"] == "initialized"

    asyncio.run(run())


def test_stream_close_fails_pending_request():
    async def run():
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer)
        reader_task = asyncio.create_task(client.read_loop())
        request_task = asyncio.create_task(client.send_request("thread/start", {"cwd": "C:/repo"}))

        await asyncio.sleep(0)
        reader.feed_eof()
        await reader_task

        try:
            await request_task
        except RuntimeError as exc:
            assert "stream closed" in str(exc)
        else:
            raise AssertionError("pending request completed successfully after stream close")

    asyncio.run(run())


def test_send_request_times_out_and_clears_pending():
    async def run():
        reader = asyncio.StreamReader()
        writer = FakeWriter()
        client = CodexAppServerClient(reader, writer, request_timeout_sec=0.01)

        try:
            await client.send_request("initialize", {"hello": True})
        except TimeoutError as exc:
            assert "initialize" in str(exc)
        else:
            raise AssertionError("request completed without a response")

        assert client._pending == {}

    asyncio.run(run())
