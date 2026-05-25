import json
import sqlite3
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from keyboard import Profile, ProfileValidationError  # noqa: E402
from persistence import SQLiteAppStore, migrate_app_store  # noqa: E402
from persistence.import_export import (  # noqa: E402
    ImportConflictPolicy,
    export_store_config_json,
    import_store_config_json,
)


def _profile(profile_id="profile_dev", name="Developer"):
    return Profile(
        id=profile_id,
        name=name,
        target_device_family="ai_keyboard_ch32h417",
        tags=["coding"],
        keymap={"K_A": "hid.key.a"},
    )


def test_empty_database_migration_creates_app_settings_storage(tmpdir):
    db_path = Path(str(tmpdir)) / "app.db"

    migrate_app_store(db_path)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        versions = conn.execute(
            "SELECT version, description FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert "app_settings" in tables
    assert versions == [
        (1, "create app store tables"),
        (2, "create app settings table"),
    ]


def test_active_profile_and_app_settings_persist_across_reopen(tmpdir):
    db_path = Path(str(tmpdir)) / "app.db"
    store = SQLiteAppStore.open(db_path)
    store.profiles.upsert(_profile())

    store.settings.set_active_profile_id("profile_dev")
    store.settings.set_active_tool_for_device("kbd_01", "tool_terminal")
    store.settings.set_global_flag("feature.virtual_input", True)
    store.settings.set_global_flag("theme", "dark")
    store.settings.set_global_flag("device.config", {"poll_ms": 8, "layers": ["base"]})
    with pytest.raises(ValueError, match="unknown profile"):
        store.settings.set_active_profile_id("missing_profile")
    assert store.settings.get_active_profile_id() == "profile_dev"
    store.close()

    reopened = SQLiteAppStore.open(db_path)
    assert reopened.settings.get_active_profile_id() == "profile_dev"
    assert reopened.settings.get_active_tool_for_device("kbd_01") == "tool_terminal"
    assert reopened.settings.get_global_flag("feature.virtual_input") is True
    assert reopened.settings.get_global_flag("theme") == "dark"
    assert reopened.settings.get_global_flag("device.config") == {
        "poll_ms": 8,
        "layers": ["base"],
    }
    reopened.close()


def test_export_includes_persisted_active_profile_by_default(tmpdir):
    store = SQLiteAppStore.open(Path(str(tmpdir)) / "source.db")
    store.profiles.upsert(_profile())
    store.settings.set_active_profile_id("profile_dev")

    exported = export_store_config_json(store)

    payload = json.loads(exported)
    assert payload["active_profile_id"] == "profile_dev"
    store.close()


def test_import_export_round_trips_app_settings(tmpdir):
    source = SQLiteAppStore.open(Path(str(tmpdir)) / "source.db")
    source.profiles.upsert(_profile())
    source.settings.set_active_profile_id("profile_dev")
    source.settings.set_active_tool_for_device("kbd_01", "tool_terminal")
    source.settings.set_global_flag("feature.virtual_input", True)
    source.settings.set_global_flag("device.config", {"poll_ms": 8})

    exported = export_store_config_json(source)
    target = SQLiteAppStore.open(Path(str(tmpdir)) / "target.db")
    import_store_config_json(target, exported)

    assert target.settings.get_active_profile_id() == "profile_dev"
    assert target.settings.get_active_tool_for_device("kbd_01") == "tool_terminal"
    assert target.settings.get_global_flag("feature.virtual_input") is True
    assert target.settings.get_global_flag("device.config") == {"poll_ms": 8}
    source.close()
    target.close()


def test_import_conflict_does_not_overwrite_profile_by_default(tmpdir):
    source = SQLiteAppStore.open(Path(str(tmpdir)) / "source.db")
    source.profiles.upsert(_profile(name="Imported Developer"))
    exported = export_store_config_json(source)

    target = SQLiteAppStore.open(Path(str(tmpdir)) / "target.db")
    target.profiles.upsert(_profile(name="Existing Developer"))

    result = import_store_config_json(target, exported)

    assert result.imported_profile_ids == []
    assert [conflict.profile_id for conflict in result.conflicts] == ["profile_dev"]
    assert target.profiles.get("profile_dev").name == "Existing Developer"
    source.close()
    target.close()


def test_import_replace_policy_overwrites_existing_profile(tmpdir):
    source = SQLiteAppStore.open(Path(str(tmpdir)) / "source.db")
    source.profiles.upsert(_profile(name="Imported Developer"))
    exported = export_store_config_json(source)

    target = SQLiteAppStore.open(Path(str(tmpdir)) / "target.db")
    target.profiles.upsert(_profile(name="Existing Developer"))

    result = import_store_config_json(
        target,
        exported,
        conflict_policy=ImportConflictPolicy.REPLACE,
    )

    assert result.conflicts == []
    assert result.imported_profile_ids == ["profile_dev"]
    assert target.profiles.get("profile_dev").name == "Imported Developer"
    source.close()
    target.close()


def test_import_rename_on_conflict_creates_new_identifiable_profile(tmpdir):
    source = SQLiteAppStore.open(Path(str(tmpdir)) / "source.db")
    source.profiles.upsert(_profile(name="Imported Developer"))
    exported = export_store_config_json(source)

    target = SQLiteAppStore.open(Path(str(tmpdir)) / "target.db")
    target.profiles.upsert(_profile(name="Existing Developer"))

    result = import_store_config_json(
        target,
        exported,
        conflict_policy=ImportConflictPolicy.RENAME_ON_CONFLICT,
    )

    assert result.conflicts == []
    assert len(result.imported_profile_ids) == 1
    renamed_id = result.imported_profile_ids[0]
    assert renamed_id != "profile_dev"
    renamed = target.profiles.get(renamed_id)
    assert renamed.name.startswith("Imported Developer")
    assert renamed.keymap == {"K_A": "hid.key.a"}
    assert target.profiles.get("profile_dev").name == "Existing Developer"
    source.close()
    target.close()


def test_import_rejects_unsupported_schema_version(tmpdir):
    store = SQLiteAppStore.open(Path(str(tmpdir)) / "app.db")
    payload = json.loads(export_store_config_json(store))
    payload["schema_version"] = "999.0"

    with pytest.raises(ProfileValidationError, match="unsupported schema_version"):
        import_store_config_json(store, json.dumps(payload))
    store.close()
