"""Newline-delimited JSON transport for the desktop bridge.

Each input line is one request::

    {"id":"req-1","method":"bridge.hello","params":{}}

The bridge writes exactly one matching response.  Long-running operations may
write event lines before that response::

    {"event":"profile.install.progress","request_id":"req-2","data":{...}}

Only protocol data is written to stdout.  Diagnostic tracebacks go to stderr
so they cannot corrupt the Tauri/Rust message stream.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sys
import threading
import traceback
from typing import Any, Callable, Mapping, Protocol, TextIO


JsonObject = dict[str, Any]
EventEmitter = Callable[[str, Mapping[str, Any]], None]


@dataclass
class BridgeError(Exception):
    """An expected failure that is safe to expose across the bridge."""

    code: str
    message: str
    recoverable: bool = True
    details: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return self.message

    def as_payload(self) -> JsonObject:
        payload: JsonObject = {
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }
        if self.details is not None:
            payload["details"] = dict(self.details)
        return payload


class RequestHandler(Protocol):
    def dispatch(
        self,
        method: str,
        params: Mapping[str, Any],
        request_id: str | int,
        emit_event: EventEmitter,
    ) -> Any:
        ...

    def close(self) -> None:
        ...


class JsonlWriter:
    """Serialize messages atomically, including progress callbacks."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def write(self, message: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._lock:
            self._stream.write(encoded + "\n")
            self._stream.flush()


def serve_jsonl(
    handler: RequestHandler,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Serve requests until stdin reaches EOF.

    Malformed input is reported as an ``E_PARSE``/``E_INVALID_REQUEST``
    response and does not terminate the process.  EOF cleanly disconnects any
    open device through ``handler.close``.
    """

    writer = JsonlWriter(stdout)
    try:
        for raw_line in stdin:
            if not raw_line.strip():
                continue
            _handle_line(raw_line, handler, writer, stderr)
    finally:
        handler.close()
    return 0


def _handle_line(
    raw_line: str,
    handler: RequestHandler,
    writer: JsonlWriter,
    stderr: TextIO,
) -> None:
    request_id: str | int | None = None
    try:
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise BridgeError(
                "E_PARSE",
                "Request is not valid JSON",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc

        if not isinstance(request, dict):
            raise BridgeError(
                "E_INVALID_REQUEST", "Request must be a JSON object"
            )

        candidate_id = request.get("id")
        if isinstance(candidate_id, bool) or not isinstance(
            candidate_id, (str, int)
        ):
            raise BridgeError(
                "E_INVALID_REQUEST", "Request id must be a string or integer"
            )
        request_id = candidate_id

        method = request.get("method")
        if not isinstance(method, str) or not method:
            raise BridgeError(
                "E_INVALID_REQUEST", "Request method must be a non-empty string"
            )

        params = request.get("params", {})
        if not isinstance(params, dict):
            raise BridgeError(
                "E_INVALID_PARAMS", "Request params must be a JSON object"
            )

        def emit_event(event: str, data: Mapping[str, Any]) -> None:
            writer.write(
                {
                    "event": event,
                    "request_id": request_id,
                    "data": dict(data),
                }
            )

        result = handler.dispatch(method, params, request_id, emit_event)
        writer.write({"id": request_id, "ok": True, "result": result})
    except BridgeError as exc:
        writer.write({"id": request_id, "ok": False, "error": exc.as_payload()})
    except Exception as exc:  # pragma: no cover - defensive process boundary
        traceback.print_exc(file=stderr)
        writer.write(
            {
                "id": request_id,
                "ok": False,
                "error": {
                    "code": "E_INTERNAL",
                    "message": "Unexpected desktop bridge failure",
                    "recoverable": False,
                    "details": {"errorType": type(exc).__name__},
                },
            }
        )
