"""Serial (USB CDC) client for the keyboard's AKPK upload protocol.

Talks the line protocol implemented by the H417 V3F ``pc_link`` module
(hardware/firmware/h417/v3f/applications/pc_link.c):

    AK PING / AK INFO / AK BEGIN / AK DATA / AK COMMIT / AK ABORT /
    AK ACTIVATE / AK FACTORY

pyserial is imported lazily so the module stays importable (and
testable with an injected fake) without the dependency installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import time

from keyboard.akpk import crc32c


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
