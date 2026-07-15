from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from desktop_bridge.protocol import BridgeError, serve_jsonl
from desktop_bridge.service import DesktopBridgeService
from devices.transports.serial_cdc import DeviceInfo


ROOT = Path(__file__).resolve().parents[1]
FACTORY_PROFILE = ROOT / "config" / "factory_default_profile.json"


def _dispatch(service: DesktopBridgeService, method: str, params=None):
    events: list[tuple[str, dict]] = []
    result = service.dispatch(
        method,
        params or {},
        "test-request",
        lambda event, data: events.append((event, dict(data))),
    )
    return result, events


def test_hello_and_factory_compile_do_not_require_a_device() -> None:
    service = DesktopBridgeService(FACTORY_PROFILE)

    hello, _ = _dispatch(service, "bridge.hello")
    factory, _ = _dispatch(service, "profile.factory.get")
    compiled, _ = _dispatch(
        service,
        "profile.compile",
        {"profile": factory["profile"]},
    )

    assert hello["connected"] is False
    assert hello["capabilities"]["factorySlot"] == 0
    assert compiled["packageSize"] > 0
    assert compiled["warnings"] == []


def test_port_listing_uses_the_frontend_contract() -> None:
    fake_port = SimpleNamespace(
        device="COM42",
        name="COM42",
        description="KIIIe CDC",
        hwid="USB\\VID_1234&PID_5678",
        vid=0x1234,
        pid=0x5678,
        serial_number="ABC",
        manufacturer="KIIIe",
        product="Control Lab",
        interface="CDC",
        location="1-2",
    )
    service = DesktopBridgeService(
        FACTORY_PROFILE,
        port_provider=lambda: [fake_port],
    )

    result, _ = _dispatch(service, "device.list_ports")

    assert result["count"] == 1
    assert result["ports"][0]["device"] == "COM42"
    assert result["ports"][0]["serialNumber"] == "ABC"


class _FakeClient:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.uploaded: tuple[bytes, int, int] | None = None
        self.activated: list[int] = []
        self.closed = False

    def open(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def ping(self) -> str:
        return "PONG 1"

    def info(self) -> DeviceInfo:
        active = self.activated[-1] if self.activated else 0
        return DeviceInfo(active, 0x1234, 7, (True, False, False))

    def upload(self, package, slot, chunk_size, progress) -> None:
        self.uploaded = (package, slot, chunk_size)
        progress(len(package), len(package))

    def activate(self, slot: int) -> str:
        self.activated.append(slot)
        return f"ACTIVE {slot}"

    def abort(self) -> None:
        return None


class _ActivationFailureClient(_FakeClient):
    def activate(self, slot: int) -> str:
        from devices.transports.serial_cdc import SerialCdcError

        raise SerialCdcError("activation transport failed")


def test_connect_and_install_keep_factory_slot_read_only() -> None:
    clients: list[_FakeClient] = []

    def make_client(**kwargs):
        client = _FakeClient(**kwargs)
        clients.append(client)
        return client

    service = DesktopBridgeService(FACTORY_PROFILE, client_factory=make_client)
    connected, _ = _dispatch(
        service,
        "device.connect",
        {"port": "COM42"},
    )
    profile = json.loads(FACTORY_PROFILE.read_text(encoding="utf-8"))
    installed, events = _dispatch(
        service,
        "profile.install",
        {"profile": profile, "slot": 1, "activate": True},
    )

    assert connected["port"] == "COM42"
    assert connected["info"]["activeSlot"] == 0
    assert clients[0].uploaded is not None
    assert clients[0].uploaded[1:] == (1, 64)
    assert installed["slot"] == 1
    assert installed["info"]["activeSlot"] == 1
    assert events[0][1]["stage"] == "compiling"
    assert events[-1][1]["stage"] == "complete"


def test_install_reports_committed_profile_when_activation_fails() -> None:
    service = DesktopBridgeService(
        FACTORY_PROFILE,
        client_factory=lambda **kwargs: _ActivationFailureClient(**kwargs),
    )
    _dispatch(service, "device.connect", {"port": "COM42"})
    profile = json.loads(FACTORY_PROFILE.read_text(encoding="utf-8"))

    try:
        _dispatch(
            service,
            "profile.install",
            {"profile": profile, "slot": 1, "activate": True},
        )
    except BridgeError as error:
        assert error.code == "E_PROFILE_ACTIVATE_AFTER_COMMIT"
        assert error.details is not None
        assert error.details["committed"] is True
        assert error.details["activated"] is False
    else:  # pragma: no cover - makes the failure explicit in plain pytest
        raise AssertionError("activation failure should cross the bridge")


def test_jsonl_boundary_returns_structured_errors_and_continues() -> None:
    service = DesktopBridgeService(FACTORY_PROFILE)
    stdin = io.StringIO(
        "not-json\n"
        '{"id":"ok-1","method":"bridge.hello","params":{}}\n'
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    assert serve_jsonl(service, stdin, stdout, stderr) == 0
    messages = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert messages[0]["ok"] is False
    assert messages[0]["error"]["code"] == "E_PARSE"
    assert messages[1]["id"] == "ok-1"
    assert messages[1]["ok"] is True
    assert stderr.getvalue() == ""
