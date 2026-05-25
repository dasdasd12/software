"""SQLite migration helper for the future product store."""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    sql: str


class SQLiteMigrationManager:
    """Applies forward-only SQLite migrations."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def apply(self, migrations: Iterable[Migration]) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path) as conn:
            self._ensure_table(conn)
            applied = self._applied_versions(conn)
            for migration in sorted(migrations, key=lambda item: item.version):
                if migration.version in applied:
                    continue
                conn.executescript(migration.sql)
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at, description)
                    VALUES (?, ?, ?)
                    """,
                    (migration.version, int(time.time()), migration.description),
                )
            conn.commit()

    @staticmethod
    def _ensure_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              applied_at INTEGER NOT NULL,
              description TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _applied_versions(conn: sqlite3.Connection) -> set:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {int(row[0]) for row in rows}


APP_STORE_MIGRATIONS = [
    Migration(
        version=1,
        description="create app store tables",
        sql="""
        CREATE TABLE profiles (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          target_device_family TEXT NOT NULL,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE known_devices (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE agent_instance_presets (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE workspace_bindings (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE sessions (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE runs (
          id TEXT PRIMARY KEY,
          session_id TEXT,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE permission_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          permission_id TEXT NOT NULL,
          session_id TEXT,
          run_id TEXT,
          action_type TEXT NOT NULL,
          risk_level TEXT NOT NULL,
          decision TEXT NOT NULL,
          source_client TEXT NOT NULL,
          timestamp INTEGER NOT NULL,
          summary TEXT NOT NULL,
          payload TEXT NOT NULL
        );

        CREATE INDEX idx_permission_history_session_id
          ON permission_history(session_id);

        CREATE INDEX idx_permission_history_run_id
          ON permission_history(run_id);

        CREATE TABLE approval_policies (
          id TEXT PRIMARY KEY,
          payload TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );

        CREATE TABLE ui_preferences (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        );
        """,
    ),
    Migration(
        version=2,
        description="create app settings table",
        sql="""
        CREATE TABLE app_settings (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        );
        """,
    ),
]


def migrate_app_store(database_path: Path) -> None:
    SQLiteMigrationManager(database_path).apply(APP_STORE_MIGRATIONS)
