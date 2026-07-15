"""Synchronous keyboard configuration service used by the JSONL bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Iterable, Mapping

from devices.transports.serial_cdc import (
    AkpkSerialClient,
    DeviceError,
    DeviceInfo,
    SerialCdcError,
)
from keyboard.akpk import PackageResult, ProfileCompileError, build_package

from . import BRIDGE_VERSION, PROTOCOL_VERSION
from .protocol import BridgeError, EventEmitter


ClientFactory = Callable[..., AkpkSerialClient]
PackageBuilder = Callable[[dict[str, Any]], PackageResult]
PortProvider = Callable[[], Iterable[Any]]


class DesktopBridgeService:
    """Own the optional serial connection and expose desktop-safe commands.

    Constructing this service does not import pyserial or touch hardware.
    Pyserial is loaded only by ``device.list_ports`` or ``device.connect``.
    """

    METHOD_NAMES = (
        "bridge.hello",
        "device.list_ports",
        "device.connect",
        "device.info",
        "device.disconnect",
        "profile.factory.get",
        "profile.compile",
        "profile.install",
        "device.activate",
    )

    def __init__(
        self,
        factory_profile_path: Path,
        *,
        client_factory: ClientFactory = AkpkSerialClient,
        package_builder: PackageBuilder = build_package,
        port_provider: PortProvider | None = None,
    ) -> None:
        self._factory_profile_path = Path(factory_profile_path).resolve()
        self._client_factory = client_factory
        self._package_builder = package_builder
        self._port_provider = port_provider
        self._client: AkpkSerialClient | None = None
        self._port: str | None = None

    def dispatch(
        self,
        method: str,
        params: Mapping[str, Any],
        request_id: str | int,
        emit_event: EventEmitter,
    ) -> Any:
        del request_id  # The transport owns correlation; methods need events only.
        handlers: dict[str, Callable[[Mapping[str, Any], EventEmitter], Any]] = {
            "bridge.hello": self._hello,
            "device.list_ports": self._list_ports,
            "device.connect": self._connect,
            "device.info": self._info,
            "device.disconnect": self._disconnect,
            "profile.factory.get": self._factory_get,
            "profile.compile": self._compile,
            "profile.install": self._install,
            "device.activate": self._activate,
        }
        handler = handlers.get(method)
        if handler is None:
            raise BridgeError(
                "E_METHOD_NOT_FOUND",
                f"Unknown bridge method: {method}",
                details={"method": method},
            )
        try:
            return handler(params, emit_event)
        except BridgeError:
            raise
        except ProfileCompileError as exc:
            raise BridgeError(
                "E_PROFILE_COMPILE",
                str(exc),
                details={"errorType": type(exc).__name__},
            ) from exc
        except DeviceError as exc:
            raise BridgeError(
                "E_DEVICE_RESPONSE",
                "The keyboard rejected the command",
                details={
                    "deviceCode": exc.code,
                    "detail": exc.detail,
                    "command": exc.command,
                },
            ) from exc
        except SerialCdcError as exc:
            raise BridgeError(
                "E_DEVICE_IO",
                str(exc),
                details={"port": self._port},
            ) from exc
        except (ImportError, OSError) as exc:
            raise BridgeError(
                "E_SERIAL_UNAVAILABLE",
                str(exc),
                details={"port": self._port, "errorType": type(exc).__name__},
            ) from exc

    def close(self) -> None:
        client = self._client
        self._client = None
        self._port = None
        if client is not None:
            try:
                client.close()
            except Exception:
                # EOF/process shutdown must not emit a second protocol response.
                pass

    # -- bridge -------------------------------------------------------

    def _hello(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del params, emit_event
        return {
            "bridgeVersion": BRIDGE_VERSION,
            "protocolVersion": PROTOCOL_VERSION,
            "methods": list(self.METHOD_NAMES),
            "connected": self._client is not None,
            "port": self._port,
            "capabilities": {
                "profileSlots": 3,
                "factorySlot": 0,
                "profileSourceReadback": False,
                "profileCompile": True,
                "installProgressEvents": True,
            },
        }

    # -- device -------------------------------------------------------

    def _list_ports(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del params, emit_event
        provider = self._port_provider
        if provider is None:
            try:
                from serial.tools import list_ports  # type: ignore[import-untyped]
            except ImportError as exc:
                raise BridgeError(
                    "E_SERIAL_UNAVAILABLE",
                    "pyserial is required to enumerate serial ports",
                    details={"dependency": "pyserial"},
                ) from exc
            provider = list_ports.comports

        ports = sorted(
            (_port_payload(port) for port in provider()),
            key=lambda item: item["device"],
        )
        return {"ports": ports, "count": len(ports)}

    def _connect(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del emit_event
        port = _required_string(params, "port")
        baudrate = _optional_int(params, "baudrate", 115200, minimum=1)
        timeout = _optional_number(params, "timeout", 3.0, minimum=0.05)

        # A failed replacement connection must not leave a stale client around.
        self.close()
        client = self._client_factory(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
        )
        try:
            client.open()
            ping = client.ping()
            info = client.info()
        except Exception:
            try:
                client.close()
            finally:
                self._client = None
                self._port = None
            raise

        self._client = client
        self._port = port
        return {"port": port, "ping": ping, "info": _device_info_payload(info)}

    def _info(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del params, emit_event
        client = self._require_client()
        return {"port": self._port, "info": _device_info_payload(client.info())}

    def _disconnect(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del params, emit_event
        was_connected = self._client is not None
        port = self._port
        self.close()
        return {"disconnected": was_connected, "port": port}

    def _activate(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del emit_event
        client = self._require_client()
        slot = _required_int(params, "slot", minimum=0, maximum=3)
        reply = client.activate(slot)
        info = client.info()
        return {
            "slot": slot,
            "reply": reply,
            "info": _device_info_payload(info),
        }

    # -- profiles -----------------------------------------------------

    def _factory_get(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del params, emit_event
        profile = self._read_factory_profile()
        return {
            "profile": profile,
            "source": "factory",
            "identity": profile.get("identity", {}),
        }

    def _compile(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        del emit_event
        profile = _required_profile(params)
        result = self._package_builder(profile)
        return _package_payload(result)

    def _install(
        self, params: Mapping[str, Any], emit_event: EventEmitter
    ) -> dict[str, Any]:
        client = self._require_client()
        profile = _required_profile(params)
        slot = _required_int(params, "slot", minimum=1, maximum=3)
        chunk_size = _optional_int(
            params, "chunkSize", 64, minimum=1, maximum=128
        )
        activate = _optional_bool(params, "activate", True)

        emit_event(
            "profile.install.progress",
            {"stage": "compiling", "slot": slot, "percent": 0},
        )
        result = self._package_builder(profile)
        total = len(result.package)
        emit_event(
            "profile.install.progress",
            {
                "stage": "uploading",
                "slot": slot,
                "bytesDone": 0,
                "bytesTotal": total,
                "percent": 0,
            },
        )

        def progress(done: int, package_total: int) -> None:
            percent = 100 if package_total == 0 else (done * 100) // package_total
            emit_event(
                "profile.install.progress",
                {
                    "stage": "uploading",
                    "slot": slot,
                    "bytesDone": done,
                    "bytesTotal": package_total,
                    "percent": percent,
                },
            )

        try:
            client.upload(result.package, slot, chunk_size, progress)
        except Exception:
            try:
                client.abort()
            except Exception:
                pass
            raise

        emit_event(
            "profile.install.progress",
            {"stage": "committed", "slot": slot, "percent": 100},
        )
        activation_reply: str | None = None
        if activate:
            emit_event(
                "profile.install.progress",
                {"stage": "activating", "slot": slot, "percent": 100},
            )
            try:
                activation_reply = client.activate(slot)
            except (SerialCdcError, ImportError, OSError) as exc:
                details: dict[str, Any] = {
                    "stage": "activating",
                    "slot": slot,
                    "committed": True,
                    "activated": False,
                    "errorType": type(exc).__name__,
                }
                if isinstance(exc, DeviceError):
                    details.update(
                        {
                            "deviceCode": exc.code,
                            "detail": exc.detail,
                            "command": exc.command,
                        }
                    )
                raise BridgeError(
                    "E_PROFILE_ACTIVATE_AFTER_COMMIT",
                    f"Profile was written to slot {slot}, but activation failed",
                    details=details,
                ) from exc

        try:
            info = client.info()
        except (SerialCdcError, ImportError, OSError) as exc:
            raise BridgeError(
                "E_PROFILE_VERIFY_AFTER_COMMIT",
                f"Profile was written to slot {slot}, but device state could not be verified",
                details={
                    "stage": "verifying",
                    "slot": slot,
                    "committed": True,
                    "activated": activate,
                    "errorType": type(exc).__name__,
                },
            ) from exc
        emit_event(
            "profile.install.progress",
            {
                "stage": "complete",
                "slot": slot,
                "percent": 100,
                "activated": activate,
            },
        )
        return {
            "slot": slot,
            "activated": activate,
            "activationReply": activation_reply,
            "package": _package_payload(result),
            "info": _device_info_payload(info),
        }

    def _read_factory_profile(self) -> dict[str, Any]:
        try:
            raw = self._factory_profile_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BridgeError(
                "E_FACTORY_PROFILE_UNAVAILABLE",
                "Factory profile could not be read",
                recoverable=False,
                details={"path": str(self._factory_profile_path)},
            ) from exc
        try:
            profile = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeError(
                "E_FACTORY_PROFILE_INVALID",
                "Factory profile is not valid JSON",
                recoverable=False,
                details={
                    "path": str(self._factory_profile_path),
                    "line": exc.lineno,
                    "column": exc.colno,
                },
            ) from exc
        if not isinstance(profile, dict):
            raise BridgeError(
                "E_FACTORY_PROFILE_INVALID",
                "Factory profile root must be a JSON object",
                recoverable=False,
                details={"path": str(self._factory_profile_path)},
            )
        return profile

    def _require_client(self) -> AkpkSerialClient:
        if self._client is None:
            raise BridgeError(
                "E_DEVICE_NOT_CONNECTED",
                "Connect to a keyboard before using this method",
            )
        return self._client


def default_factory_profile_path() -> Path:
    """Find the source-tree default while allowing packaged overrides."""

    env_path = os.environ.get("KIIIE_FACTORY_PROFILE")
    if env_path:
        return Path(env_path)

    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "config" / "factory_default_profile.json")
    candidates.extend(
        [
            Path.cwd() / "config" / "factory_default_profile.json",
            Path(__file__).resolve().parents[2]
            / "config"
            / "factory_default_profile.json",
            Path(sys.executable).resolve().parent
            / "resources"
            / "config"
            / "factory_default_profile.json",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _required_profile(params: Mapping[str, Any]) -> dict[str, Any]:
    profile = params.get("profile")
    if not isinstance(profile, dict):
        raise BridgeError(
            "E_INVALID_PARAMS", "params.profile must be a JSON object"
        )
    return dict(profile)


def _required_string(params: Mapping[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(
            "E_INVALID_PARAMS", f"params.{name} must be a non-empty string"
        )
    return value.strip()


def _required_int(
    params: Mapping[str, Any],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeError("E_INVALID_PARAMS", f"params.{name} must be an integer")
    if not minimum <= value <= maximum:
        raise BridgeError(
            "E_INVALID_PARAMS",
            f"params.{name} must be between {minimum} and {maximum}",
        )
    return value


def _optional_int(
    params: Mapping[str, Any],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BridgeError("E_INVALID_PARAMS", f"params.{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f"..{maximum}" if maximum is not None else f" or greater"
        raise BridgeError(
            "E_INVALID_PARAMS",
            f"params.{name} must be {minimum}{suffix}",
        )
    return value


def _optional_number(
    params: Mapping[str, Any], name: str, default: float, *, minimum: float
) -> float:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError("E_INVALID_PARAMS", f"params.{name} must be a number")
    numeric = float(value)
    if numeric < minimum:
        raise BridgeError(
            "E_INVALID_PARAMS", f"params.{name} must be at least {minimum}"
        )
    return numeric


def _optional_bool(
    params: Mapping[str, Any], name: str, default: bool
) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise BridgeError("E_INVALID_PARAMS", f"params.{name} must be boolean")
    return value


def _device_info_payload(info: DeviceInfo) -> dict[str, Any]:
    return {
        "activeSlot": info.active_slot,
        "profileId16": info.profile_id16,
        "profileId16Hex": f"0x{info.profile_id16:04x}",
        "generation": info.generation,
        "slotValid": list(info.slot_valid),
    }


def _package_payload(result: PackageResult) -> dict[str, Any]:
    return {
        "profileId": result.profile_id,
        "profileId16": result.profile_id16,
        "profileId16Hex": f"0x{result.profile_id16:04x}",
        "revision": result.revision,
        "packageSize": len(result.package),
        "runtimeTableSize": len(result.runtime_table),
        "sourceSize": len(result.canonical_source),
        "sourceSha256": result.source_hash.hex(),
        "warnings": list(result.warnings),
        "manifest": dict(result.manifest),
    }


def _port_payload(port: Any) -> dict[str, Any]:
    return {
        "device": str(getattr(port, "device", "")),
        "name": getattr(port, "name", None),
        "description": getattr(port, "description", None),
        "hwid": getattr(port, "hwid", None),
        "vid": getattr(port, "vid", None),
        "pid": getattr(port, "pid", None),
        "serialNumber": getattr(port, "serial_number", None),
        "manufacturer": getattr(port, "manufacturer", None),
        "product": getattr(port, "product", None),
        "interface": getattr(port, "interface", None),
        "location": getattr(port, "location", None),
    }
