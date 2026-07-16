import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices.approval_display import (  # noqa: E402
    SUMMARY_MAX_BYTES,
    TOOL_MAX_BYTES,
    build_approval_display_payload,
)
from devices.transports.serial_cdc import (  # noqa: E402
    AkpkSerialClient,
    ApprovalCdcSender,
    SerialCdcError,
)


class FakeSerial:
    def __init__(self):
        self.commands = []
        self.replies = []
        self.closed = False

    def write(self, raw):
        command = raw.decode("ascii").strip()
        self.commands.append(command)
        self.replies.append(b"OK APPROVAL\n")

    def readline(self):
        return self.replies.pop(0) if self.replies else b""

    def close(self):
        self.closed = True


class RecordingApprovalClient:
    def __init__(self, records, **kwargs):
        self.records = records
        self.kwargs = kwargs

    def __enter__(self):
        self.records.append(("open", self.kwargs))
        return self

    def __exit__(self, *_exc):
        self.records.append(("close", self.kwargs["port"]))

    def approval_show(self, tag, risk, tool, summary):
        self.records.append(("show", tag, risk, tool, summary))
        return "APPROVAL"

    def approval_clear(self, tag):
        self.records.append(("clear", tag))
        return "APPROVAL"


def test_akpk_client_encodes_approval_commands_exactly():
    serial = FakeSerial()
    client = AkpkSerialClient(
        "FAKE",
        serial_factory=lambda *_args, **_kwargs: serial,
    )

    with client:
        client.approval_show(
            "deadbeef",
            2,
            b"Bash",
            b"python -m pytest",
        )
        client.approval_clear("deadbeef")

    assert serial.commands == [
        "AK APPROVAL SHOW deadbeef 2 42617368 707974686f6e202d6d20707974657374",
        "AK APPROVAL CLEAR deadbeef",
    ]
    assert serial.closed is True


def test_akpk_client_rejects_unbounded_or_non_ascii_approval_fields():
    serial = FakeSerial()
    client = AkpkSerialClient(
        "FAKE",
        serial_factory=lambda *_args, **_kwargs: serial,
    )

    with client:
        with pytest.raises(SerialCdcError, match="tool.*16"):
            client.approval_show("deadbeef", 2, b"x" * 17, b"ok")
        with pytest.raises(SerialCdcError, match="summary.*120"):
            client.approval_show("deadbeef", 2, b"Bash", b"x" * 121)
        with pytest.raises(SerialCdcError, match="ASCII"):
            client.approval_show("deadbeef", 2, b"\xff", b"ok")
        with pytest.raises(SerialCdcError, match="printable ASCII"):
            client.approval_show("deadbeef", 2, b"Bash", b"line\nbreak")

    assert serial.commands == []


def test_approval_sender_discovers_production_cdc_and_opens_per_command():
    records = []
    ports = [
        SimpleNamespace(
            device="COM7",
            vid=0x1A86,
            pid=0xFE07,
            description="AI Key H417",
            product="",
            interface="",
            manufacturer="WCH",
        ),
        SimpleNamespace(
            device="COM17",
            vid=0x1A86,
            pid=0xFE17,
            description="USB Serial Device",
            product="AI Key H417 NKRO",
            interface="CDC",
            manufacturer="WCH",
        ),
    ]
    sender = ApprovalCdcSender(
        port_provider=lambda: ports,
        client_factory=lambda **kwargs: RecordingApprovalClient(
            records,
            **kwargs,
        ),
    )

    async def run():
        await sender.show("0123abcd", 3, b"Write", b"C:/work/a.py")
        await sender.clear("0123abcd")

    asyncio.run(run())

    opens = [entry for entry in records if entry[0] == "open"]
    assert [entry[1]["port"] for entry in opens] == ["COM17", "COM17"]
    assert ("show", "0123abcd", 3, b"Write", b"C:/work/a.py") in records
    assert ("clear", "0123abcd") in records
    assert len([entry for entry in records if entry[0] == "close"]) == 2


def test_approval_display_prefers_actual_command_and_bounds_ascii():
    bash = build_approval_display_payload(
        request_id="req_1",
        session_id="sess_1",
        risk_level="high",
        tool="Bash",
        description="fallback",
        native={
            "tool_input": {
                "description": "运行 " + ("x" * 200),
                "command": "python dangerous.py",
            }
        },
    )
    unicode_command = build_approval_display_payload(
        request_id="req_unicode",
        session_id="sess_1",
        risk_level="high",
        tool="Bash",
        description="执行 Python 测试代码",
        native={
            "tool_input": {
                "command": 'python -c "print(\u201c测试成功\u201d)"',
            }
        },
    )
    file_request = build_approval_display_payload(
        request_id="req_2",
        session_id="sess_1",
        risk_level="medium",
        tool="Write",
        description="write a file",
        native={"tool_input": {"file_path": "C:/work/generated.py"}},
    )
    long_tool = build_approval_display_payload(
        request_id="req_3",
        session_id="sess_1",
        risk_level="low",
        tool="ToolNameLongerThanSixteenBytes",
        description="bounded\x00tool name",
    )

    assert len(long_tool.tool) == TOOL_MAX_BYTES
    assert bash.summary == b"python dangerous.py"
    assert unicode_command.summary == b'python -c "print([...])"'
    assert b"?" not in unicode_command.summary
    assert bash.risk == 2
    assert len(bash.tag8hex) == 8
    assert file_request.summary == b"C:/work/generated.py"
    assert b"\x00" not in long_tool.summary
    assert long_tool.summary == b"bounded[...]tool name"


def test_approval_display_collapses_long_non_ascii_runs_before_truncation():
    payload = build_approval_display_payload(
        request_id="req_4",
        session_id="sess_1",
        risk_level="medium",
        tool="Custom",
        description=("中文内容" * 100) + " python " + ("更多内容" * 100),
    )

    assert payload.summary == b"[...] python [...]"
    assert len(payload.summary) < SUMMARY_MAX_BYTES


def test_approval_display_marks_non_ascii_path_segments():
    payload = build_approval_display_payload(
        request_id="req_5",
        session_id="sess_1",
        risk_level="medium",
        tool="Write",
        description="write a file",
        native={"tool_input": {"file_path": "C:/tmp/审批结果.py"}},
    )

    assert payload.summary == b"C:/tmp/[...].py"
