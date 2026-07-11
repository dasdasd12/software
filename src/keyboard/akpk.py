"""AKPK ProfilePackage / AKRT RuntimeTable compiler.

Compiles a profile source JSON (docs/architecture/keyboard_config_site
profile schema) into the binary containers consumed by the H417 V3F
firmware:

  - RuntimeTableBinary v1 ("AKRT", runtime_contract.html): 13-section
    directory; this compiler populates control_index_map, trigger_table,
    dispatch_table, behavior_table, mutable_param_slots, resource_limits
    and scope_table, leaving the rest declared-but-empty.
  - ProfilePackageBinary v1 ("AKPK", profile_package.html): canonical
    source JSON + runtime table cache + cache metadata.

Threshold/delta values are carried in permille of full travel (0..1000),
the CH585 magnetic key engine native unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import struct
from typing import Any


PROFILE_SCHEMA_VERSION = 1
PACKAGE_VERSION = 1
RUNTIME_TABLE_VERSION = 1
RUNTIME_ABI_VERSION = 1
COMPILER_IR_VERSION = 1

KEY_COUNT = 77
CONTROL_INDEX_FIVEWAY = 77
CONTROL_INDEX_ENC = 78
CONTROL_COUNT = 79

PKG_MAGIC = b"AKPK"
PKG_HEADER_SIZE = 68
RT_MAGIC = b"AKRT"
RT_HEADER_SIZE = 108
RT_SECTION_COUNT = 13

SECTION_SOURCE_PROFILE_JSON = 0x0001
SECTION_RUNTIME_TABLE_CACHE = 0x0002
SECTION_RUNTIME_TABLE_CACHE_META = 0x0005

ENCODING_CANONICAL_JSON = 0x01
ENCODING_RUNTIME_TABLE_BINARY = 0x02

RT_SECTION_CONTROL_INDEX_MAP = 0x0001
RT_SECTION_TRIGGER_TABLE = 0x0002
RT_SECTION_INTERACTION_TABLE = 0x0003
RT_SECTION_DISPATCH_TABLE = 0x0004
RT_SECTION_BEHAVIOR_TABLE = 0x0005
RT_SECTION_MACRO_BYTECODE = 0x0006
RT_SECTION_MUTABLE_PARAM_SLOTS = 0x0007
RT_SECTION_RESOURCE_LIMITS = 0x0008
RT_SECTION_SCOPE_TABLE = 0x0009
RT_SECTION_INTERACTION_MEMBER_TABLE = 0x000A
RT_SECTION_VIRTUAL_SIGNAL_TABLE = 0x000B
RT_SECTION_DKS_STAGE_TABLE = 0x000C
RT_SECTION_PARAM_CONSTRAINT_TABLE = 0x000D

RT_ENTRY_SIZES = {
    RT_SECTION_CONTROL_INDEX_MAP: 16,
    RT_SECTION_TRIGGER_TABLE: 20,
    RT_SECTION_INTERACTION_TABLE: 16,
    RT_SECTION_DISPATCH_TABLE: 16,
    RT_SECTION_BEHAVIOR_TABLE: 20,
    RT_SECTION_MACRO_BYTECODE: 1,
    RT_SECTION_MUTABLE_PARAM_SLOTS: 28,
    RT_SECTION_RESOURCE_LIMITS: 32,
    RT_SECTION_SCOPE_TABLE: 12,
    RT_SECTION_INTERACTION_MEMBER_TABLE: 16,
    RT_SECTION_VIRTUAL_SIGNAL_TABLE: 12,
    RT_SECTION_DKS_STAGE_TABLE: 16,
    RT_SECTION_PARAM_CONSTRAINT_TABLE: 16,
}

CONTROL_TYPE_AKEY = 0x01
CONTROL_TYPE_BUTTON = 0x03
CONTROL_TYPE_ENCODER = 0x05

SOURCE_KIND_H417_KEY = 0x01
SOURCE_KIND_CH585_PERIPHERAL = 0x02

MODE_VALUES = {"normal": 0x01, "rapid_trigger": 0x02, "disabled": 0x7F}

SIGNAL_SOURCE_CONTROL = 0x01
RESULT_BEHAVIOR = 0x01

BEHAVIOR_HOST_INPUT = 0x01
BEHAVIOR_OVERLAY_CONTROL = 0x04

HOST_USAGE_KEYBOARD = 0x0001
HOST_USAGE_CONSUMER = 0x0002
HOST_USAGE_MOUSE_AXIS = 0x0004
MOUSE_AXIS_WHEEL = 0x00

OVERLAY_ACTIONS = {
    "hold": 0x0001,
    "toggle": 0x0002,
    "oneshot": 0x0003,
    "set_active": 0x0004,
    "set_inactive": 0x0005,
}

EVENT_PRESS = 0x0001
EVENT_CW_STEP = 0x0008
EVENT_CCW_STEP = 0x0009
EVENT_CONTROL_LEVEL = 0xFFFF
# Firmware extension events for the fiveway directions (pending doc ids).
EVENT_FW_UP = 0x0100
EVENT_FW_DOWN = 0x0101
EVENT_FW_LEFT = 0x0102
EVENT_FW_RIGHT = 0x0103

PARAM_PRESS_THRESHOLD = 0x0003
PARAM_PRESS_DELTA = 0x0004
PARAM_RELEASE_DELTA = 0x0014
PARAM_RELEASE_THRESHOLD = 0x0015
PARAM_RESET_THRESHOLD = 0x0016
PARAM_DEADZONE = 0x0017

VALUE_KIND_I32 = 0x01
UNIT_NORM_I16 = 0x03

PARAM_ORDER = (
    PARAM_PRESS_THRESHOLD,
    PARAM_RELEASE_THRESHOLD,
    PARAM_RESET_THRESHOLD,
    PARAM_PRESS_DELTA,
    PARAM_RELEASE_DELTA,
    PARAM_DEADZONE,
)

KEYBOARD_USAGES = {
    **{chr(ord("a") + i): 0x04 + i for i in range(26)},
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "enter": 0x28, "escape": 0x29, "backspace": 0x2A, "tab": 0x2B,
    "space": 0x2C, "minus": 0x2D, "equal": 0x2E, "left_bracket": 0x2F,
    "right_bracket": 0x30, "backslash": 0x31, "semicolon": 0x33,
    "quote": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37,
    "slash": 0x38, "caps_lock": 0x39,
    "f1": 0x3A, "f2": 0x3B, "f3": 0x3C, "f4": 0x3D, "f5": 0x3E,
    "f6": 0x3F, "f7": 0x40, "f8": 0x41, "f9": 0x42, "f10": 0x43,
    "f11": 0x44, "f12": 0x45,
    "print_screen": 0x46, "scroll_lock": 0x47, "pause": 0x48,
    "insert": 0x49, "home": 0x4A, "page_up": 0x4B, "delete": 0x4C,
    "end": 0x4D, "page_down": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "application": 0x65,
}

KEYBOARD_MODIFIERS = {
    "left_ctrl": 0x01, "left_shift": 0x02, "left_alt": 0x04,
    "left_gui": 0x08, "right_ctrl": 0x10, "right_shift": 0x20,
    "right_alt": 0x40, "right_gui": 0x80,
}

CONSUMER_USAGES = {
    "volume_increment": 0x00E9,
    "volume_decrement": 0x00EA,
    "mute": 0x00E2,
    "play_pause": 0x00CD,
    "scan_next_track": 0x00B5,
    "scan_previous_track": 0x00B6,
    "stop": 0x00B7,
}

LOCAL_EVENTS = {
    "up": EVENT_FW_UP,
    "down": EVENT_FW_DOWN,
    "left": EVENT_FW_LEFT,
    "right": EVENT_FW_RIGHT,
    "press": EVENT_PRESS,
    "cw_step": EVENT_CW_STEP,
    "ccw_step": EVENT_CCW_STEP,
}


class ProfileCompileError(ValueError):
    pass


@dataclass
class PackageResult:
    package: bytes
    runtime_table: bytes
    canonical_source: bytes
    source_hash: bytes
    profile_id: str
    profile_id16: int
    revision: int
    manifest: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def crc32c(data: bytes, seed: int = 0) -> int:
    crc = seed ^ 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def canonical_json_bytes(value: Any) -> bytes:
    _reject_non_integer_numbers(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _reject_non_integer_numbers(value: Any) -> None:
    if isinstance(value, float):
        raise ProfileCompileError(
            "profile source must not contain floating point numbers"
        )
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_integer_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _reject_non_integer_numbers(item)


def _require(container: dict[str, Any], key: str, kind: type) -> Any:
    value = container.get(key)
    if not isinstance(value, kind):
        raise ProfileCompileError(f"{key} must be {kind.__name__}")
    return value


def _control_index(control_id: str) -> int:
    if control_id.startswith("key_"):
        try:
            key_id = int(control_id[4:], 10)
        except ValueError as exc:
            raise ProfileCompileError(f"bad control id {control_id}") from exc
        if not 0 <= key_id < KEY_COUNT:
            raise ProfileCompileError(f"key out of range: {control_id}")
        return key_id
    if control_id == "fiveway_000":
        return CONTROL_INDEX_FIVEWAY
    if control_id == "enc_000":
        return CONTROL_INDEX_ENC
    raise ProfileCompileError(f"unknown control id {control_id}")


@dataclass
class _Trigger:
    mode: int
    params: dict[int, int]


def _effective_triggers(profile: dict[str, Any],
                        warnings: list[str]) -> list[_Trigger]:
    defaults = _require(profile, "defaults", dict)
    trig_defaults = _require(_require(defaults, "triggers", dict), "akey", dict)
    common = _require(trig_defaults, "common", dict)
    normal = _require(trig_defaults, "normal", dict)
    rapid = _require(trig_defaults, "rapid_trigger", dict)

    def default_params() -> dict[int, int]:
        return {
            PARAM_PRESS_THRESHOLD: _require(normal,
                                            "press_threshold_norm_i16", int),
            PARAM_RELEASE_THRESHOLD: _require(normal,
                                              "release_threshold_norm_i16",
                                              int),
            PARAM_RESET_THRESHOLD: _require(rapid,
                                            "reset_threshold_norm_i16", int),
            PARAM_PRESS_DELTA: _require(rapid, "press_delta_norm_i16", int),
            PARAM_RELEASE_DELTA: _require(rapid, "release_delta_norm_i16",
                                          int),
            PARAM_DEADZONE: _require(common, "deadzone_norm_i16", int),
        }

    param_keys = {
        "press_threshold_norm_i16": PARAM_PRESS_THRESHOLD,
        "release_threshold_norm_i16": PARAM_RELEASE_THRESHOLD,
        "reset_threshold_norm_i16": PARAM_RESET_THRESHOLD,
        "press_delta_norm_i16": PARAM_PRESS_DELTA,
        "release_delta_norm_i16": PARAM_RELEASE_DELTA,
        "deadzone_norm_i16": PARAM_DEADZONE,
    }

    triggers = [_Trigger(mode=MODE_VALUES["rapid_trigger"],
                         params=default_params())
                for _ in range(KEY_COUNT)]

    assignments = profile.get("control_assignments")
    if not isinstance(assignments, list):
        raise ProfileCompileError("control_assignments must be a list")

    for assignment in assignments:
        if not isinstance(assignment, dict):
            raise ProfileCompileError("control_assignment must be an object")
        controls = assignment.get("controls")
        if not isinstance(controls, list):
            raise ProfileCompileError("assignment controls must be a list")
        a_type = assignment.get("type")
        mode_name = assignment.get("mode")
        if a_type != "akey":
            warnings.append(f"assignment type {a_type} ignored (v1 firmware)")
            continue
        if mode_name not in MODE_VALUES:
            raise ProfileCompileError(f"unsupported akey mode {mode_name}")

        params = assignment.get("params")
        if params == {"defaults": True}:
            effective = default_params()
        elif isinstance(params, dict):
            effective = default_params()
            for name, value in params.items():
                if name == "defaults":
                    continue
                if name not in param_keys:
                    warnings.append(f"assignment param {name} ignored")
                    continue
                if not isinstance(value, int):
                    raise ProfileCompileError(f"param {name} must be int")
                effective[param_keys[name]] = value
        else:
            raise ProfileCompileError("assignment params must be an object")

        for name, value in effective.items():
            if not 0 <= value <= 1000:
                raise ProfileCompileError(
                    f"trigger param 0x{name:04x}={value} out of 0..1000"
                )

        targets: list[int] = []
        for control in controls:
            if control == "@main_keys":
                targets.extend(range(KEY_COUNT))
            else:
                index = _control_index(str(control))
                if index >= KEY_COUNT:
                    warnings.append(
                        f"akey assignment to non-key {control} ignored"
                    )
                    continue
                targets.append(index)

        for key_id in targets:
            triggers[key_id] = _Trigger(mode=MODE_VALUES[mode_name],
                                        params=dict(effective))

    return triggers


@dataclass
class _Behavior:
    kind: int
    data: tuple[int, int, int, int]


class _BehaviorTable:
    def __init__(self) -> None:
        self.entries: list[_Behavior] = []
        self._index: dict[tuple[int, int, int, int, int], int] = {}

    def add(self, kind: int, data0: int, data1: int = 0, data2: int = 0,
            data3: int = 0) -> int:
        key = (kind, data0, data1, data2, data3)
        if key in self._index:
            return self._index[key]
        index = len(self.entries)
        self.entries.append(_Behavior(kind, (data0, data1, data2, data3)))
        self._index[key] = index
        return index


def _binding_behavior(target: str, table: _BehaviorTable,
                      scope_indexes: dict[str, int],
                      behaviors_def: dict[str, Any],
                      warnings: list[str]) -> int | None:
    """Resolve a binding target string to a behavior index (None = no-op)."""
    if target in ("no_op", "transparent"):
        return None
    if target.startswith("keyboard."):
        name = target[len("keyboard."):]
        if name in KEYBOARD_MODIFIERS:
            usage_id = KEYBOARD_MODIFIERS[name] << 8
        elif name in KEYBOARD_USAGES:
            usage_id = KEYBOARD_USAGES[name]
        else:
            raise ProfileCompileError(f"unknown keyboard usage {target}")
        return table.add(BEHAVIOR_HOST_INPUT, HOST_USAGE_KEYBOARD, usage_id)
    if target.startswith("consumer."):
        name = target[len("consumer."):]
        if name not in CONSUMER_USAGES:
            raise ProfileCompileError(f"unknown consumer usage {target}")
        return table.add(BEHAVIOR_HOST_INPUT, HOST_USAGE_CONSUMER,
                         CONSUMER_USAGES[name])
    if target in ("mouse.wheel_up", "mouse.wheel_down"):
        step = 1 if target.endswith("up") else 0xFF  # int8 -1
        return table.add(BEHAVIOR_HOST_INPUT, HOST_USAGE_MOUSE_AXIS,
                         (step << 8) | MOUSE_AXIS_WHEEL)
    if target.startswith("b_"):
        definition = behaviors_def.get(target)
        if not isinstance(definition, dict):
            raise ProfileCompileError(f"behavior {target} is not defined")
        kind = definition.get("kind")
        if kind == "overlay_control":
            scope = definition.get("scope")
            action = definition.get("action")
            if scope not in scope_indexes:
                raise ProfileCompileError(
                    f"behavior {target} references unknown scope {scope}"
                )
            if action not in OVERLAY_ACTIONS:
                raise ProfileCompileError(
                    f"behavior {target} action {action} unsupported"
                )
            return table.add(BEHAVIOR_OVERLAY_CONTROL, scope_indexes[scope],
                             OVERLAY_ACTIONS[action])
        raise ProfileCompileError(
            f"behavior kind {kind} not supported by v1 compiler"
        )
    warnings.append(f"binding target {target} unsupported, treated as no_op")
    return None


def _signal_key(raw_key: str) -> tuple[int, int]:
    """ControlSignalKey -> (control_index, signal_event_code)."""
    if "." in raw_key:
        control_id, event_name = raw_key.split(".", 1)
        control = _control_index(control_id)
        if control < KEY_COUNT:
            if event_name != "press":
                raise ProfileCompileError(
                    f"key event {raw_key} unsupported (press only)"
                )
            return control, EVENT_PRESS
        if event_name not in LOCAL_EVENTS:
            raise ProfileCompileError(f"unknown signal {raw_key}")
        return control, LOCAL_EVENTS[event_name]
    control = _control_index(raw_key)
    return control, EVENT_CONTROL_LEVEL


def compile_runtime_table(profile: dict[str, Any],
                          warnings: list[str]) -> bytes:
    identity = _require(profile, "identity", dict)
    compatibility = _require(profile, "compatibility", dict)

    schema_version = identity.get("schema_version", PROFILE_SCHEMA_VERSION)
    control_map_hash = _decode_hash(
        _require(compatibility, "control_map_hash", str)
    )
    source_hash = hashlib.sha256(canonical_json_bytes(profile)).digest()

    triggers = _effective_triggers(profile, warnings)

    scopes_src = _require(profile, "binding_scopes", dict)
    if "base" not in scopes_src:
        raise ProfileCompileError("binding_scopes.base is required")
    scope_names = ["base"] + sorted(k for k in scopes_src if k != "base")
    scope_indexes = {name: i for i, name in enumerate(scope_names)}

    behaviors_def = profile.get("behavior_defs") or profile.get(
        "behaviors", {}
    )
    if not isinstance(behaviors_def, dict):
        raise ProfileCompileError("behaviors must be an object")

    behavior_table = _BehaviorTable()
    dispatch: list[tuple[int, int, int, int, int]] = []
    # (scope_index, source_kind, control_index, event_code, behavior_index)

    for scope_name in scope_names:
        scope = scopes_src[scope_name]
        if not isinstance(scope, dict):
            raise ProfileCompileError(f"scope {scope_name} must be an object")
        bindings = scope.get("bindings", {})
        if not isinstance(bindings, dict):
            raise ProfileCompileError(f"{scope_name}.bindings must be object")
        for raw_key, target in bindings.items():
            if not isinstance(target, str):
                raise ProfileCompileError(
                    f"binding {raw_key} target must be a string"
                )
            control, event = _signal_key(raw_key)
            behavior = _binding_behavior(target, behavior_table,
                                         scope_indexes, behaviors_def,
                                         warnings)
            if behavior is None:
                continue
            dispatch.append((scope_indexes[scope_name],
                             SIGNAL_SOURCE_CONTROL, control, event, behavior))

    dispatch.sort(key=lambda d: (d[0], d[1], d[2], d[3]))

    # --- section payloads -------------------------------------------------
    control_map = bytearray()
    for key_id in range(KEY_COUNT):
        control_map += struct.pack(
            "<HBBHHIHH",
            key_id, CONTROL_TYPE_AKEY, SOURCE_KIND_H417_KEY,
            key_id, key_id,
            crc32c(f"key_{key_id:03d}".encode("ascii")),
            key_id, 1,
        )
    control_map += struct.pack(
        "<HBBHHIHH",
        CONTROL_INDEX_FIVEWAY, CONTROL_TYPE_BUTTON,
        SOURCE_KIND_CH585_PERIPHERAL, 0, 0,
        crc32c(b"fiveway_000"), 0xFFFF, 0,
    )
    control_map += struct.pack(
        "<HBBHHIHH",
        CONTROL_INDEX_ENC, CONTROL_TYPE_ENCODER,
        SOURCE_KIND_CH585_PERIPHERAL, 1, 0,
        crc32c(b"enc_000"), 0xFFFF, 0,
    )

    trigger_payload = bytearray()
    param_payload = bytearray()
    slot_index = 0
    for key_id, trigger in enumerate(triggers):
        trigger_payload += struct.pack(
            "<HHBBHHHHHHH",
            key_id, key_id, CONTROL_TYPE_AKEY, trigger.mode,
            slot_index, len(PARAM_ORDER),
            EVENT_PRESS, 2, 0, 0, 0,
        )
        for order, param_id in enumerate(PARAM_ORDER):
            param_payload += struct.pack(
                "<HHHHHHBBBBiii",
                slot_index, RT_SECTION_TRIGGER_TABLE, key_id, key_id,
                param_id, order,
                VALUE_KIND_I32, 4, UNIT_NORM_I16, 1,
                0, 1000, trigger.params[param_id],
            )
            slot_index += 1

    scope_payload = bytearray()
    for name in scope_names:
        scope = scopes_src[name]
        unbound = scope.get("unbound", "no_op" if name == "base"
                            else "transparent")
        unbound_kind = {"transparent": 0x02, "no_op": 0x03}.get(unbound)
        if unbound_kind is None:
            raise ProfileCompileError(f"scope {name} unbound {unbound}")
        scope_payload += struct.pack(
            "<HhBBBBHH",
            scope_indexes[name],
            int(scope.get("priority", 0)),
            1 if scope.get("default_active", name == "base") else 0,
            1 if name == "base" else 0,
            unbound_kind, 0, 0xFFFF, 0,
        )

    dispatch_payload = bytearray()
    for index, (scope_i, source_kind, control, event, behavior) in enumerate(
        dispatch
    ):
        dispatch_payload += struct.pack(
            "<HHBBHHBBHH",
            index, scope_i, source_kind, 0, control, event,
            RESULT_BEHAVIOR, 0, behavior, 0,
        )

    behavior_payload = bytearray()
    for index, behavior in enumerate(behavior_table.entries):
        behavior_payload += struct.pack(
            "<HBBIIII",
            index, behavior.kind, 0, *behavior.data,
        )

    limits_payload = struct.pack(
        "<HHHHHHHHIHHHHI",
        CONTROL_COUNT, KEY_COUNT, 8, 512, 0, 128, 0, 0,
        0, 1024, 0, 0, 0, 0,
    )

    payloads = {
        RT_SECTION_CONTROL_INDEX_MAP: bytes(control_map),
        RT_SECTION_TRIGGER_TABLE: bytes(trigger_payload),
        RT_SECTION_DISPATCH_TABLE: bytes(dispatch_payload),
        RT_SECTION_BEHAVIOR_TABLE: bytes(behavior_payload),
        RT_SECTION_MUTABLE_PARAM_SLOTS: bytes(param_payload),
        RT_SECTION_RESOURCE_LIMITS: limits_payload,
        RT_SECTION_SCOPE_TABLE: bytes(scope_payload),
    }

    # --- assemble table ----------------------------------------------------
    directory_offset = RT_HEADER_SIZE
    payload_offset = RT_HEADER_SIZE + RT_SECTION_COUNT * 20
    directory = bytearray()
    body = bytearray()
    for kind in range(1, RT_SECTION_COUNT + 1):
        entry_size = RT_ENTRY_SIZES[kind]
        payload = payloads.get(kind, b"")
        if payload:
            if len(payload) % entry_size:
                raise ProfileCompileError(
                    f"section {kind:#06x} payload misaligned"
                )
            entry_count = len(payload) // entry_size
            offset = payload_offset + len(body)
            directory += struct.pack(
                "<HHIIII", kind, entry_size, entry_count, offset,
                len(payload), crc32c(payload),
            )
            body += payload
            while len(body) % 4:
                body += b"\x00"
        else:
            directory += struct.pack("<HHIIII", kind, entry_size, 0, 0, 0, 0)

    total_size = payload_offset + len(body)
    header = struct.pack(
        "<4sHHHHHHBBHIIHH32s32sIII",
        RT_MAGIC,
        RUNTIME_TABLE_VERSION, RUNTIME_ABI_VERSION, COMPILER_IR_VERSION,
        schema_version,
        RT_HEADER_SIZE, RT_SECTION_COUNT,
        2, 4, 0,
        0, 0, 0, 0,
        source_hash, control_map_hash,
        0, total_size, 0,
    )
    assert len(header) == RT_HEADER_SIZE
    assert directory_offset + len(directory) == payload_offset

    table = bytearray(header + directory + body)
    crc = crc32c(bytes(table[:RT_HEADER_SIZE - 4]) + b"\x00\x00\x00\x00" +
                 bytes(table[RT_HEADER_SIZE:]))
    struct.pack_into("<I", table, RT_HEADER_SIZE - 4, crc)
    return bytes(table)


def _decode_hash(value: str) -> bytes:
    if not value.startswith("sha256:"):
        raise ProfileCompileError("hash must use sha256:<hex> form")
    raw = bytes.fromhex(value[len("sha256:"):])
    if len(raw) != 32:
        raise ProfileCompileError("hash must be 32 bytes")
    return raw


def build_package(profile: dict[str, Any]) -> PackageResult:
    warnings: list[str] = []
    identity = _require(profile, "identity", dict)
    compatibility = _require(profile, "compatibility", dict)
    profile_id = _require(identity, "profile_id", str)
    revision = identity.get("revision", 1)
    if not isinstance(revision, int) or revision < 1:
        raise ProfileCompileError("identity.revision must be a positive int")
    if "revision" not in identity:
        warnings.append("identity.revision missing, defaulted to 1")
    schema_version = identity.get("schema_version", PROFILE_SCHEMA_VERSION)
    keyboard_model_id = _require(compatibility, "keyboard_model_id", str)

    canonical = canonical_json_bytes(profile)
    source_hash = hashlib.sha256(canonical).digest()
    runtime_table = compile_runtime_table(profile, warnings)

    metadata = {
        "source_hash": "sha256:" + source_hash.hex(),
        "control_map_hash": compatibility["control_map_hash"],
        "runtime_table_version": RUNTIME_TABLE_VERSION,
        "runtime_abi_version": RUNTIME_ABI_VERSION,
        "compiler_ir_version": COMPILER_IR_VERSION,
        "resource_limit_profile_id": 0,
        "required_feature_flags": 0,
        "firmware_compat": 0,
    }
    metadata_bytes = canonical_json_bytes(metadata)

    sections = [
        (SECTION_SOURCE_PROFILE_JSON, ENCODING_CANONICAL_JSON, canonical),
        (SECTION_RUNTIME_TABLE_CACHE, ENCODING_RUNTIME_TABLE_BINARY,
         runtime_table),
        (SECTION_RUNTIME_TABLE_CACHE_META, ENCODING_CANONICAL_JSON,
         metadata_bytes),
    ]

    directory = bytearray()
    body = bytearray()
    payload_offset = PKG_HEADER_SIZE + len(sections) * 16
    for kind, encoding, payload in sections:
        offset = payload_offset + len(body)
        directory += struct.pack(
            "<HBBIII", kind, encoding, 0, offset, len(payload),
            crc32c(payload),
        )
        body += payload
        while len(body) % 4:
            body += b"\x00"

    total_size = payload_offset + len(body)
    header = struct.pack(
        "<4sHHHHIIII32sII",
        PKG_MAGIC, PACKAGE_VERSION, PKG_HEADER_SIZE, len(sections),
        schema_version, 0,
        crc32c(keyboard_model_id.encode("ascii")),
        crc32c(profile_id.encode("ascii")),
        revision, source_hash, total_size, 0,
    )
    assert len(header) == PKG_HEADER_SIZE

    package = bytearray(header + directory + body)
    crc = crc32c(bytes(package[:PKG_HEADER_SIZE - 4]) + b"\x00\x00\x00\x00" +
                 bytes(package[PKG_HEADER_SIZE:]))
    struct.pack_into("<I", package, PKG_HEADER_SIZE - 4, crc)

    profile_id16 = crc32c(profile_id.encode("ascii")) & 0xFFFF
    manifest = {
        "format": "AKPK",
        "profile_id": profile_id,
        "profile_id16": f"0x{profile_id16:04x}",
        "revision": revision,
        "package_size": total_size,
        "runtime_table_size": len(runtime_table),
        "source_size": len(canonical),
        "source_sha256": source_hash.hex(),
        "warnings": warnings,
    }

    return PackageResult(
        package=bytes(package),
        runtime_table=runtime_table,
        canonical_source=canonical,
        source_hash=source_hash,
        profile_id=profile_id,
        profile_id16=profile_id16,
        revision=revision,
        manifest=manifest,
        warnings=warnings,
    )


def emit_c_image(package: bytes, symbol: str, header_guard: str,
                 header_name: str) -> tuple[str, str]:
    header_text = (
        f"#ifndef {header_guard}\n"
        f"#define {header_guard}\n\n"
        "#include <stdint.h>\n\n"
        f"extern const uint8_t {symbol}[];\n"
        f"extern const uint32_t {symbol}_size;\n\n"
        "#endif\n"
    )
    rows = []
    for start in range(0, len(package), 12):
        row = ", ".join(f"0x{value:02X}" for value in package[start:start + 12])
        rows.append(f"    {row},")
    source_text = (
        f'#include "{header_name}"\n\n'
        f"const uint8_t {symbol}[] = {{\n"
        + "\n".join(rows)
        + "\n};\n\n"
        f"const uint32_t {symbol}_size = {len(package)}U;\n"
    )
    return source_text, header_text
