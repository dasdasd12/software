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
