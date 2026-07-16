"""Serial (USB CDC) client for the keyboard's AKPK upload protocol.

Talks the line protocol implemented by the H417 V3F ``pc_link`` module
(hardware/firmware/h417/v3f/applications/pc_link.c):

    AK PING / AK INFO / AK BEGIN / AK DATA / AK COMMIT / AK ABORT /
    AK ACTIVATE / AK FACTORY / AK APPROVAL SHOW / AK APPROVAL CLEAR

pyserial is imported lazily so the module stays importable (and
testable with an injected fake) without the dependency installed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import string
import time
from typing import Any, Callable, Iterable, Sequence

from keyboard.akpk import crc32c


DEFAULT_WCH_USB_VID = 0x1A86
DEFAULT_H417_USBFS_CDC_PIDS = (0xFE17, 0xFE07)
APPROVAL_TOOL_MAX_BYTES = 16
APPROVAL_SUMMARY_MAX_BYTES = 120
APPROVAL_COMMAND_MAX_BYTES = 320


class SerialCdcError(RuntimeError):
    pass


class DeviceError(SerialCdcError):
    """Device replied with an ERR line."""

    def __init__(self, code: int, detail: str, command: str) -> None:
        super().__init__(f"device error {code} ({detail}) for: {command}")
        self.code = code
        self.detail = detail
        self.command = command


@dataclass(frozen=True)
class DeviceInfo:
    active_slot: int
    profile_id16: int
    generation: int
    slot_valid: tuple[bool, bool, bool]


class AkpkSerialClient:
    """Blocking client for the AK line protocol."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        timeout: float = 3.0,
        serial_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._timeout = timeout
        self._serial_factory = serial_factory
        self._serial: Any = None

    def open(self) -> None:
        if self._serial is not None:
            return
        factory = self._serial_factory
        if factory is None:
            try:
                import serial  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover
                raise SerialCdcError(
                    "pyserial is required for real serial transport "
                    "(pip install pyserial)"
                ) from exc
            factory = serial.Serial
        self._serial = factory(
            self._port, baudrate=self._baudrate, timeout=self._timeout
        )

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def __enter__(self) -> "AkpkSerialClient":
        self.open()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- protocol ------------------------------------------------------

    def _request(self, command: str) -> str:
        if self._serial is None:
            raise SerialCdcError("client is not open")
        self._serial.write((command + "\n").encode("ascii"))
        deadline = time.monotonic() + self._timeout
        while True:
            raw = self._serial.readline()
            if not raw:
                if time.monotonic() > deadline:
                    raise SerialCdcError(f"timeout waiting reply to {command}")
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if line.startswith("OK "):
                return line[3:]
            if line.startswith("ERR "):
                parts = line.split(" ", 2)
                code = int(parts[1]) if len(parts) > 1 else -1
                detail = parts[2] if len(parts) > 2 else ""
                raise DeviceError(code, detail, command)
            # skip debug/noise lines and keep waiting

    def ping(self) -> str:
        return self._request("AK PING")

    def info(self) -> DeviceInfo:
        reply = self._request("AK INFO")
        fields = dict(
            item.split("=", 1) for item in reply.split(" ")[1:] if "=" in item
        )
        slots = fields.get("slots", "000")
        return DeviceInfo(
            active_slot=int(fields.get("active", "0")),
            profile_id16=int(fields.get("id16", "0"), 16),
            generation=int(fields.get("gen", "0")),
            slot_valid=tuple(c == "1" for c in slots[:3].ljust(3, "0")),
        )

    def upload(
        self,
        package: bytes,
        slot: int,
        chunk_size: int = 64,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        if not 1 <= slot <= 3:
            raise SerialCdcError("slot must be 1..3")
        if not 1 <= chunk_size <= 128:
            raise SerialCdcError("chunk_size must be 1..128")

        crc = crc32c(package)
        self._request(f"AK BEGIN {slot:x} {len(package):x} {crc:08x}")

        offset = 0
        while offset < len(package):
            data = package[offset:offset + chunk_size]
            reply = self._request(f"AK DATA {offset:x} {data.hex()}")
            next_offset = int(reply.split(" ")[1], 16)
            if next_offset != offset + len(data):
                raise SerialCdcError(
                    f"device acked offset {next_offset:#x}, "
                    f"expected {offset + len(data):#x}"
                )
            offset = next_offset
            if progress is not None:
                progress(offset, len(package))

        reply = self._request("AK COMMIT")
        if not reply.startswith("COMMIT"):
            raise SerialCdcError(f"unexpected commit reply: {reply}")

    def activate(self, slot: int) -> str:
        if not 0 <= slot <= 3:
            raise SerialCdcError("slot must be 0..3")
        return self._request(f"AK ACTIVATE {slot:x}")

    def abort(self) -> None:
        self._request("AK ABORT")

    def approval_show(
        self,
        tag8hex: str,
        risk: int,
        tool: bytes,
        summary: bytes,
    ) -> str:
        tag = _approval_tag(tag8hex)
        risk_value = _approval_risk(risk)
        tool_value = _approval_field(
            tool,
            APPROVAL_TOOL_MAX_BYTES,
            "tool",
        )
        summary_value = _approval_field(
            summary,
            APPROVAL_SUMMARY_MAX_BYTES,
            "summary",
        )
        command = (
            "AK APPROVAL SHOW "
            f"{tag} {risk_value:x} "
            f"{_hex_or_dash(tool_value)} {_hex_or_dash(summary_value)}"
        )
        if len(command.encode("ascii")) + 1 >= APPROVAL_COMMAND_MAX_BYTES:
            raise SerialCdcError(
                "approval command must be shorter than 320 bytes"
            )
        return self._request(command)

    def approval_clear(self, tag8hex: str) -> str:
        return self._request(f"AK APPROVAL CLEAR {_approval_tag(tag8hex)}")


class ApprovalCdcSender:
    """Open, send one approval command, and close the H417 USBFS CDC port."""

    def __init__(
        self,
        *,
        port: str = "",
        baudrate: int = 115200,
        timeout: float = 1.0,
        vid: int = DEFAULT_WCH_USB_VID,
        pids: Sequence[int] = DEFAULT_H417_USBFS_CDC_PIDS,
        client_factory: Callable[..., AkpkSerialClient] = AkpkSerialClient,
        port_provider: Callable[[], Iterable[Any]] | None = None,
    ) -> None:
        self._port = str(port or "").strip()
        self._baudrate = int(baudrate)
        self._timeout = max(0.05, float(timeout))
        self._vid = int(vid)
        self._pids = tuple(int(pid) for pid in pids)
        self._client_factory = client_factory
        self._port_provider = port_provider
        self._send_lock = asyncio.Lock()

    async def show(
        self,
        tag8hex: str,
        risk: int,
        tool: bytes,
        summary: bytes,
    ) -> str:
        async with self._send_lock:
            return await asyncio.to_thread(
                self._show_sync,
                tag8hex,
                risk,
                bytes(tool),
                bytes(summary),
            )

    async def clear(self, tag8hex: str) -> str:
        async with self._send_lock:
            return await asyncio.to_thread(self._clear_sync, tag8hex)

    def resolve_port(self) -> str:
        if self._port:
            return self._port

        provider = self._port_provider
        if provider is None:
            try:
                from serial.tools import list_ports  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover
                raise SerialCdcError(
                    "pyserial is required to discover the H417 USBFS CDC port"
                ) from exc
            provider = list_ports.comports

        candidates = []
        for port in provider():
            device = str(getattr(port, "device", "") or "").strip()
            if not device:
                continue
            if getattr(port, "vid", None) != self._vid:
                continue
            pid = getattr(port, "pid", None)
            if pid not in self._pids:
                continue
            candidates.append((self._port_score(port), device))

        if not candidates:
            expected = ", ".join(f"0x{pid:04X}" for pid in self._pids)
            raise SerialCdcError(
                "H417 USBFS CDC port not found "
                f"(VID 0x{self._vid:04X}, PID {expected})"
            )
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]

    def _show_sync(
        self,
        tag8hex: str,
        risk: int,
        tool: bytes,
        summary: bytes,
    ) -> str:
        with self._client() as client:
            return client.approval_show(tag8hex, risk, tool, summary)

    def _clear_sync(self, tag8hex: str) -> str:
        with self._client() as client:
            return client.approval_clear(tag8hex)

    def _client(self) -> AkpkSerialClient:
        return self._client_factory(
            port=self.resolve_port(),
            baudrate=self._baudrate,
            timeout=self._timeout,
        )

    def _port_score(self, port: Any) -> int:
        pid = getattr(port, "pid", None)
        try:
            pid_preference = len(self._pids) - self._pids.index(pid)
        except ValueError:
            pid_preference = 0
        text = " ".join(
            str(getattr(port, field, "") or "")
            for field in ("description", "product", "interface", "manufacturer")
        ).lower()
        score = pid_preference * 100
        if "cdc" in text:
            score += 30
        if "h417" in text:
            score += 20
        if "ai key" in text or "kiiie" in text:
            score += 10
        return score


def _approval_tag(value: str) -> str:
    tag = str(value or "").lower()
    if len(tag) != 8 or any(ch not in string.hexdigits for ch in tag):
        raise SerialCdcError("approval tag must be exactly 8 hexadecimal digits")
    return tag


def _approval_risk(value: int) -> int:
    risk = int(value)
    if not 0 <= risk <= 0xF:
        raise SerialCdcError("approval risk must fit one hexadecimal digit")
    return risk


def _hex_or_dash(value: bytes) -> str:
    raw = bytes(value)
    return raw.hex() if raw else "-"


def _approval_field(value: bytes, limit: int, field: str) -> bytes:
    raw = bytes(value)
    if len(raw) > limit:
        raise SerialCdcError(
            f"approval {field} must be at most {limit} bytes"
        )
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        raise SerialCdcError(
            f"approval {field} must contain printable ASCII bytes only"
        )
    return raw
