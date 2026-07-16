#!/usr/bin/env python
"""Claude Code hook bridge for Local API native foreground sessions."""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict


DEFAULT_API_URL = "ws://127.0.0.1:8765"
CLAUDE_HOOK_TOKEN_ENV = "AI_KEYB_CLAUDE_HOOK_TOKEN"


def now_ts() -> float:
    return time.time()


def _unicode_safe(value: str) -> str:
    """Replace lone surrogate code points before JSON/WebSocket encoding."""
    return value.encode("utf-8", errors="replace").decode("utf-8")


def _sanitize_json_value(value: Any) -> Any:
    if isinstance(value, str):
        return _unicode_safe(value)
    if isinstance(value, dict):
        return {
            _unicode_safe(key) if isinstance(key, str) else key: _sanitize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_json_value(item) for item in value]
    return value


def _read_stdin_utf8() -> str:
    """Claude Code writes hook JSON to stdin as UTF-8, including on Windows."""
    binary_stream = getattr(sys.stdin, "buffer", None)
    if binary_stream is not None:
        raw = binary_stream.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8-sig", errors="replace")
    raw = sys.stdin.read()
    if isinstance(raw, bytes):
        return raw.decode("utf-8-sig", errors="replace")
    return _unicode_safe(raw)


def _json_for_wire(payload: Any) -> str:
    # ASCII-only JSON keeps Windows console code pages out of the WebSocket path.
    return json.dumps(_sanitize_json_value(payload), ensure_ascii=True)


def _write_json_stdout(payload: Dict[str, Any]) -> None:
    encoded = _json_for_wire(payload)
    binary_stream = getattr(sys.stdout, "buffer", None)
    if binary_stream is not None:
        binary_stream.write(encoded.encode("ascii") + b"\n")
        binary_stream.flush()
        return
    print(encoded, flush=True)


def _report_nonblocking_error(message: str) -> None:
    safe_message = _unicode_safe(message)
    print(f"AI keyboard hook bridge unavailable; using Claude's native prompt: {safe_message}", file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Forward Claude Code hook events to the Local API.")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--client-kind", default="agent-hook")
    parser.add_argument("--client-id", default="claude-code-hook")
    parser.add_argument("--timeout", type=float, default=600.0)
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def build_hello(args, token: str) -> Dict[str, Any]:
    return {
        "type": "hello",
        "token": token or None,
        "client_kind": args.client_kind,
        "client_id": args.client_id,
        "capabilities": ["claude:hook"],
        "timestamp": now_ts(),
    }


def build_hook_event(args, hook_input: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "claude_hook_event",
        "session_id": args.session_id,
        "hook": _sanitize_json_value(hook_input),
        "timestamp": now_ts(),
    }


def build_hook_delivered(
    args,
    request_id: str,
    hook_event_name: str,
    response_written: bool,
    error: str = "",
) -> Dict[str, Any]:
    payload = {
        "type": "claude_hook_delivered",
        "session_id": args.session_id,
        "request_id": request_id,
        "hook_event_name": hook_event_name,
        "response_written": bool(response_written),
        "timestamp": now_ts(),
    }
    if error:
        payload["error"] = error
    return payload


def _load_hook_input(raw: str) -> Dict[str, Any]:
    payload = json.loads(_unicode_safe(raw) or "{}")
    if not isinstance(payload, dict):
        raise ValueError("hook input must be a JSON object")
    return _sanitize_json_value(payload)


async def run_hook(args, hook_input: Dict[str, Any]) -> Dict[str, Any]:
    import websockets

    token = os.environ.get(CLAUDE_HOOK_TOKEN_ENV, "")
    async with websockets.connect(args.api_url) as ws:
        await ws.send(_json_for_wire(build_hello(args, token)))
        while True:
            hello_ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=args.timeout))
            if hello_ack.get("type") == "hello_ack":
                break
            if hello_ack.get("type") == "error":
                raise RuntimeError(hello_ack.get("message") or hello_ack.get("code") or "hello failed")

        await ws.send(_json_for_wire(build_hook_event(args, hook_input)))
        while True:
            payload = json.loads(await asyncio.wait_for(ws.recv(), timeout=args.timeout))
            if payload.get("type") == "claude_hook_result":
                response = payload.get("hook_response")
                return {
                    "hook_response": response if isinstance(response, dict) else {},
                    "request_id": payload.get("request_id"),
                    "hook_event_name": payload.get("hook_event_name"),
                }
            if payload.get("type") == "error":
                raise RuntimeError(payload.get("message") or payload.get("code") or "hook failed")


async def mark_hook_delivered(
    args,
    request_id: str,
    hook_event_name: str,
    response_written: bool,
    error: str = "",
) -> None:
    import websockets

    token = os.environ.get(CLAUDE_HOOK_TOKEN_ENV, "")
    async with websockets.connect(args.api_url) as ws:
        await ws.send(_json_for_wire(build_hello(args, token)))
        while True:
            hello_ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=args.timeout))
            if hello_ack.get("type") == "hello_ack":
                break
            if hello_ack.get("type") == "error":
                raise RuntimeError(hello_ack.get("message") or hello_ack.get("code") or "hello failed")
        await ws.send(_json_for_wire(
            build_hook_delivered(args, request_id, hook_event_name, response_written, error)
        ))


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        raw_input = _read_stdin_utf8()
        hook_input = _load_hook_input(raw_input)
    except Exception as exc:
        _report_nonblocking_error(f"Invalid Claude hook input: {exc}")
        return 1

    try:
        result = asyncio.run(run_hook(args, hook_input))
    except Exception as exc:
        # Claude treats non-2 hook failures as non-blocking. Returning no
        # decision here preserves the native approval prompt instead of
        # fabricating a user denial.
        _report_nonblocking_error(str(exc))
        return 1
    if isinstance(result, dict) and (
        "hook_response" in result or "request_id" in result or "hook_event_name" in result
    ):
        response = result.get("hook_response") if isinstance(result.get("hook_response"), dict) else {}
        request_id = result.get("request_id")
        hook_event_name = result.get("hook_event_name")
    else:
        response = result if isinstance(result, dict) else {}
        request_id = None
        hook_event_name = None

    if response:
        _write_json_stdout(response)
    if isinstance(request_id, str) and request_id and isinstance(hook_event_name, str) and hook_event_name:
        try:
            asyncio.run(mark_hook_delivered(
                args,
                request_id,
                hook_event_name,
                response_written=bool(response),
            ))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
