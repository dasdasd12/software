"""Pure symbolic target resolution for focus-based commands."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional


SYMBOLIC_TARGETS = {
    "focused_agent",
    "focused_session",
    "focused_run",
    "focused_permission",
}


@dataclass(frozen=True)
class TargetResolution:
    selector: str
    resolved: bool
    target: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    code: str = "UNRESOLVED_TARGET"

    @classmethod
    def resolved_target(cls, selector: str, target: Mapping[str, Any]) -> "TargetResolution":
        return cls(selector=selector, resolved=True, target=dict(target))

    @classmethod
    def unresolved(cls, selector: str, reason: str) -> "TargetResolution":
        return cls(selector=selector, resolved=False, reason=reason)


def symbolic_selector(target: Any) -> Optional[str]:
    """Return the symbolic selector from a command target, if one is present."""
    if isinstance(target, str) and target in SYMBOLIC_TARGETS:
        return target
    if isinstance(target, Mapping):
        for key in ("selector", "target", "target_selector"):
            value = target.get(key)
            if isinstance(value, str) and value in SYMBOLIC_TARGETS:
                return value
    return None


class TargetResolver:
    """Resolve symbolic targets from already-available runtime state."""

    def resolve(
        self,
        selector: str,
        *,
        focus: Any,
        instances: Optional[Any] = None,
        sessions: Optional[Any] = None,
        runs: Optional[Any] = None,
        permissions: Optional[Any] = None,
    ) -> TargetResolution:
        if selector == "focused_permission":
            return self._resolve_focused_permission(selector, focus, permissions or ())
        if selector == "focused_run":
            return self._resolve_focused_run(selector, focus, sessions or {}, runs or {})
        if selector == "focused_session":
            return self._resolve_focused_session(selector, focus, sessions or {}, runs or {})
        if selector == "focused_agent":
            return self._resolve_focused_agent(selector, focus, instances or {})
        return TargetResolution.unresolved(selector, f"unsupported target selector: {selector}")

    def _resolve_focused_permission(
        self,
        selector: str,
        focus: Any,
        permissions: Any,
    ) -> TargetResolution:
        pending = sorted(
            [
                permission
                for permission in self._permission_records(permissions)
                if self._is_pending_permission(permission)
                and self._record_id(permission, "permission_id", "request_id")
            ],
            key=self._priority,
            reverse=True,
        )

        for field in ("run_id", "session_id", "instance_id"):
            value = self._focus_value(focus, field)
            if not value:
                continue
            match = self._first(pending, lambda item, field=field, value=value: item.get(field) == value)
            if match:
                return TargetResolution.resolved_target(selector, self._permission_target(match))

        if pending:
            return TargetResolution.resolved_target(selector, self._permission_target(pending[0]))
        return TargetResolution.unresolved(selector, "no pending permission matches the focused target")

    def _resolve_focused_run(
        self,
        selector: str,
        focus: Any,
        sessions: Any,
        runs: Any,
    ) -> TargetResolution:
        sessions_by_id = self._records_by_id(sessions, "session_id")
        runs_by_id = self._records_by_id(runs, "run_id")

        run_id = self._focus_value(focus, "run_id")
        if run_id and run_id in runs_by_id:
            return TargetResolution.resolved_target(selector, self._run_target(runs_by_id[run_id]))

        session_id = self._focus_value(focus, "session_id")
        session = sessions_by_id.get(session_id or "")
        if session:
            active_run_id = session.get("active_run_id") or session.get("run_id")
            if active_run_id and active_run_id in runs_by_id:
                return TargetResolution.resolved_target(selector, self._run_target(runs_by_id[active_run_id]))
            matching = [
                run
                for run in runs_by_id.values()
                if run.get("session_id") == session_id
            ]
            if matching:
                return TargetResolution.resolved_target(selector, self._run_target(matching[0]))

        return TargetResolution.unresolved(selector, "focused run does not exist")

    def _resolve_focused_session(
        self,
        selector: str,
        focus: Any,
        sessions: Any,
        runs: Any,
    ) -> TargetResolution:
        sessions_by_id = self._records_by_id(sessions, "session_id")
        runs_by_id = self._records_by_id(runs, "run_id")

        session_id = self._focus_value(focus, "session_id")
        if session_id and session_id in sessions_by_id:
            return TargetResolution.resolved_target(selector, self._session_target(sessions_by_id[session_id]))

        run_id = self._focus_value(focus, "run_id")
        run = runs_by_id.get(run_id or "")
        if run:
            run_session_id = run.get("session_id")
            if run_session_id and run_session_id in sessions_by_id:
                return TargetResolution.resolved_target(
                    selector,
                    self._session_target(sessions_by_id[run_session_id]),
                )

        return TargetResolution.unresolved(selector, "focused session does not exist")

    def _resolve_focused_agent(
        self,
        selector: str,
        focus: Any,
        instances: Any,
    ) -> TargetResolution:
        instance_id = self._focus_value(focus, "instance_id")
        if not instance_id:
            return TargetResolution.unresolved(selector, "focused agent does not exist")

        instances_by_id = self._records_by_id(instances, "instance_id")
        if instances_by_id and instance_id not in instances_by_id:
            return TargetResolution.unresolved(selector, "focused agent does not exist")
        instance = instances_by_id.get(instance_id, {"instance_id": instance_id})
        return TargetResolution.resolved_target(selector, self._agent_target(instance))

    @staticmethod
    def _focus_value(focus: Any, field: str) -> Optional[str]:
        value = getattr(focus, field, None)
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _first(records: Iterable[Dict[str, Any]], predicate) -> Optional[Dict[str, Any]]:
        for record in records:
            if predicate(record):
                return record
        return None

    @staticmethod
    def _priority(record: Mapping[str, Any]) -> int:
        try:
            return int(record.get("priority", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _is_pending_permission(record: Mapping[str, Any]) -> bool:
        status = str(record.get("status", "pending")).lower()
        return status in {"pending", "waiting", "waiting_permission"}

    def _records_by_id(self, source: Any, id_field: str) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        if isinstance(source, Mapping):
            iterator = source.items()
            for key, value in iterator:
                record = self._record(value)
                record.setdefault(id_field, str(key))
                record_id = self._record_id(record, id_field)
                if record_id:
                    records[record_id] = record
            return records

        for value in self._records(source):
            record_id = self._record_id(value, id_field)
            if record_id:
                records[record_id] = value
        return records

    def _records(self, source: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(source, Mapping):
            for value in source.values():
                yield self._record(value)
            return
        if source is None:
            return
        for value in source:
            yield self._record(value)

    def _permission_records(self, source: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(source, Mapping):
            for key, value in source.items():
                record = self._record(value)
                record.setdefault("permission_id", str(key))
                yield record
            return
        yield from self._records(source)

    @staticmethod
    def _record(value: Any) -> Dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return dict(to_dict())
        result: Dict[str, Any] = {}
        for field in (
            "provider_id",
            "instance_id",
            "session_id",
            "run_id",
            "active_run_id",
            "permission_id",
            "request_id",
            "priority",
            "status",
            "agent",
        ):
            attr = getattr(value, field, None)
            if attr is not None:
                result[field] = attr
        return result

    @staticmethod
    def _record_id(record: Mapping[str, Any], *fields: str) -> Optional[str]:
        for field in fields:
            value = record.get(field)
            if isinstance(value, str) and value:
                return value
        return None

    def _permission_target(self, permission: Mapping[str, Any]) -> Dict[str, Any]:
        permission_id = self._record_id(permission, "permission_id", "request_id")
        target: Dict[str, Any] = {"permission_id": permission_id}
        for field in ("provider_id", "agent", "instance_id", "session_id", "run_id"):
            value = permission.get(field)
            if isinstance(value, str) and value:
                target[field] = value
        request_id = permission.get("request_id")
        if isinstance(request_id, str) and request_id and request_id != permission_id:
            target["request_id"] = request_id
        return target

    @staticmethod
    def _run_target(run: Mapping[str, Any]) -> Dict[str, Any]:
        target = {
            "run_id": run.get("run_id"),
            "session_id": run.get("session_id"),
            "instance_id": run.get("instance_id"),
            "provider_id": run.get("provider_id"),
        }
        return {key: value for key, value in target.items() if isinstance(value, str) and value}

    @staticmethod
    def _session_target(session: Mapping[str, Any]) -> Dict[str, Any]:
        target = {
            "session_id": session.get("session_id"),
            "instance_id": session.get("instance_id"),
            "provider_id": session.get("provider_id"),
            "agent": session.get("agent"),
        }
        return {key: value for key, value in target.items() if isinstance(value, str) and value}

    @staticmethod
    def _agent_target(instance: Mapping[str, Any]) -> Dict[str, Any]:
        target = {
            "instance_id": instance.get("instance_id"),
            "provider_id": instance.get("provider_id"),
            "agent": instance.get("agent"),
        }
        return {key: value for key, value in target.items() if isinstance(value, str) and value}
