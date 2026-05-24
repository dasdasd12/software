"""Codex app-server JSON-RPC helpers."""

import asyncio
import json
from typing import Any, Callable, Dict, Optional


JsonMessage = Dict[str, Any]
MessageCallback = Callable[[JsonMessage], Any]


class CodexAppServerClient:
    """Small JSON-RPC client for `codex app-server --listen stdio://`."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: Any,
        on_server_request: Optional[MessageCallback] = None,
        on_notification: Optional[MessageCallback] = None,
        request_timeout_sec: float = 60.0,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._on_server_request = on_server_request
        self._on_notification = on_notification
        self._request_timeout_sec = request_timeout_sec
        self._next_id = 1
        self._pending: Dict[str, asyncio.Future] = {}

    async def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        request_id = f"codex_app_{self._next_id}"
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        payload: JsonMessage = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)
        try:
            return await asyncio.wait_for(future, timeout=self._request_timeout_sec)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise TimeoutError(f"Codex app-server request timed out: {method}") from exc
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise

    async def send_notification(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: JsonMessage = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._write(payload)

    async def send_response(self, request_id: Any, result: Dict[str, Any]) -> None:
        await self._write({"id": request_id, "result": result})

    async def initialize(self) -> Any:
        result = await self.send_request(
            "initialize",
            {
                "clientInfo": {"name": "ai-keyboard-local-core", "title": None, "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.send_notification("initialized")
        return result

    async def start_thread(
        self,
        cwd: str,
        approval_policy: str = "untrusted",
        approvals_reviewer: str = "user",
        sandbox: str = "workspace-write",
    ) -> Any:
        return await self.send_request(
            "thread/start",
            {
                "cwd": cwd,
                "approvalPolicy": approval_policy,
                "approvalsReviewer": approvals_reviewer,
                "sandbox": sandbox,
                "experimentalRawEvents": False,
                "persistExtendedHistory": True,
            },
        )

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        cwd: str,
        approval_policy: str = "untrusted",
        approvals_reviewer: str = "user",
        sandbox_policy: Optional[Dict[str, Any]] = None,
    ) -> Any:
        params: JsonMessage = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
            "cwd": cwd,
            "approvalPolicy": approval_policy,
            "approvalsReviewer": approvals_reviewer,
        }
        if sandbox_policy is not None:
            params["sandboxPolicy"] = sandbox_policy
        return await self.send_request("turn/start", params)

    async def read_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(message)
        except Exception as exc:
            self._fail_pending(exc)
            raise
        else:
            self._fail_pending(RuntimeError("Codex app-server stream closed"))

    async def _dispatch(self, message: JsonMessage) -> None:
        message_id = message.get("id")
        if message_id is not None and "method" in message:
            if self._on_server_request:
                result = self._on_server_request(message)
                if asyncio.iscoroutine(result):
                    await result
            return

        if message_id is not None and ("result" in message or "error" in message):
            future = self._pending.pop(str(message_id), None)
            if future and not future.done():
                if "error" in message:
                    future.set_exception(RuntimeError(str(message["error"])))
                else:
                    future.set_result(message.get("result"))
            return

        if "method" in message and self._on_notification:
            result = self._on_notification(message)
            if asyncio.iscoroutine(result):
                await result

    async def _write(self, payload: JsonMessage) -> None:
        self._writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        drain = getattr(self._writer, "drain", None)
        if drain is not None:
            result = drain()
            if asyncio.iscoroutine(result):
                await result

    def _fail_pending(self, exc: Exception) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)
