"""Health check scaffold for local runtime diagnostics."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


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
