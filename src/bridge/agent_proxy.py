"""
Agent Proxy — Bridge Server

Manages Codex and Claude CLI subprocesses, streams their stdout/stderr,
and forwards parsed events through the ProtocolUnifier.
"""

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (
    ClaudeAgentSdkPermissionAdapter,
    ClaudeSdkPermissionBridge,
    UnsupportedPermissionAdapter,
)
from protocol_unifier import ProtocolUnifier
from session_manager import AgentType, AgentState, Session, SessionManager


class AgentProxy:
    """Base class for AI agent subprocess management."""

    def __init__(
        self,
        agent_type: AgentType,
        session_manager: SessionManager,
        unifier: ProtocolUnifier,
        executable: Optional[str] = None,
        mode: str = "",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        api_key: Optional[str] = None,
        session_timeout_sec: int = 3600,
        permission_adapter: Optional[Any] = None,
    ):
        self.agent_type = agent_type
        self._sm = session_manager
        self._unifier = unifier
        self._executable = executable or self._find_executable()
        self._mode = mode
        self._args = args or []
        self._extra_env = dict(env or {})
        self._env = {**os.environ, **self._extra_env}
        if api_key:
            key_env = "ANTHROPIC_API_KEY" if agent_type == AgentType.CLAUDE else "CODEX_API_KEY"
            self._env[key_env] = api_key
            self._extra_env[key_env] = api_key
        self._session_timeout_sec = session_timeout_sec
        self._permission_adapter = permission_adapter or self._make_permission_adapter()

        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._read_tasks: Dict[str, asyncio.Task] = {}
        self._sdk_tasks: Dict[str, asyncio.Task] = {}
        self._sdk_input_done: Dict[str, asyncio.Event] = {}
        self._on_unified_event: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------ #
    #  Discovery
    # ------------------------------------------------------------------ #

    def _find_executable(self) -> Optional[str]:
        name = "claude" if self.agent_type == AgentType.CLAUDE else "codex"
        return shutil.which(name)

    def is_available(self) -> bool:
        if self._uses_claude_agent_sdk() and not self._claude_agent_sdk_importable():
            return False
        return self._executable is not None and os.path.isfile(self._executable)

    def _make_permission_adapter(self) -> Any:
        if self.agent_type == AgentType.CLAUDE:
            if self._uses_claude_agent_sdk():
                return ClaudeAgentSdkPermissionAdapter(self._emit_unified_event)
            return ClaudeSdkPermissionBridge(
                lambda agent, request_id, approved, native_request: self._unifier.encode_permission_decision(
                    agent=agent,
                    request_id=request_id,
                    approved=approved,
                    native_request=native_request,
                )
            )
        return UnsupportedPermissionAdapter()

    def _uses_claude_agent_sdk(self) -> bool:
        return self.agent_type == AgentType.CLAUDE and self._mode in {
            "agent_sdk",
            "python_sdk",
            "sdk",
        }

    @staticmethod
    def _claude_agent_sdk_importable() -> bool:
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            return False
        return True

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def launch(self, session_id: str, context: str = "") -> Optional[Session]:
        """Start a new session subprocess for this agent."""
        if not self.is_available():
            raise RuntimeError(f"{self.agent_type.value} executable not found")

        if self._uses_claude_agent_sdk():
            return await self._launch_claude_agent_sdk(session_id, context)

        # Build command line based on mode
        cmd = self._build_command(session_id, context)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=str(Path.home()),
            )
        except Exception as exc:
            self._sm.update_state(session_id, AgentState.FAILED)
            raise RuntimeError(f"Failed to start {self.agent_type.value}: {exc}")

        self._processes[session_id] = proc
        self._sm.set_process_pid(session_id, proc.pid)
        self._sm.update_state(session_id, AgentState.WORKING)

        # Start stdout / stderr readers
        self._read_tasks[session_id] = asyncio.create_task(
            self._read_stream(session_id, proc.stdout, proc.stderr)
        )

        return self._sm.get(session_id)

    async def resume(self, session_id: str) -> Optional[Session]:
        """Resume an existing session. For CLI-based agents, this typically means
        launching a new process with the previous context (if persisted).
        """
        sess = self._sm.get(session_id)
        if not sess:
            return None
        # For MVP: treat resume as launch with empty context
        # Future: load conversation history and inject as context
        return await self.launch(session_id, context="")

    async def terminate(self, session_id: str) -> bool:
        """Gracefully terminate a session subprocess."""
        sdk_task = self._sdk_tasks.pop(session_id, None)
        if sdk_task is not None:
            done_event = self._sdk_input_done.pop(session_id, None)
            if done_event:
                done_event.set()
            sdk_task.cancel()
            try:
                await sdk_task
            except asyncio.CancelledError:
                pass
            self._sm.update_state(session_id, AgentState.CANCELLED)
            return True

        proc = self._processes.pop(session_id, None)
        if proc is None:
            return False

        task = self._read_tasks.pop(session_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

        self._sm.update_state(session_id, AgentState.CANCELLED)
        return True

    async def send_interrupt(self, session_id: str) -> bool:
        """Send SIGINT to the subprocess (if supported by the OS)."""
        sdk_task = self._sdk_tasks.get(session_id)
        if sdk_task:
            done_event = self._sdk_input_done.get(session_id)
            if done_event:
                done_event.set()
            sdk_task.cancel()
            self._sm.update_state(session_id, AgentState.CANCELLED)
            return True

        proc = self._processes.get(session_id)
        if not proc:
            return False
        try:
            proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT if sys.platform == "win32" else subprocess.signal.SIGINT)
            return True
        except Exception:
            return False

    async def handle_permission_response(
        self,
        session_id: str,
        request_id: str,
        approved: bool,
    ) -> Dict[str, Any]:
        """Accept and optionally forward a device permission response."""
        return await self._permission_adapter.forward_permission_response(
            session_id,
            request_id,
            approved,
        )

    async def _launch_claude_agent_sdk(
        self,
        session_id: str,
        context: str,
    ) -> Optional[Session]:
        self._sm.update_state(session_id, AgentState.WORKING)
        done_event = asyncio.Event()
        self._sdk_input_done[session_id] = done_event
        self._sdk_tasks[session_id] = asyncio.create_task(
            self._run_claude_agent_sdk(session_id, context, done_event)
        )
        return self._sm.get(session_id)

    async def _run_claude_agent_sdk(
        self,
        session_id: str,
        context: str,
        done_event: asyncio.Event,
    ) -> None:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query

            async for message in query(
                prompt=self._claude_sdk_prompt_stream(context, done_event),
                options=ClaudeAgentOptions(
                    can_use_tool=self._claude_sdk_can_use_tool(session_id),
                    permission_mode="default",
                    cwd=Path.home(),
                    cli_path=self._executable,
                    env=self._extra_env,
                    max_turns=3,
                    stderr=lambda text: print(
                        f"[{session_id}] {self.agent_type.value} stderr: {text}"
                    ),
                ),
            ):
                for event in self._claude_sdk_message_to_events(session_id, message):
                    self._emit_unified_event(event)
                if message.__class__.__name__ == "ResultMessage":
                    done_event.set()

        except asyncio.CancelledError:
            done_event.set()
            raise
        except Exception as exc:
            done_event.set()
            self._emit_unified_event(
                self._unifier._mk_task_failed(
                    session_id,
                    AgentType.CLAUDE,
                    "SDK_ERROR",
                    str(exc),
                )
            )
        finally:
            done_event.set()
            self._sdk_tasks.pop(session_id, None)
            self._sdk_input_done.pop(session_id, None)

    async def _claude_sdk_prompt_stream(
        self,
        context: str,
        done_event: asyncio.Event,
    ) -> AsyncIterator[Dict[str, Any]]:
        yield {
            "type": "user",
            "session_id": "",
            "message": {"role": "user", "content": context or "say hello"},
            "parent_tool_use_id": None,
        }
        await done_event.wait()

    def _claude_sdk_can_use_tool(self, session_id: str) -> Callable[..., Any]:
        async def can_use_tool(tool_name: str, input_data: Dict[str, Any], context: Any) -> Any:
            return await self._permission_adapter.can_use_tool(
                session_id,
                tool_name,
                input_data,
                context,
            )

        return can_use_tool

    def _claude_sdk_message_to_events(
        self,
        session_id: str,
        message: Any,
    ) -> List[Dict[str, Any]]:
        message_type = message.__class__.__name__
        if message_type == "SystemMessage":
            return [self._unifier._mk_task_update(session_id, AgentType.CLAUDE, AgentState.SUBMITTED)]

        if message_type == "AssistantMessage":
            events: List[Dict[str, Any]] = []
            text_parts: List[str] = []
            saw_tool_use = False
            for item in getattr(message, "content", []) or []:
                item_type = item.__class__.__name__
                text = getattr(item, "text", None)
                if item_type == "TextBlock" and text:
                    text_parts.append(str(text))
                elif item_type in {"ToolUseBlock", "ServerToolUseBlock"}:
                    saw_tool_use = True

            if saw_tool_use:
                events.append(self._unifier._mk_task_update(
                    session_id,
                    AgentType.CLAUDE,
                    AgentState.EXECUTING,
                ))
            if text_parts:
                events.append(self._unifier._mk_delta(
                    session_id,
                    AgentType.CLAUDE,
                    "\n".join(text_parts),
                ))
            return events

        if message_type == "ResultMessage":
            if getattr(message, "is_error", False):
                return [self._unifier._mk_task_failed(
                    session_id,
                    AgentType.CLAUDE,
                    str(getattr(message, "api_error_status", None) or getattr(message, "subtype", "ERROR")),
                    getattr(message, "result", None) or "Claude Agent SDK run failed.",
                )]
            return [self._unifier._mk_task_completed(
                session_id,
                AgentType.CLAUDE,
                getattr(message, "result", None) or "Claude Agent SDK run completed.",
            )]

        return []

    # ------------------------------------------------------------------ #
    #  Command builders
    # ------------------------------------------------------------------ #

    def _build_command(self, session_id: str, context: str) -> List[str]:
        if self.agent_type == AgentType.CLAUDE:
            return self._build_claude_cmd(session_id, context)
        return self._build_codex_cmd(session_id, context)

    def _build_claude_cmd(self, session_id: str, context: str) -> List[str]:
        cmd = [self._executable]
        if self._mode == "sdk_url":
            raise RuntimeError(
                "Claude Code rejects local --sdk-url companion endpoints; use agent_sdk mode."
            )
        else:
            # Headless mode with stream-json output
            cmd += ["-p"]
            if context:
                cmd += [context]
            cmd += ["--output-format", "stream-json", "--verbose"]
        cmd += self._dedupe_args(cmd, self._args)
        return cmd

    def _build_codex_cmd(self, session_id: str, context: str) -> List[str]:
        cmd = [self._executable]
        if self._mode == "remote_control":
            cmd += ["remote-control"]
        else:
            # exec --json mode: safest for programmatic use
            cmd += ["exec", "--json"]
            if context:
                cmd += [context]
        cmd += self._dedupe_args(cmd, self._args)
        return cmd

    @staticmethod
    def _dedupe_args(existing: List[str], extra: List[str]) -> List[str]:
        """Keep config args additive while avoiding duplicated built-in flags."""
        existing_flags = {arg for arg in existing if arg.startswith("-")}
        filtered: List[str] = []
        skip_next = False
        for index, arg in enumerate(extra):
            if skip_next:
                skip_next = False
                continue
            if arg in existing_flags:
                if index + 1 < len(extra) and not extra[index + 1].startswith("-"):
                    skip_next = True
                continue
            filtered.append(arg)
        return filtered

    # ------------------------------------------------------------------ #
    #  Stream reading
    # ------------------------------------------------------------------ #

    async def _read_stream(
        self,
        session_id: str,
        stdout: asyncio.StreamReader,
        stderr: asyncio.StreamReader,
    ) -> None:
        """Read stdout/stderr lines and convert to unified events."""

        async def _read_pipe(reader: asyncio.StreamReader, prefix: str) -> None:
            while True:
                try:
                    line = await reader.readline()
                except Exception:
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                if not text:
                    continue

                if prefix == "ERR":
                    # Log stderr as-is; do not parse as events
                    print(f"[{session_id}] {self.agent_type.value} stderr: {text}")
                    continue

                # Parse stdout as native agent event
                if self.agent_type == AgentType.CLAUDE:
                    event = self._unifier.claude_to_unified(text, session_id)
                else:
                    event = self._unifier.codex_to_unified(text, session_id)

                if event:
                    if event.get("type") == "permission_request" and hasattr(
                        self._permission_adapter,
                        "register_control_request",
                    ):
                        native_request = event.get("native")
                        if isinstance(native_request, dict):
                            self._permission_adapter.register_control_request(
                                session_id,
                                native_request,
                            )
                    # Update session state in manager
                    self._update_session_from_event(session_id, event)
                    # Forward to Bridge Server for device dispatch
                    json_line = self._unifier.encode_device_message(event)
                    if self._on_unified_event:
                        try:
                            self._on_unified_event(json_line)
                        except Exception:
                            pass

        # Run stdout and stderr readers concurrently
        await asyncio.gather(
            _read_pipe(stdout, "OUT"),
            _read_pipe(stderr, "ERR"),
            return_exceptions=True,
        )

        # Process exited
        self._processes.pop(session_id, None)
        sess = self._sm.get(session_id)
        if sess and sess.state not in {
            AgentState.COMPLETED, AgentState.FAILED,
            AgentState.CANCELLED, AgentState.ERROR,
        }:
            self._sm.update_state(session_id, AgentState.OFFLINE)
            event = self._unifier._mk_task_failed(
                session_id, self.agent_type,
                "OFFLINE", "Agent process exited unexpectedly."
            )
            if self._on_unified_event:
                self._on_unified_event(self._unifier.encode_device_message(event))

    def _update_session_from_event(self, session_id: str, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")
        if etype == "task_update":
            state_str = event.get("state", "IDLE")
            self._sm.update_state(session_id, AgentState(state_str))
        elif etype == "agent_message_delta":
            self._sm.update_delta(session_id, event.get("delta", ""))
        elif etype == "task_completed":
            self._sm.update_state(session_id, AgentState.COMPLETED)
        elif etype == "task_failed":
            self._sm.update_state(session_id, AgentState.FAILED)

    def _emit_unified_event(self, event: Dict[str, Any]) -> None:
        session_id = event.get("session_id", "")
        if session_id:
            self._update_session_from_event(session_id, event)
        json_line = self._unifier.encode_device_message(event)
        if self._on_unified_event:
            try:
                self._on_unified_event(json_line)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  Callback registration
    # ------------------------------------------------------------------ #

    def set_event_callback(self, callback: Callable[[str], None]) -> None:
        self._on_unified_event = callback
