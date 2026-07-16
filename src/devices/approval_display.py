"""Bounded approval payloads for the H417/V5F screen bridge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import unicodedata
from typing import Any, Dict, Optional


TOOL_MAX_BYTES = 16
SUMMARY_MAX_BYTES = 120
RISK_CODES = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
    "destructive": 4,
}
FILE_PATH_FIELDS = (
    "file_path",
    "path",
    "notebook_path",
    "destination",
    "target_path",
)
FILE_TOOLS = {
    "edit",
    "multiedit",
    "notebookedit",
    "read",
    "write",
}
DISPLAY_PUNCTUATION = str.maketrans(
    {
        "\u2013": "-",
        "\u2014": "-",
        "\u2022": "*",
        "\u2026": "...",
        "\u2212": "-",
    }
)
OMITTED_TEXT_MARKER = "[...]"


@dataclass(frozen=True)
class ApprovalDisplayPayload:
    tag8hex: str
    risk: int
    tool: bytes
    summary: bytes


def build_approval_display_payload(
    *,
    request_id: str,
    session_id: str,
    risk_level: Any,
    tool: str,
    description: str,
    native: Optional[Dict[str, Any]] = None,
) -> ApprovalDisplayPayload:
    native_payload = native if isinstance(native, dict) else {}
    tool_input = (
        native_payload.get("tool_input")
        if isinstance(native_payload.get("tool_input"), dict)
        else {}
    )
    normalized_tool = str(tool or "unknown")
    summary_source = _summary_source(
        normalized_tool,
        str(description or ""),
        tool_input,
    )
    risk_name = getattr(risk_level, "value", risk_level)
    return ApprovalDisplayPayload(
        tag8hex=approval_tag(request_id, session_id),
        risk=RISK_CODES.get(str(risk_name or "").lower(), RISK_CODES["medium"]),
        tool=_bounded_ascii(normalized_tool, TOOL_MAX_BYTES),
        summary=_bounded_ascii(summary_source, SUMMARY_MAX_BYTES),
    )


def approval_tag(request_id: str, session_id: str) -> str:
    raw = f"{session_id}\0{request_id}".encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:8]


def _summary_source(
    tool: str,
    description: str,
    tool_input: Dict[str, Any],
) -> str:
    tool_key = tool.strip().lower()
    if tool_key == "bash":
        return (
            _text(tool_input.get("command"))
            or _text(tool_input.get("description"))
            or description
            or "Claude requests shell access."
        )
    if tool_key in FILE_TOOLS:
        for field in FILE_PATH_FIELDS:
            value = _text(tool_input.get(field))
            if value:
                return value
    return description or f"Claude requests permission to use {tool}."


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _bounded_ascii(value: str, limit: int) -> bytes:
    normalized = _display_ascii(value)
    raw = normalized.encode("ascii")
    if len(raw) <= limit:
        return raw
    if limit <= 3:
        return b"." * limit
    return raw[: limit - 3] + b"..."


def _display_ascii(value: str) -> str:
    source = unicodedata.normalize(
        "NFKD",
        str(value or "").translate(DISPLAY_PUNCTUATION),
    )
    output = []
    omitted_run = False

    for char in source:
        codepoint = ord(char)
        if 0x20 <= codepoint <= 0x7E:
            output.append(char)
            omitted_run = False
        elif char.isspace():
            output.append(" ")
            omitted_run = False
        elif unicodedata.combining(char):
            continue
        elif not omitted_run:
            output.append(OMITTED_TEXT_MARKER)
            omitted_run = True

    return " ".join("".join(output).split())
