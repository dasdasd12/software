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
        "hook": hook_input,
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


def permission_denied_response(message: str) -> Dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "deny",
                "message": message,
                "interrupt": True,
            },
        }
    }


def pretooluse_denied_response(message: str) -> Dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": message,
        }
    }


def is_controlled_pretooluse_input(hook_input: Dict[str, Any]) -> bool:
    return (
        hook_input.get("hook_event_name") == "PreToolUse"
        and hook_input.get("tool_name") in {"AskUserQuestion", "ExitPlanMode"}
    )


def _parse_failure_response(raw: str, message: str) -> Dict[str, Any]:
    if any(marker in raw for marker in ("PreToolUse", "AskUserQuestion", "ExitPlanMode")):
        return pretooluse_denied_response(message)
    return permission_denied_response(message)


def _load_hook_input(raw: str) -> Dict[str, Any]:
    payload = json.loads(raw or "{}")
    if not isinstance(payload, dict):
        raise ValueError("hook input must be a JSON object")
    return payload


async def run_hook(args, hook_input: Dict[str, Any]) -> Dict[str, Any]:
    import websockets

    token = os.environ.get(CLAUDE_HOOK_TOKEN_ENV, "")
    async with websockets.connect(args.api_url) as ws:
        await ws.send(json.dumps(build_hello(args, token), ensure_ascii=False))
        while True:
            hello_ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=args.timeout))
            if hello_ack.get("type") == "hello_ack":
                break
            if hello_ack.get("type") == "error":
                raise RuntimeError(hello_ack.get("message") or hello_ack.get("code") or "hello failed")

        await ws.send(json.dumps(build_hook_event(args, hook_input), ensure_ascii=False))
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
        await ws.send(json.dumps(build_hello(args, token), ensure_ascii=False))
        while True:
            hello_ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=args.timeout))
            if hello_ack.get("type") == "hello_ack":
                break
            if hello_ack.get("type") == "error":
                raise RuntimeError(hello_ack.get("message") or hello_ack.get("code") or "hello failed")
        await ws.send(json.dumps(
            build_hook_delivered(args, request_id, hook_event_name, response_written, error),
            ensure_ascii=False,
        ))


def main(argv=None) -> int:
    args = parse_args(argv)
    raw_input = sys.stdin.read()
    try:
        hook_input = _load_hook_input(raw_input)
    except Exception as exc:
        print(json.dumps(_parse_failure_response(raw_input, f"Invalid Claude hook input: {exc}")), flush=True)
        return 0

    try:
        result = asyncio.run(run_hook(args, hook_input))
    except Exception as exc:
        if hook_input.get("hook_event_name") == "PermissionRequest":
            print(json.dumps(permission_denied_response(f"Local API hook bridge failed closed: {exc}")), flush=True)
        elif is_controlled_pretooluse_input(hook_input):
            print(json.dumps(pretooluse_denied_response(f"Local API hook bridge failed closed: {exc}")), flush=True)
        return 0
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

    if not response and is_controlled_pretooluse_input(hook_input):
        response = pretooluse_denied_response("Local API hook bridge returned no PreToolUse decision.")
    if response:
        print(json.dumps(response, ensure_ascii=False), flush=True)
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
