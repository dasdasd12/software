import json
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices.transports.serial_cdc import (  # noqa: E402
    AkpkSerialClient,
    DeviceError,
    SerialCdcError,
)
from keyboard.akpk import build_package, crc32c  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT_DIR / "config" / "factory_default_profile.json"


class FakeKeyboardSerial:
    """Line-level emulation of the firmware pc_link command handler."""

    def __init__(self, *args, **kwargs):
        self._pending: list[bytes] = []
        self.staging = bytearray()
        self.expected_len = 0
        self.expected_crc = 0
        self.upload_slot = None
        self.slots: dict[int, bytes] = {}
        self.invalid_slots: set[int] = set()
        self.active_slot = 0
        self.fail_next_data = False

    # serial API used by the client
    def write(self, raw: bytes) -> None:
        line = raw.decode("ascii").strip()
        if line:
            self._handle(line)

    def readline(self) -> bytes:
        return self._pending.pop(0) if self._pending else b""

    def close(self) -> None:
        pass

    # firmware behaviour
    def _reply(self, line: str) -> None:
        self._pending.append((line + "\r\n").encode("ascii"))

    def _handle(self, line: str) -> None:
        if not line.startswith("AK "):
            return
        parts = line[3:].split(" ")
        cmd = parts[0]
        if cmd == "PING":
            self._reply("OK PONG 1")
        elif cmd == "INFO":
            slots = "".join(
                "0" if s in self.invalid_slots else "1" for s in (1, 2, 3)
            )
            self._reply(
                f"OK INFO active={self.active_slot} id16=1abd gen=1 "
                f"slots={slots}"
            )
        elif cmd == "BEGIN":
            self.upload_slot = int(parts[1], 16)
            self.expected_len = int(parts[2], 16)
            self.expected_crc = int(parts[3], 16)
            self.staging = bytearray()
            self._reply("OK BEGIN")
        elif cmd == "DATA":
            if self.fail_next_data:
                self.fail_next_data = False
                self._reply("ERR 5 write")
                return
            offset = int(parts[1], 16)
            data = bytes.fromhex(parts[2])
            if offset != len(self.staging):
                self._reply("ERR 4 offset")
                return
            self.staging.extend(data)
            self._reply(f"OK DATA {len(self.staging):x}")
        elif cmd == "COMMIT":
            if (len(self.staging) != self.expected_len or
                    crc32c(bytes(self.staging)) != self.expected_crc):
                self._reply("ERR 6 crc32c")
                return
            self.slots[self.upload_slot] = bytes(self.staging)
            self.invalid_slots.discard(self.upload_slot)
            self._reply(f"OK COMMIT {self.upload_slot}")
        elif cmd == "ACTIVATE":
            slot = int(parts[1], 16)
            if slot in self.invalid_slots:
                self._reply("ERR 7 install")
                return
            self.active_slot = slot
            self._reply(f"OK ACTIVATE {slot} id16=1abd gen=1")
        elif cmd == "ABORT":
            self.staging = bytearray()
            self._reply("OK ABORT")
        else:
            self._reply("ERR 1 cmd")


def _client(fake: FakeKeyboardSerial) -> AkpkSerialClient:
    return AkpkSerialClient("FAKE", serial_factory=lambda *a, **k: fake)


def _package() -> bytes:
    profile = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
    return build_package(profile).package


def test_upload_streams_package_byte_exact():
    fake = FakeKeyboardSerial()
    package = _package()
    seen = []

    with _client(fake) as client:
        assert client.ping().startswith("PONG")
        client.upload(package, slot=2, chunk_size=64,
                      progress=lambda done, total: seen.append(done))

    assert fake.slots[2] == package
    assert seen[-1] == len(package)

    with _client(fake) as client:
        reply = client.activate(2)
    assert reply.startswith("ACTIVATE 2")
    assert fake.active_slot == 2


def test_info_parsing():
    fake = FakeKeyboardSerial()
    fake.invalid_slots.add(2)
    fake.active_slot = 1
    with _client(fake) as client:
        info = client.info()
    assert info.active_slot == 1
    assert info.profile_id16 == 0x1ABD
    assert info.generation == 1
    assert info.slot_valid == (True, False, True)


def test_device_error_is_raised():
    fake = FakeKeyboardSerial()
    fake.fail_next_data = True
    package = _package()

    with _client(fake) as client:
        with pytest.raises(DeviceError) as err:
            client.upload(package, slot=1)
    assert err.value.code == 5


def test_activate_unwritten_slot_uses_default():
    fake = FakeKeyboardSerial()
    with _client(fake) as client:
        reply = client.activate(3)
    assert reply.startswith("ACTIVATE 3")
    assert fake.active_slot == 3
    assert 3 not in fake.slots


def test_activate_invalid_slot_errors():
    fake = FakeKeyboardSerial()
    fake.invalid_slots.add(3)
    with _client(fake) as client:
        with pytest.raises(DeviceError):
            client.activate(3)


def test_slot_range_validation():
    fake = FakeKeyboardSerial()
    with _client(fake) as client:
        with pytest.raises(SerialCdcError):
            client.upload(b"\x00" * 16, slot=0)
        with pytest.raises(SerialCdcError):
            client.activate(9)
