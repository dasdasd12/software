"""Health check scaffold for local runtime diagnostics."""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional


class HealthStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    status: HealthStatus
    message: str = ""
    details: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": dict(self.details),
        }


class HealthReporter:
    def __init__(self) -> None:
        self._checks: Dict[str, HealthCheck] = {}

    def record(self, check: HealthCheck) -> None:
        self._checks[check.name] = check

    def record_local_api(
        self,
        is_running: bool,
        clients: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        check_details = {
            "is_running": bool(is_running),
            "clients": clients,
        }
        check_details.update(details or {})
        self.record(HealthCheck(
            name="local_api",
            status=HealthStatus.OK if is_running else HealthStatus.ERROR,
            message="Local API is running" if is_running else "Local API is not running",
            details=check_details,
        ))

    def record_database(
        self,
        is_connected: bool,
        path: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        check_details = {
            "is_connected": bool(is_connected),
            "path": path,
        }
        check_details.update(details or {})
        self.record(HealthCheck(
            name="database",
            status=HealthStatus.OK if is_connected else HealthStatus.ERROR,
            message="Database is connected" if is_connected else "Database is not connected",
            details=check_details,
        ))

    def record_device_transport(
        self,
        status: Any,
        capabilities: Optional[Any] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        is_open = bool(getattr(status, "is_open", False))
        check_details = {
            "device_id": getattr(status, "device_id", None),
            "transport_kind": getattr(status, "transport_kind", None),
            "is_open": is_open,
            "queued_frames": getattr(status, "queued_frames", None),
        }
        if capabilities is not None:
            check_details.update({
                "protocol_version": getattr(capabilities, "protocol_version", None),
                "max_payload_size": getattr(capabilities, "max_payload_size", None),
                "supports_config_sync": getattr(capabilities, "supports_config_sync", None),
                "supports_agent_slots": getattr(capabilities, "supports_agent_slots", None),
            })
        check_details.update(details or {})
        self.record(HealthCheck(
            name="device_transport",
            status=HealthStatus.OK if is_open else HealthStatus.WARNING,
            message="Device transport is open" if is_open else "Device transport is closed",
            details=check_details,
        ))

    def record_profile_validation(
        self,
        profile_id: Optional[str],
        validation: Dict[str, Any],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        valid = bool(validation.get("valid"))
        issues = list(validation.get("issues") or [])
        check_details = {
            "profile_id": profile_id,
            "valid": valid,
            "issues": issues,
        }
        check_details.update(details or {})
        self.record(HealthCheck(
            name="profile_validation",
            status=HealthStatus.OK if valid else HealthStatus.WARNING,
            message="Profile is valid" if valid else "Profile validation reported issues",
            details=check_details,
        ))

    def record_config_sync(
        self,
        active_profile_id: Optional[str] = None,
        active_synced_profile_id: Optional[str] = None,
        pending_changes: Optional[bool] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        if pending_changes is None:
            pending_changes = active_profile_id != active_synced_profile_id
        check_details = {
            "active_profile_id": active_profile_id,
            "active_synced_profile_id": active_synced_profile_id,
            "pending_changes": bool(pending_changes),
        }
        check_details.update(details or {})
        self.record(HealthCheck(
            name="config_sync",
            status=HealthStatus.WARNING if pending_changes else HealthStatus.OK,
            message="Config sync has pending changes" if pending_changes else "Config sync is current",
            details=check_details,
        ))

    def summarize(self) -> Dict[str, object]:
        checks: List[HealthCheck] = list(self._checks.values())
        if any(check.status == HealthStatus.ERROR for check in checks):
            status = HealthStatus.ERROR
        elif any(check.status == HealthStatus.WARNING for check in checks):
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.OK
        return {
            "status": status.value,
            "checks": [check.to_dict() for check in checks],
        }

    def export(self) -> Dict[str, object]:
        return _redact_sensitive(self.summarize())


SENSITIVE_DETAIL_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}
SENSITIVE_KEY_SUFFIXES = {
    "credential",
    "credentials",
    "header",
    "raw",
    "string",
    "value",
}
SENSITIVE_TOKEN_GROUPS = (
    ("api", "key"),
    ("access", "token"),
    ("refresh", "token"),
    ("session", "token"),
    ("id", "token"),
    ("bearer", "token"),
)

_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive(item) for item in value)
    return value


def _is_sensitive_key(key: Any) -> bool:
    raw_key = str(key)
    camel_split = _CAMEL_CASE_BOUNDARY.sub("_", raw_key)
    tokens = [token for token in _NON_ALNUM.split(camel_split.lower()) if token]
    normalized = "_".join(tokens)
    compact = "".join(tokens)

    if normalized in SENSITIVE_DETAIL_KEYS or compact in SENSITIVE_DETAIL_KEYS:
        return True
    if "authorization" in tokens:
        return True
    if "secret" in tokens or compact.endswith("secret"):
        return True
    if compact.endswith("apikey"):
        return True
    if _has_sensitive_group_with_suffix(tokens):
        return True
    if len(tokens) >= 2 and tokens[-2:] == ["api", "key"]:
        return True
    if tokens and tokens[-1] == "token":
        return True
    return False


def _has_sensitive_group_with_suffix(tokens: List[str]) -> bool:
    for group in SENSITIVE_TOKEN_GROUPS:
        group_len = len(group)
        for index in range(0, len(tokens) - group_len + 1):
            if tuple(tokens[index:index + group_len]) != group:
                continue
            suffix = tokens[index + group_len:]
            if suffix and all(token in SENSITIVE_KEY_SUFFIXES for token in suffix):
                return True
    if tokens[:1] == ["token"] and all(token in SENSITIVE_KEY_SUFFIXES for token in tokens[1:]):
        return len(tokens) > 1
    return False
