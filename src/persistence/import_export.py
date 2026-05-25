"""Conflict-aware JSON import/export helpers for the SQLite app store."""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Union

from keyboard import (
    AppConfig,
    Profile,
    app_config_from_dict,
    import_app_config_json,
    profile_from_dict,
)


class ImportConflictPolicy(str, Enum):
    SKIP = "skip"
    REPLACE = "replace"
    RENAME_ON_CONFLICT = "rename_on_conflict"


@dataclass(frozen=True)
class ProfileImportConflict:
    profile_id: str
    existing_name: str = ""
    incoming_name: str = ""
    reason: str = "profile_id_exists"
    entity_type: str = "profiles"
    entity_id: str = ""

    def __post_init__(self):
        if not self.entity_id:
            object.__setattr__(self, "entity_id", self.profile_id)


@dataclass(frozen=True)
class ConfigImportResult:
    imported_profile_ids: List[str] = field(default_factory=list)
    conflicts: List[ProfileImportConflict] = field(default_factory=list)
    renamed_profile_ids: Dict[str, str] = field(default_factory=dict)


def export_store_config_json(store) -> str:
    payload = json.loads(store.export_config_json())
    payload["app_settings"] = {
        "active_tool_by_device": store.settings.list_active_tools_by_device(),
        "global_flags": store.settings.list_global_flags(),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def import_store_config_json(
    store,
    raw: str,
    conflict_policy: Union[ImportConflictPolicy, str] = ImportConflictPolicy.SKIP,
) -> ConfigImportResult:
    policy = ImportConflictPolicy(conflict_policy)
    config = import_app_config_json(raw)
    payload = json.loads(raw)
    parsed = app_config_from_dict(config.to_dict())

    imported_profile_ids: List[str] = []
    renamed_profile_ids: Dict[str, str] = {}
    conflicts: List[ProfileImportConflict] = []

    with store.transaction():
        for profile in parsed.profiles:
            existing = store.profiles.get(profile.id)
            if existing is not None and policy == ImportConflictPolicy.SKIP:
                conflicts.append(ProfileImportConflict(
                    profile_id=profile.id,
                    existing_name=existing.name,
                    incoming_name=profile.name,
                ))
                continue
            if existing is not None and policy == ImportConflictPolicy.RENAME_ON_CONFLICT:
                original_id = profile.id
                profile = _renamed_profile(store, profile)
                renamed_profile_ids[original_id] = profile.id
            store.profiles.upsert(profile)
            imported_profile_ids.append(profile.id)

        _import_id_based_entities(
            store.known_devices,
            "known_devices",
            parsed.known_devices,
            policy,
            conflicts,
        )
        _import_id_based_entities(
            store.agent_instance_presets,
            "agent_instance_presets",
            parsed.agent_instance_presets,
            policy,
            conflicts,
        )
        _import_id_based_entities(
            store.workspace_bindings,
            "workspace_bindings",
            parsed.workspace_bindings,
            policy,
            conflicts,
        )
        _import_id_based_entities(
            store.approval_policies,
            "approval_policies",
            parsed.approval_policies,
            policy,
            conflicts,
        )
        for key, value in parsed.ui_preferences.items():
            store.ui_preferences.set(key, value)

        _import_settings(store, payload, parsed, renamed_profile_ids)
    return ConfigImportResult(
        imported_profile_ids=imported_profile_ids,
        conflicts=conflicts,
        renamed_profile_ids=renamed_profile_ids,
    )


def _renamed_profile(store, profile: Profile) -> Profile:
    base_id = f"{profile.id}_imported"
    candidate = base_id
    suffix = 2
    while store.profiles.get(candidate) is not None:
        candidate = f"{base_id}_{suffix}"
        suffix += 1

    data = profile.to_dict()
    data["id"] = candidate
    data["name"] = f"{profile.name} (imported)"
    return profile_from_dict(data)


def _import_id_based_entities(
    repository,
    entity_type: str,
    items: List[dict],
    policy: ImportConflictPolicy,
    conflicts: List[ProfileImportConflict],
) -> None:
    for item in items:
        entity_id = item.get("id")
        existing = repository.get(str(entity_id)) if entity_id else None
        if existing is not None and policy != ImportConflictPolicy.REPLACE:
            conflicts.append(_entity_conflict(entity_type, item, existing))
            continue
        repository.upsert(item)


def _entity_conflict(entity_type: str, incoming: dict, existing: dict) -> ProfileImportConflict:
    entity_id = str(incoming.get("id", ""))
    return ProfileImportConflict(
        profile_id=entity_id,
        existing_name=str(existing.get("name") or existing.get("display_name") or ""),
        incoming_name=str(incoming.get("name") or incoming.get("display_name") or ""),
        reason=f"{entity_type}_id_exists",
        entity_type=entity_type,
        entity_id=entity_id,
    )


def _import_settings(
    store,
    payload: dict,
    config: AppConfig,
    renamed_profile_ids: Dict[str, str],
) -> None:
    active_profile_id = config.active_profile_id
    if active_profile_id in renamed_profile_ids:
        active_profile_id = renamed_profile_ids[active_profile_id]
    if active_profile_id is not None and store.profiles.get(active_profile_id) is not None:
        store.settings.set_active_profile_id(active_profile_id)

    settings = payload.get("app_settings") or {}
    for device_id, tool_id in (settings.get("active_tool_by_device") or {}).items():
        store.settings.set_active_tool_for_device(str(device_id), str(tool_id))
    for key, value in (settings.get("global_flags") or {}).items():
        store.settings.set_global_flag(str(key), value)
