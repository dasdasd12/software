from pathlib import Path
import sys


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))

from agent_proxy import AgentProxy  # noqa: E402
from protocol_unifier import ProtocolUnifier  # noqa: E402
from session_manager import AgentType, SessionManager  # noqa: E402


def make_proxy(agent_type, args=None):
    return AgentProxy(
        agent_type=agent_type,
        session_manager=SessionManager(),
        unifier=ProtocolUnifier(),
        executable="agent.exe",
        args=args or [],
    )


def test_claude_stream_json_command_includes_verbose():
    proxy = make_proxy(AgentType.CLAUDE)

    cmd = proxy._build_claude_cmd("sess_1", "hello")

    assert cmd == [
        "agent.exe",
        "-p",
        "hello",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def test_claude_command_dedupes_user_output_format_and_verbose_args():
    proxy = make_proxy(AgentType.CLAUDE, args=["--output-format", "json", "--verbose", "--model", "sonnet"])

    cmd = proxy._build_claude_cmd("sess_1", "hello")

    assert cmd.count("--output-format") == 1
    assert cmd.count("--verbose") == 1
    assert "--model" in cmd
    assert "sonnet" in cmd
