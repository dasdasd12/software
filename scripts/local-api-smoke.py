#!/usr/bin/env python3
"""
Smoke client for the Local Core Service MVP WebSocket API.

This is a local UI/test/automation API client. It is not the firmware device
transport and should not be used as the keyboard protocol contract.

Examples:
    python scripts/local-api-smoke.py --scenario basic
    python scripts/local-api-smoke.py --scenario real-agent --agent codex --context "say hello"
    python scripts/local-api-smoke.py --scenario permission --request-id req_1 --approved true
"""

import argparse
import asyncio
import json
import time
from typing import Any, Dict

import websockets


TERMINAL_TYPES = {"task_completed", "task_failed", "error"}


def now_ts() -> int:
    return int(time.time())


def parse_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "y", "approve", "approved"}


class LocalApiSmokeClient:
    def __init__(self, url: str, timeout: float, json_log: bool):
        self.url = url
        self.timeout = timeout
        self.json_log = json_log

    def log(self, direction: str, payload: Dict[str, Any]) -> None:
        record = {
            "direction": direction,
            "payload": payload,
            "timestamp": now_ts(),
        }
        if self.json_log:
            print(json.dumps(record, ensure_ascii=False))
            return

        msg_type = payload.get("type", "unknown")
        print(f"{direction} {msg_type}: {json.dumps(payload, ensure_ascii=False)}")

    async def send(self, ws, payload: Dict[str, Any]) -> None:
        self.log("SEND", payload)
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def recv_json(self, ws) -> Dict[str, Any]:
        raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
        payload = json.loads(raw)
        self.log("RECV", payload)
        return payload

    async def wait_for_type(self, ws, expected_type: str) -> Dict[str, Any]:
        while True:
            payload = await self.recv_json(ws)
            if payload.get("type") == expected_type:
                return payload

    async def run_basic(self) -> None:
        async with websockets.connect(self.url) as ws:
            await self.send(ws, {"type": "list_sessions", "agent": "all", "timestamp": now_ts()})
            await self.wait_for_type(ws, "session_list")
            await self.send(ws, {"type": "heartbeat", "timestamp": now_ts()})

    async def run_permission(self, request_id: str, approved: bool) -> None:
        async with websockets.connect(self.url) as ws:
            await self.send(ws, {
                "type": "permission_response",
                "request_id": request_id,
                "approved": approved,
                "timestamp": now_ts(),
            })
            payload = await self.recv_json(ws)
            if payload.get("type") not in {"permission_ack", "error"}:
                raise RuntimeError(f"Unexpected permission response: {payload}")

    async def run_real_agent(self, agent: str, context: str) -> None:
        async with websockets.connect(self.url) as ws:
            await self.send(ws, {
                "type": "agent_launch",
                "agent": agent,
                "session_id": "new",
                "context": context,
                "timestamp": now_ts(),
            })
            while True:
                payload = await self.recv_json(ws)
                if payload.get("type") in TERMINAL_TYPES:
                    return


async def amain() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the Local Core Service MVP WebSocket API")
    parser.add_argument("--url", default="ws://127.0.0.1:8765", help="Local Core Service WebSocket URL")
    parser.add_argument("--scenario", choices=("basic", "permission", "real-agent"), default="basic")
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--context", default="say hello", help="Prompt used by the real-agent smoke scenario")
    parser.add_argument("--timeout", type=float, default=10.0, help="Receive timeout in seconds")
    parser.add_argument("--json-log", action="store_true", help="Print each send/receive as JSON")
    parser.add_argument("--request-id", default="req_1", help="Permission request id to approve or reject")
    parser.add_argument("--approved", default="true", help="Permission decision for the permission scenario")
    args = parser.parse_args()

    client = LocalApiSmokeClient(args.url, args.timeout, args.json_log)
    if args.scenario == "basic":
        await client.run_basic()
    elif args.scenario == "permission":
        await client.run_permission(args.request_id, parse_bool(args.approved))
    else:
        await client.run_real_agent(args.agent, args.context)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
