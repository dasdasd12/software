"""Global hotkey test harness for the Local API virtual input path."""

import argparse
import asyncio
from contextlib import suppress
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from keyboard.layouts import HOTKEY_HARNESS_LAYOUT_ID


DEFAULT_URL = "ws://127.0.0.1:8765"
DEFAULT_CLIENT_ID = "test-harness"
DEFAULT_DEVICE_ID = "kbd_hotkey_harness"
DEFAULT_CAPABILITIES = ("agent:launch", "permission:respond", "session:list")
DEFAULT_CODEX_CONTEXT = (
    "Harmless approval test: if a permission prompt appears, wait for the local hotkey harness response."
)
DEFAULT_CLAUDE_CONTEXT = (
    "Harmless approval test: if a permission prompt appears, wait for the local hotkey harness response."
)
PROFILE_ID = "profile_local_hotkey_harness"

HOTKEY_TO_KEY_ID: Dict[str, str] = {
    "ctrl+alt+shift+1": "K_CODEX_LAUNCH",
    "ctrl+alt+shift+2": "K_CLAUDE_LAUNCH",
    "ctrl+alt+shift+enter": "K_APPROVE",
    "ctrl+alt+shift+backspace": "K_DENY",
    "ctrl+alt+shift+esc": "K_INTERRUPT",
    "ctrl+alt+shift+q": "K_CLOSE",
    "ctrl+alt+shift+tab": "K_FOCUS_NEXT",
    "ctrl+alt+shift+t": "K_TOOL_NEXT",
}

PYNPUT_HOTKEYS: Dict[str, str] = {
    "<ctrl>+<alt>+<shift>+1": "K_CODEX_LAUNCH",
    "<ctrl>+<alt>+<shift>+2": "K_CLAUDE_LAUNCH",
    "<ctrl>+<alt>+<shift>+<enter>": "K_APPROVE",
    "<ctrl>+<alt>+<shift>+<backspace>": "K_DENY",
    "<ctrl>+<alt>+<shift>+<esc>": "K_INTERRUPT",
    "<ctrl>+<alt>+<shift>+q": "K_CLOSE",
    "<ctrl>+<alt>+<shift>+<tab>": "K_FOCUS_NEXT",
    "<ctrl>+<alt>+<shift>+t": "K_TOOL_NEXT",
}


class PynputDependencyError(RuntimeError):
    """Raised when the optional pynput listener is requested but unavailable."""


@dataclass(frozen=True)
class HotkeyHarnessConfig:
    url: str = DEFAULT_URL
    token: str = ""
    client_id: str = DEFAULT_CLIENT_ID
    device_id: str = DEFAULT_DEVICE_ID
    workspace: Optional[str] = None
    codex_context: str = DEFAULT_CODEX_CONTEXT
    claude_context: str = DEFAULT_CLAUDE_CONTEXT
    json_log: bool = False


def now_ts() -> int:
    return int(time.time())


def normalize_hotkey(value: str) -> str:
    parts = [part.strip().lower() for part in value.replace("<", "").replace(">", "").split("+")]
    return "+".join(part for part in parts if part)


def build_hello_message(config: HotkeyHarnessConfig) -> Dict[str, Any]:
    return {
        "type": "hello",
        "token": config.token or None,
        "client_kind": "desktop-ui",
        "client_id": config.client_id,
        "capabilities": list(DEFAULT_CAPABILITIES),
        "timestamp": now_ts(),
    }


def build_virtual_profile(
    *,
    codex_context: str = DEFAULT_CODEX_CONTEXT,
    claude_context: str = DEFAULT_CLAUDE_CONTEXT,
    workspace: Optional[str] = None,
) -> Dict[str, Any]:
    codex_payload = _launch_action("codex", codex_context, workspace)
    claude_payload = _launch_action("claude", claude_context, workspace)
    return {
        "schema_version": "1.0",
        "id": PROFILE_ID,
        "name": "Local Hotkey Harness",
        "target_device_family": "simulated",
        "keymap": {
            "physical_layout_id": HOTKEY_HARNESS_LAYOUT_ID,
            "bindings": {
                "K_CODEX_LAUNCH": codex_payload,
                "K_CLAUDE_LAUNCH": claude_payload,
                "K_APPROVE": {
                    "type": "agent.permission.respond",
                    "target": "focused_permission",
                    "approved": True,
                },
                "K_DENY": {
                    "type": "agent.permission.respond",
                    "target": "focused_permission",
                    "approved": False,
                },
                "K_INTERRUPT": {
                    "type": "agent.run.interrupt",
                    "target": "focused_run",
                },
                "K_CLOSE": {
                    "type": "agent.session.close",
                    "target": "focused_session",
                },
                "K_FOCUS_NEXT": {
                    "type": "agent.focus.next_session",
                },
                "K_TOOL_NEXT": {
                    "type": "keyboard.tool.next",
                },
            }
        },
    }


def _launch_action(agent: str, context: str, workspace: Optional[str]) -> Dict[str, Any]:
    payload = {
        "type": "agent.cli.launch_foreground",
        "target": "focused_agent",
        "agent": agent,
    }
    if workspace:
        payload["workspace"] = workspace
    return payload


class FakeHotkeyEventSource:
    """Deterministic event source for tests and dry-run CLI paths."""

    def __init__(
        self,
        on_key: Callable[[str], Any],
        mapping: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.on_key = on_key
        self.mapping = {normalize_hotkey(key): value for key, value in (mapping or HOTKEY_TO_KEY_ID).items()}

    def key_id_for(self, hotkey: str) -> str:
        try:
            return self.mapping[normalize_hotkey(hotkey)]
        except KeyError as exc:
            raise ValueError(f"unmapped hotkey: {hotkey}") from exc

    def emit(self, hotkey: str) -> Any:
        return self.on_key(self.key_id_for(hotkey))


class PynputHotkeyEventSource:
    """Real global hotkey listener. Imports pynput only when started."""

    def __init__(self, on_key: Callable[[str], Any]) -> None:
        self.on_key = on_key
        self._listener = None

    def start(self) -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise PynputDependencyError(
                "pynput is required for global hotkeys; install it with `pip install pynput`."
            ) from exc

        hotkeys = {
            hotkey: self._callback_for(key_id)
            for hotkey, key_id in PYNPUT_HOTKEYS.items()
        }
        self._listener = keyboard.GlobalHotKeys(hotkeys)
        self._listener.start()

    def join(self) -> None:
        if self._listener is not None:
            self._listener.join()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()

    def _callback_for(self, key_id: str) -> Callable[[], Any]:
        def callback() -> Any:
            return self.on_key(key_id)

        return callback


class QueuedHotkeySender:
    """Serializes global hotkey callbacks onto one websocket send/recv loop."""

    def __init__(self, harness: "HotkeyHarness", ws: Any, *, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self.harness = harness
        self.ws = ws
        self._loop = loop
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def start(self) -> "QueuedHotkeySender":
        if self._task is None:
            self._loop = self._loop or asyncio.get_running_loop()
            self._task = self._loop.create_task(self._run())
        return self

    def enqueue(self, key_id: str) -> None:
        if self._loop is None:
            raise RuntimeError("queued hotkey sender must be started before enqueue")
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._queue.put_nowait(key_id)
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, key_id)

    async def drain(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            key_id = await self._queue.get()
            try:
                await self.harness.send_key_id(self.ws, key_id)
            except Exception as exc:
                self.harness.report_worker_exception(key_id, exc)
            finally:
                self._queue.task_done()


class HotkeyHarness:
    def __init__(self, config: HotkeyHarnessConfig) -> None:
        self.config = config
        self._sequence = 0

    def message_for_hotkey(self, hotkey: str) -> Dict[str, Any]:
        key_id = HOTKEY_TO_KEY_ID[normalize_hotkey(hotkey)]
        return self.virtual_input_message(key_id)

    def virtual_input_message(self, key_id: str) -> Dict[str, Any]:
        return {
            "type": "virtual_input",
            "device_id": self.config.device_id,
            "key_id": key_id,
            "event_type": "press",
        }

    async def run_once(self, ws: Any, hotkey: str) -> Dict[str, Any]:
        await self.configure(ws)
        return await self.send_hotkey(ws, hotkey)

    async def configure(self, ws: Any) -> None:
        await self._send(ws, build_hello_message(self.config))
        await self._wait_for_type(ws, "hello_ack")
        await self._send(ws, {
            "type": "virtual_device_configure",
            "device_id": self.config.device_id,
            "profile": build_virtual_profile(
                codex_context=self.config.codex_context,
                claude_context=self.config.claude_context,
                workspace=self.config.workspace,
            ),
            "timestamp": now_ts(),
        })
        await self._wait_for_type(ws, "virtual_device_configured")

    async def send_hotkey(self, ws: Any, hotkey: str) -> Dict[str, Any]:
        payload = self.message_for_hotkey(hotkey)
        payload["timestamp"] = now_ts()
        payload["sequence"] = self._next_sequence()
        await self._send(ws, payload)
        ack = await self._wait_for_type(ws, "virtual_input_ack")
        session_id = self._created_session_id(ack)
        if session_id:
            await self.focus_session(ws, session_id)
        return ack

    async def send_key_id(self, ws: Any, key_id: str) -> Dict[str, Any]:
        payload = self.virtual_input_message(key_id)
        payload["timestamp"] = now_ts()
        payload["sequence"] = self._next_sequence()
        await self._send(ws, payload)
        ack = await self._wait_for_type(ws, "virtual_input_ack")
        session_id = self._created_session_id(ack)
        if session_id:
            await self.focus_session(ws, session_id)
        return ack

    async def focus_session(self, ws: Any, session_id: str) -> None:
        await self._send(ws, {
            "type": "command",
            "command": {
                "command_id": f"cmd_hotkey_focus_{self._next_sequence()}",
                "type": "agent.focus.set",
                "source": {"kind": "desktop-ui", "client_id": self.config.client_id},
                "target": {"device_id": self.config.device_id},
                "payload": {"session_id": session_id},
            },
            "timestamp": now_ts(),
        })

    async def connect_and_listen(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("websockets is required to connect to the Local API.") from exc

        async with websockets.connect(self.config.url) as ws:
            await self.configure(ws)
            sender = QueuedHotkeySender(self, ws).start()

            def on_key(key_id: str) -> None:
                sender.enqueue(key_id)

            source = PynputHotkeyEventSource(on_key)
            source.start()
            try:
                await asyncio.Future()
            finally:
                source.stop()
                await sender.stop()

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    async def _send(self, ws: Any, payload: Dict[str, Any]) -> None:
        self._log("SEND", payload)
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def _recv(self, ws: Any) -> Dict[str, Any]:
        payload = json.loads(await ws.recv())
        self._log("RECV", payload)
        return payload

    async def _wait_for_type(self, ws: Any, expected_type: str) -> Dict[str, Any]:
        while True:
            payload = await self._recv(ws)
            if payload.get("type") == "error":
                raise RuntimeError(f"Local API error while waiting for {expected_type}: {payload}")
            if payload.get("type") == expected_type:
                return payload

    def _log(self, direction: str, payload: Dict[str, Any]) -> None:
        if self.config.json_log:
            print(json.dumps({
                "direction": direction,
                "payload": payload,
                "timestamp": now_ts(),
            }, ensure_ascii=False))

    def report_worker_exception(self, key_id: str, exc: Exception) -> None:
        message = f"hotkey send failed for {key_id}: {exc}"
        if self.config.json_log:
            print(json.dumps({
                "direction": "ERROR",
                "payload": {
                    "type": "hotkey_send_error",
                    "key_id": key_id,
                    "message": str(exc),
                },
                "timestamp": now_ts(),
            }, ensure_ascii=False), file=sys.stderr)
            return
        print(message, file=sys.stderr)

    @staticmethod
    def _created_session_id(payload: Mapping[str, Any]) -> Optional[str]:
        for event in payload.get("events") or []:
            if event.get("type") != "agent.session.created":
                continue
            event_payload = event.get("payload") or {}
            session_id = event_payload.get("session_id")
            if isinstance(session_id, str) and session_id:
                return session_id
        return None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local API global hotkey test harness.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default="")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--codex-context", default=DEFAULT_CODEX_CONTEXT)
    parser.add_argument("--claude-context", default=DEFAULT_CLAUDE_CONTEXT)
    parser.add_argument("--json-log", action="store_true")
    parser.add_argument("--dry-run-test-event", choices=sorted(HOTKEY_TO_KEY_ID.keys()))
    return parser


async def async_main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    config = HotkeyHarnessConfig(
        url=args.url,
        token=args.token,
        client_id=args.client_id,
        device_id=args.device_id,
        workspace=args.workspace,
        codex_context=args.codex_context,
        claude_context=args.claude_context,
        json_log=args.json_log,
    )
    harness = HotkeyHarness(config)
    if args.dry_run_test_event:
        print(json.dumps(harness.message_for_hotkey(args.dry_run_test_event), ensure_ascii=False))
        return 0
    try:
        await harness.connect_and_listen()
    except PynputDependencyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
