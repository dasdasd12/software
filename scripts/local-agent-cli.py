#!/usr/bin/env python
"""Local API terminal host for managed foreground agent sessions."""

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, Optional


DEFAULT_API_URL = "ws://127.0.0.1:8765"
LAUNCH_TOKEN_ENV = "AI_KEYB_LAUNCH_TOKEN"
DEFAULT_CAPABILITIES = ["agent:launch", "permission:respond", "session:list"]
PRINT_EVENT_TYPES = {
    "agent_message_delta",
    "task_update",
    "permission_request",
    "permission_ack",
    "task_completed",
    "task_failed",
}


def now_ts() -> float:
    return time.time()


def parse_args(argv, env=None):
    parser = argparse.ArgumentParser(description="Run a managed Local API agent session in a terminal.")
    parser.add_argument("--agent", choices=["claude", "codex"], required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--client-kind", default="desktop-ui")
    parser.add_argument("--client-id", default="local-agent-cli")
    parser.add_argument("--context", default="")
    args = parser.parse_args(argv)
    args.token = (env or os.environ).get(LAUNCH_TOKEN_ENV, "")
    return args


def _command(command_type: str, payload: Optional[Dict[str, Any]] = None, target: Optional[Dict[str, Any]] = None):
    command = {
        "command_id": "cli_%s" % uuid.uuid4().hex,
        "type": command_type,
        "source": {"kind": "desktop-ui", "client_id": "local-agent-cli"},
        "payload": payload or {},
    }
    if target is not None:
        command["target"] = target
    return {"type": "command", "command": command, "timestamp": now_ts()}


def build_hello_message(client_kind: str = "desktop-ui", token: str = "", client_id: str = "local-agent-cli"):
    return {
        "type": "hello",
        "token": token or None,
        "client_kind": client_kind,
        "client_id": client_id,
        "capabilities": list(DEFAULT_CAPABILITIES),
        "timestamp": now_ts(),
    }


def build_launch_command(agent: str, workspace: str, context: str = ""):
    return _command(
        "agent.session.launch_or_resume",
        payload={
            "agent": agent,
            "workspace": workspace,
            "context": context,
            "launch_surface": "foreground_cli",
            "frontend_pid": os.getpid(),
        },
        target={"session_id": "new"},
    )


def build_input_command(session_id: str, text: str):
    return _command(
        "agent.session.input",
        payload={"text": text},
        target={"session_id": session_id},
    )


def build_permission_response(session_id: str, request_id: str, approved: bool):
    return {
        "type": "permission_response",
        "session_id": session_id,
        "request_id": request_id,
        "approved": bool(approved),
        "timestamp": now_ts(),
    }


def build_interrupt_command(session_id: str):
    return _command("agent.run.interrupt", target={"session_id": session_id})


def build_close_command(session_id: str):
    return _command("agent.session.close", target={"session_id": session_id})


async def _send_json(ws, payload: Dict[str, Any]) -> None:
    await ws.send(json.dumps(payload, ensure_ascii=False))


def _load_json(raw: str) -> Dict[str, Any]:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Local API message was not an object")
    return payload


def _event_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("type") == "event":
        event = payload.get("event")
        if isinstance(event, dict):
            return event
    return payload


def _extract_session_id(payload: Dict[str, Any]) -> Optional[str]:
    event = _event_payload(payload)
    for container in (event.get("payload"), event):
        if isinstance(container, dict):
            session_id = container.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
    return None


def _extract_permission(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    event = _event_payload(payload)
    event_type = event.get("type")
    if event_type != "permission_request":
        return None
    session_id = event.get("session_id")
    request_id = event.get("request_id")
    if isinstance(session_id, str) and isinstance(request_id, str):
        return {"session_id": session_id, "request_id": request_id}
    return None


def _print_relevant(payload: Dict[str, Any]) -> None:
    event = _event_payload(payload)
    event_type = event.get("type")
    if event_type in PRINT_EVENT_TYPES:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    elif payload.get("type") == "error":
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def _start_stdin_reader(queue: asyncio.Queue, stdin=None) -> threading.Thread:
    loop = asyncio.get_event_loop()
    input_stream = stdin or sys.stdin

    def read_stdin() -> None:
        while True:
            line = input_stream.readline()
            if line == "":
                loop.call_soon_threadsafe(queue.put_nowait, "/exit")
                return
            loop.call_soon_threadsafe(queue.put_nowait, line.rstrip("\r\n"))

    thread = threading.Thread(target=read_stdin)
    thread.daemon = True
    thread.start()
    return thread


async def _receiver(ws, state: Dict[str, Any], stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        raw = await ws.recv()
        payload = _load_json(raw)
        session_id = _extract_session_id(payload)
        if session_id:
            state["session_id"] = session_id
        permission = _extract_permission(payload)
        if permission:
            state["pending_permission"] = permission
        _print_relevant(payload)


async def _sender(ws, state: Dict[str, Any], lines: asyncio.Queue, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        line = await lines.get()
        if not line:
            continue
        session_id = state.get("session_id")
        if line == "/exit":
            if session_id:
                await _send_json(ws, build_close_command(session_id))
            stop_event.set()
            return
        if line == "/approve" or line == "/deny":
            pending = state.get("pending_permission")
            if not pending:
                print("No pending permission.", flush=True)
                continue
            await _send_json(
                ws,
                build_permission_response(
                    pending["session_id"],
                    pending["request_id"],
                    line == "/approve",
                ),
            )
            state["pending_permission"] = None
            continue
        if not session_id:
            print("No active session yet.", flush=True)
            continue
        if line == "/interrupt":
            await _send_json(ws, build_interrupt_command(session_id))
        elif line == "/close":
            await _send_json(ws, build_close_command(session_id))
        else:
            await _send_json(ws, build_input_command(session_id, line))


async def run_cli(args) -> int:
    try:
        import websockets
    except ImportError:
        print("websockets is required to connect to the Local API.", file=sys.stderr)
        return 2

    state = {"session_id": None, "pending_permission": None}
    lines = asyncio.Queue()
    stop_event = asyncio.Event()
    async with websockets.connect(args.api_url) as ws:
        await _send_json(ws, build_hello_message(args.client_kind, args.token, args.client_id))
        await _send_json(ws, build_launch_command(args.agent, args.workspace, args.context))
        _start_stdin_reader(lines)
        receiver_task = asyncio.create_task(_receiver(ws, state, stop_event))
        sender_task = asyncio.create_task(_sender(ws, state, lines, stop_event))
        done, pending = await asyncio.wait(
            [receiver_task, sender_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        stop_event.set()
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                raise exc
    return 0


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return asyncio.run(run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
