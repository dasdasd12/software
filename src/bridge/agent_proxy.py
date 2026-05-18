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
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

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
    ):
        self.agent_type = agent_type
        self._sm = session_manager
        self._unifier = unifier
        self._executable = executable or self._find_executable()
        self._mode = mode
        self._args = args or []
        self._env = {**os.environ, **(env or {})}
        if api_key:
            key_env = "ANTHROPIC_API_KEY" if agent_type == AgentType.CLAUDE else "CODEX_API_KEY"
            self._env[key_env] = api_key
        self._session_timeout_sec = session_timeout_sec

        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._read_tasks: Dict[str, asyncio.Task] = {}
        self._on_unified_event: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------ #
    #  Discovery
    # ------------------------------------------------------------------ #

    def _find_executable(self) -> Optional[str]:
        name = "claude" if self.agent_type == AgentType.CLAUDE else "codex"
        return shutil.which(name)

    def is_available(self) -> bool:
        return self._executable is not None and os.path.isfile(self._executable)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def launch(self, session_id: str, context: str = "") -> Optional[Session]:
        """Start a new session subprocess for this agent."""
        if not self.is_available():
            raise RuntimeError(f"{self.agent_type.value} executable not found")

        # Check concurrent limit
        agent_sessions = self._sm.list_by_agent(self.agent_type)
        # Max concurrent is handled by session manager config; here we just log

        # Build command line based on mode
        cmd = self._build_command(session_id, context)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
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
        proc = self._processes.get(session_id)
        if not proc:
            return False
        try:
            proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT if sys.platform == "win32" else subprocess.signal.SIGINT)
            return True
        except Exception:
            return False

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
            # Hidden --sdk-url mode (WebSocket); requires companion listener
            cmd += ["--sdk-url", "ws://localhost:9999"]
        else:
            # Headless mode with stream-json output
            cmd += ["-p"]
            if context:
                cmd += [context]
            cmd += ["--output-format", "stream-json"]
        cmd += self._args
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
        cmd += self._args
        return cmd

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

    # ------------------------------------------------------------------ #
    #  Callback registration
    # ------------------------------------------------------------------ #

    def set_event_callback(self, callback: Callable[[str], None]) -> None:
        self._on_unified_event = callback
