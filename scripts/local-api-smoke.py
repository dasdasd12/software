#!/usr/bin/env python3
"""
Smoke client for the Local Core Service MVP WebSocket API.

This is a local UI/test/automation API client. It is not the firmware device
transport and should not be used as the keyboard protocol contract.

Examples:
    python scripts/local-api-smoke.py --scenario basic
    python scripts/local-api-smoke.py --scenario real-agent --agent codex --context "say hello"
    python scripts/local-api-smoke.py --scenario approval-real --agent claude --context "use a tool"
    python scripts/local-api-smoke.py --scenario permission --request-id req_1 --approved true
"""

import argparse
import asyncio
import json
import time
from typing import Any, Dict, List

import websockets


TERMINAL_TYPES = {"task_completed", "task_failed", "error"}
DEFAULT_CONTEXT = "say hello"
DEFAULT_CLAUDE_APPROVAL_CONTEXT = "Use WebFetch to fetch https://example.com and report the title."
DEFAULT_CODEX_APPROVAL_CONTEXT = (
    "Run this exact harmless command and report its output: "
    "python -c \"print('codex approval smoke')\""
)


def now_ts() -> int:
    return int(time.time())


def parse_bool(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "y", "approve", "approved"}


class LocalApiSmokeClient:
    def __init__(
        self,
        url: str,
        timeout: float,
        json_log: bool,
        token: str,
        client_kind: str,
        client_id: str,
        capabilities: List[str],
    ):
        self.url = url
        self.timeout = timeout
        self.json_log = json_log
        self.token = token
        self.client_kind = client_kind
        self.client_id = client_id
        self.capabilities = capabilities

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
            if payload.get("type") == "error":
                raise RuntimeError(f"Local API error while waiting for {expected_type}: {payload}")
            if payload.get("type") == expected_type:
                return payload

    async def wait_for_type_for_session(
        self,
        ws,
        expected_type: str,
        session_id: str,
    ) -> Dict[str, Any]:
        while True:
            payload = await self.recv_json(ws)
            if payload.get("type") == "error":
                raise RuntimeError(f"Local API error while waiting for {expected_type}: {payload}")
            if payload.get("session_id") == session_id and payload.get("type") in {"task_failed", "error"}:
                raise RuntimeError(f"Session failed while waiting for {expected_type}: {payload}")
            if payload.get("type") == expected_type and payload.get("session_id") == session_id:
                return payload

    async def hello(self, ws) -> None:
        await self.send(ws, {
            "type": "hello",
            "token": self.token or None,
            "client_kind": self.client_kind,
            "client_id": self.client_id,
            "capabilities": self.capabilities,
            "timestamp": now_ts(),
        })
        await self.wait_for_type(ws, "hello_ack")

    async def run_basic(self) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, {"type": "list_sessions", "agent": "all", "timestamp": now_ts()})
            await self.wait_for_type(ws, "session_list")
            await self.send(ws, {"type": "heartbeat", "timestamp": now_ts()})

    async def run_permission(self, request_id: str, approved: bool) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
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
            await self.hello(ws)
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

    async def run_approval_real(
        self,
        agent: str,
        context: str,
        approved: bool,
        require_forwarded: bool,
    ) -> None:
        async with websockets.connect(self.url) as ws:
            await self.hello(ws)
            await self.send(ws, {
                "type": "agent_launch",
                "agent": agent,
                "session_id": "new",
                "context": context,
                "timestamp": now_ts(),
            })

            launch_ack = await self.wait_for_type(ws, "task_update")
            session_id = launch_ack.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError(f"Launch ack did not include session_id: {launch_ack}")

            permission_request = await self.wait_for_type_for_session(ws, "permission_request", session_id)
            request_id = permission_request["request_id"]
            await self.send(ws, {
                "type": "permission_response",
                "request_id": request_id,
                "session_id": session_id,
                "approved": approved,
                "timestamp": now_ts(),
            })

            ack = await self.wait_for_type_for_session(ws, "permission_ack", session_id)
            if ack.get("request_id") != request_id or ack.get("session_id") != session_id:
                raise RuntimeError(f"Permission ack did not match request/session: {ack}")
            if require_forwarded and not ack.get("forwarded"):
                raise RuntimeError(f"Permission was not forwarded: {ack}")
            evidence = ack.get("evidence") or {}
            if not evidence:
                raise RuntimeError(f"Permission ack did not include forwarding evidence: {ack}")
            if agent == "claude":
                if evidence.get("adapter") != "claude_agent_sdk" or not evidence.get("callback_returned"):
                    raise RuntimeError(f"Permission ack did not include SDK callback evidence: {ack}")
            elif agent == "codex":
                if (
                    evidence.get("adapter") != "codex_app_server"
                    or not evidence.get("response_written")
                    or not evidence.get("decision_delivered")
                ):
                    raise RuntimeError(f"Permission ack did not include Codex app-server evidence: {ack}")

            while True:
                payload = await self.recv_json(ws)
                payload_type = payload.get("type")
                if payload.get("session_id") != session_id:
                    continue
                if payload_type == "task_completed":
                    return
                if payload_type in {"task_failed", "error"}:
                    raise RuntimeError(f"Approval scenario ended with failure: {payload}")


async def amain() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the Local Core Service MVP WebSocket API")
    parser.add_argument("--url", default="ws://127.0.0.1:8765", help="Local Core Service WebSocket URL")
    parser.add_argument("--scenario", choices=("basic", "permission", "real-agent", "approval-real"), default="basic")
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--context", default=DEFAULT_CONTEXT, help="Prompt used by the real-agent smoke scenario")
    parser.add_argument("--timeout", type=float, default=10.0, help="Receive timeout in seconds")
    parser.add_argument("--json-log", action="store_true", help="Print each send/receive as JSON")
    parser.add_argument("--request-id", default="req_1", help="Permission request id to approve or reject")
    parser.add_argument("--approved", default="true", help="Permission decision for the permission scenario")
    parser.add_argument("--decision", choices=("approve", "deny"), help="Permission decision alias for approval scenarios")
    parser.add_argument("--token", default="", help="Launch token for auth-enabled Local API")
    parser.add_argument("--client-kind", default="desktop-ui", help="Local API client kind for hello")
    parser.add_argument("--client-id", default="local-api-smoke", help="Local API client id for hello")
    parser.add_argument(
        "--capability",
        action="append",
        dest="capabilities",
        default=None,
        help="Capability to advertise in hello; repeatable",
    )
    parser.add_argument(
        "--require-forwarded",
        action="store_true",
        help="Fail approval-real unless permission_ack.forwarded is true",
    )
    args = parser.parse_args()

    capabilities = args.capabilities or ["agent:launch", "permission:respond", "session:list"]
    client = LocalApiSmokeClient(
        args.url,
        args.timeout,
        args.json_log,
        args.token,
        args.client_kind,
        args.client_id,
        capabilities,
    )
    context = args.context
    if args.scenario == "approval-real" and context == DEFAULT_CONTEXT:
        context = (
            DEFAULT_CODEX_APPROVAL_CONTEXT
            if args.agent == "codex"
            else DEFAULT_CLAUDE_APPROVAL_CONTEXT
        )

    if args.scenario == "basic":
        await client.run_basic()
    elif args.scenario == "permission":
        approved = args.decision == "approve" if args.decision else parse_bool(args.approved)
        await client.run_permission(args.request_id, approved)
    elif args.scenario == "approval-real":
        approved = args.decision == "approve" if args.decision else parse_bool(args.approved)
        await client.run_approval_real(
            args.agent,
            context,
            approved,
            args.require_forwarded,
        )
    else:
        await client.run_real_agent(args.agent, context)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
