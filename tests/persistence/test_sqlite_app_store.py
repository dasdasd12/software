import json
import sqlite3
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from keyboard import AppConfig, Profile, ProfileValidationError  # noqa: E402
from keyboard.profile import (  # noqa: E402
    export_app_config_json,
    export_profile_json,
    import_app_config_json,
    import_profile_json,
)
from persistence import SQLiteAppStore, migrate_app_store  # noqa: E402


def test_empty_database_migration_creates_app_store_tables(tmpdir):
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

    assert {
        "schema_migrations",
        "profiles",
        "known_devices",
        "agent_instance_presets",
        "workspace_bindings",
        "sessions",
        "runs",
        "permission_history",
        "approval_policies",
        "ui_preferences",
        "app_settings",
    }.issubset(tables)
    assert versions == [
        (1, "create app store tables"),
        (2, "create app settings table"),
    ]


def test_profile_repository_crud_round_trip(tmpdir):
    store = SQLiteAppStore.open(Path(str(tmpdir)) / "app.db")
    profile = Profile(
        id="profile_dev",
        name="Developer",
        target_device_family="ai_keyboard_ch32h417",
        tags=["coding"],
        keymap={"K_A": "hid.key.a"},
    )

    store.profiles.upsert(profile)
    stored = store.profiles.get("profile_dev")
    assert stored == profile

    profile.name = "Developer Updated"
    store.profiles.upsert(profile)
    assert store.profiles.get("profile_dev").name == "Developer Updated"
    assert [item.id for item in store.profiles.list()] == ["profile_dev"]

    assert store.profiles.delete("profile_dev") is True
    assert store.profiles.get("profile_dev") is None
    assert store.profiles.delete("profile_dev") is False
    store.close()


def test_app_store_repositories_cover_core_entities(tmpdir):
    store = SQLiteAppStore.open(Path(str(tmpdir)) / "app.db")

    store.known_devices.upsert({"id": "kbd_01", "display_name": "Desk Keyboard"})
    store.agent_instance_presets.upsert({"id": "codex_sw", "provider_id": "codex"})
    store.workspace_bindings.upsert({"id": "bind_01", "workspace": "software", "profile_id": "profile_dev"})
    store.sessions.upsert({"id": "sess_01", "provider_id": "codex", "state": "running"})
    store.runs.upsert({"id": "run_01", "session_id": "sess_01", "state": "running"})
    store.approval_policies.upsert({"id": "policy_standard", "mode": "approve_low_risk"})
    store.ui_preferences.set("theme", {"mode": "dark"})

    assert store.known_devices.get("kbd_01")["display_name"] == "Desk Keyboard"
    assert store.agent_instance_presets.get("codex_sw")["provider_id"] == "codex"
    assert store.workspace_bindings.get("bind_01")["workspace"] == "software"
    assert store.sessions.get("sess_01")["state"] == "running"
    assert store.runs.get("run_01")["session_id"] == "sess_01"
    assert store.approval_policies.get("policy_standard")["mode"] == "approve_low_risk"
    assert store.ui_preferences.get("theme") == {"mode": "dark"}
    store.close()


def test_permission_history_persists_append_only_records(tmpdir):
    store = SQLiteAppStore.open(Path(str(tmpdir)) / "app.db")

    first_id = store.permission_history.append({
        "permission_id": "perm_01",
        "session_id": "sess_01",
        "run_id": "run_01",
        "action_type": "agent.file.write",
        "risk_level": "high",
        "decision": "deny",
        "source_client": "desktop",
        "timestamp": 100,
        "summary": "Denied file write",
    })
    second_id = store.permission_history.append({
        "permission_id": "perm_02",
        "session_id": "sess_01",
        "action_type": "agent.command.run",
        "risk_level": "low",
        "decision": "allow",
        "source_client": "keyboard",
        "timestamp": 101,
        "summary": "Allowed command",
    })

    assert second_id > first_id
    assert [item["permission_id"] for item in store.permission_history.list()] == ["perm_01", "perm_02"]
    assert [item["permission_id"] for item in store.permission_history.list(session_id="sess_01")] == [
        "perm_01",
        "perm_02",
    ]
    store.close()


def test_app_config_export_import_round_trip_through_store(tmpdir):
    source = SQLiteAppStore.open(Path(str(tmpdir)) / "source.db")
    target = SQLiteAppStore.open(Path(str(tmpdir)) / "target.db")
    source.profiles.upsert(Profile(
        id="profile_dev",
        name="Developer",
        target_device_family="ai_keyboard_ch32h417",
        tags=["coding"],
    ))
    source.known_devices.upsert({"id": "kbd_01", "display_name": "Desk Keyboard"})
    source.agent_instance_presets.upsert({"id": "codex_sw", "provider_id": "codex"})
    source.workspace_bindings.upsert({"id": "bind_01", "workspace": "software", "profile_id": "profile_dev"})
    source.approval_policies.upsert({"id": "policy_standard", "mode": "approve_low_risk"})
    source.ui_preferences.set("theme", {"mode": "dark"})

    exported = source.export_config_json(active_profile_id="profile_dev")
    target.import_config_json(exported)

    assert json.loads(exported)["active_profile_id"] == "profile_dev"
    assert target.profiles.get("profile_dev").name == "Developer"
    assert target.known_devices.get("kbd_01")["display_name"] == "Desk Keyboard"
    assert target.ui_preferences.get("theme") == {"mode": "dark"}
    source.close()
    target.close()


def test_profile_and_config_json_helpers_reject_unsupported_schema_versions():
    profile = Profile(id="profile_dev", name="Developer", target_device_family="ai_keyboard_ch32h417")
    profile_json = export_profile_json(profile)
    config_json = export_app_config_json(AppConfig(profiles=[profile], active_profile_id="profile_dev"))

    assert import_profile_json(profile_json) == profile
    assert import_app_config_json(config_json).profiles == [profile]

    bad_profile = json.loads(profile_json)
    bad_profile["schema_version"] = "999.0"
    with pytest.raises(ProfileValidationError, match="unsupported schema_version"):
        import_profile_json(json.dumps(bad_profile))

    bad_config = json.loads(config_json)
    bad_config["schema_version"] = "999.0"
    with pytest.raises(ProfileValidationError, match="unsupported schema_version"):
        import_app_config_json(json.dumps(bad_config))
