"""SQLite repositories for the Local Core Service app store."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from keyboard import AppConfig, Profile, app_config_from_dict, export_app_config_json, import_app_config_json
from keyboard import profile_from_dict, validate_profile

from .migrations import migrate_app_store


def _now() -> int:
    return int(time.time())


def _dumps(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _loads(raw: str) -> Dict[str, Any]:
    return json.loads(raw)


class JsonEntityRepository:
    def __init__(self, conn: sqlite3.Connection, table: str):
        self.conn = conn
        self.table = table

    def upsert(self, data: Dict[str, Any]) -> None:
        entity_id = data.get("id")
        if not entity_id:
            raise ValueError(f"{self.table} item id is required")
        current = _now()
        row = self.conn.execute(
            f"SELECT created_at FROM {self.table} WHERE id = ?",
            (entity_id,),
        ).fetchone()
        created_at = int(row["created_at"]) if row else current
        self.conn.execute(
            f"""
            INSERT OR REPLACE INTO {self.table}(id, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (entity_id, _dumps(data), created_at, current),
        )
        self.conn.commit()

    def get(self, entity_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            f"SELECT payload FROM {self.table} WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        return _loads(row["payload"])

    def list(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            f"SELECT payload FROM {self.table} ORDER BY id"
        ).fetchall()
        return [_loads(row["payload"]) for row in rows]

    def delete(self, entity_id: str) -> bool:
        cursor = self.conn.execute(f"DELETE FROM {self.table} WHERE id = ?", (entity_id,))
        self.conn.commit()
        return cursor.rowcount > 0


class ProfileRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert(self, profile: Profile) -> None:
        validate_profile(profile)
        current = _now()
        row = self.conn.execute(
            "SELECT created_at FROM profiles WHERE id = ?",
            (profile.id,),
        ).fetchone()
        created_at = int(row["created_at"]) if row else current
        self.conn.execute(
            """
            INSERT OR REPLACE INTO profiles(id, name, target_device_family, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile.id,
                profile.name,
                profile.target_device_family,
                _dumps(profile.to_dict()),
                created_at,
                current,
            ),
        )
        self.conn.commit()

    def get(self, profile_id: str) -> Optional[Profile]:
        row = self.conn.execute(
            "SELECT payload FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        return profile_from_dict(_loads(row["payload"]))

    def list(self) -> List[Profile]:
        rows = self.conn.execute("SELECT payload FROM profiles ORDER BY id").fetchall()
        return [profile_from_dict(_loads(row["payload"])) for row in rows]

    def delete(self, profile_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self.conn.commit()
        return cursor.rowcount > 0


class PermissionHistoryRepository:
    REQUIRED_FIELDS = (
        "permission_id",
        "action_type",
        "risk_level",
        "decision",
        "source_client",
        "timestamp",
        "summary",
    )

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, data: Dict[str, Any]) -> int:
        missing = [field for field in self.REQUIRED_FIELDS if field not in data]
        if missing:
            raise ValueError(f"permission history missing fields: {', '.join(missing)}")
        cursor = self.conn.execute(
            """
            INSERT INTO permission_history(
              permission_id, session_id, run_id, action_type, risk_level,
              decision, source_client, timestamp, summary, payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["permission_id"],
                data.get("session_id"),
                data.get("run_id"),
                data["action_type"],
                data["risk_level"],
                data["decision"],
                data["source_client"],
                int(data["timestamp"]),
                data["summary"],
                _dumps(data),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list(self, session_id: Optional[str] = None, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT payload FROM permission_history"
        params: List[Any] = []
        filters = []
        if session_id is not None:
            filters.append("session_id = ?")
            params.append(session_id)
        if run_id is not None:
            filters.append("run_id = ?")
            params.append(run_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY id"
        rows = self.conn.execute(query, params).fetchall()
        return [_loads(row["payload"]) for row in rows]


class UIPreferenceRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO ui_preferences(key, value, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True), _now()),
        )
        self.conn.commit()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT value FROM ui_preferences WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["value"])

    def list(self) -> Dict[str, Dict[str, Any]]:
        rows = self.conn.execute("SELECT key, value FROM ui_preferences ORDER BY key").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def delete(self, key: str) -> bool:
        cursor = self.conn.execute("DELETE FROM ui_preferences WHERE key = ?", (key,))
        self.conn.commit()
        return cursor.rowcount > 0


class SQLiteAppStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.profiles = ProfileRepository(conn)
        self.known_devices = JsonEntityRepository(conn, "known_devices")
        self.agent_instance_presets = JsonEntityRepository(conn, "agent_instance_presets")
        self.workspace_bindings = JsonEntityRepository(conn, "workspace_bindings")
        self.sessions = JsonEntityRepository(conn, "sessions")
        self.runs = JsonEntityRepository(conn, "runs")
        self.permission_history = PermissionHistoryRepository(conn)
        self.approval_policies = JsonEntityRepository(conn, "approval_policies")
        self.ui_preferences = UIPreferenceRepository(conn)

    @classmethod
    def open(cls, database_path: Path) -> "SQLiteAppStore":
        migrate_app_store(database_path)
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def export_config_json(
        self,
        active_profile_id: Optional[str] = None,
        global_approval_policy_id: str = "policy_standard",
    ) -> str:
        return export_app_config_json(AppConfig(
            active_profile_id=active_profile_id,
            profiles=self.profiles.list(),
            known_devices=self.known_devices.list(),
            agent_instance_presets=self.agent_instance_presets.list(),
            workspace_bindings=self.workspace_bindings.list(),
            approval_policies=self.approval_policies.list(),
            global_approval_policy_id=global_approval_policy_id,
            ui_preferences=self.ui_preferences.list(),
        ))

    def import_config_json(self, raw: str) -> None:
        config = import_app_config_json(raw)
        self.import_config(config)

    def import_config(self, config: AppConfig) -> None:
        parsed = app_config_from_dict(config.to_dict())
        for profile in parsed.profiles:
            self.profiles.upsert(profile)
        for item in parsed.known_devices:
            self.known_devices.upsert(item)
        for item in parsed.agent_instance_presets:
            self.agent_instance_presets.upsert(item)
        for item in parsed.workspace_bindings:
            self.workspace_bindings.upsert(item)
        for item in parsed.approval_policies:
            self.approval_policies.upsert(item)
        for key, value in parsed.ui_preferences.items():
            self.ui_preferences.set(key, value)
