from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from core import CommandEnvelope, CommandSource  # noqa: E402


def test_command_envelope_round_trips_symbolic_string_target():
    command = CommandEnvelope(
        command_id="cmd_symbolic",
        type="agent.run.interrupt",
        source=CommandSource(kind="keyboard-device", client_id="kbd_01", device_id="kbd_01"),
        target="focused_run",
    )

    encoded = command.to_dict()
    decoded = CommandEnvelope.from_dict(encoded)

    assert encoded["target"] == "focused_run"
    assert decoded.target == "focused_run"


def test_command_envelope_round_trips_focused_permission_target():
    command = CommandEnvelope(
        command_id="cmd_permission",
        type="agent.permission.respond",
        source=CommandSource(kind="keyboard-device", client_id="kbd_01", device_id="kbd_01"),
        target="focused_permission",
        payload={"approved": True},
    )

    encoded = command.to_dict()
    decoded = CommandEnvelope.from_dict(encoded)

    assert encoded["target"] == "focused_permission"
    assert decoded.target == "focused_permission"
