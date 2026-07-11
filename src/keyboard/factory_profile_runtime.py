"""Compile the factory profile JSON into the compact H417 flash image."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import struct
from pathlib import Path
from typing import Any


KEY_COUNT = 77
FACTORY_RUNTIME_MAGIC = b"AKPF"
FACTORY_RUNTIME_SIZE = 188
DEFAULT_RELEASED_ADC = 490
DEFAULT_PRESSED_ADC = 330
DEFAULT_FILTER_SHIFT = 0
MODE_RAPID_TRIGGER = 1
TRIGGER_FLAG_RETRIGGER_BEFORE_RESET = 0x01

_TRIGGER_STRUCT = struct.Struct("<HHHHHHHBBBB")
_HEADER_STRUCT = struct.Struct("<4sHH")
_REPORT_STRUCT = struct.Struct("<HH")
_CRC_OFFSET = FACTORY_RUNTIME_SIZE - 2


_KEYBOARD_USAGES = {
    **{chr(ord("a") + i): 0x04 + i for i in range(26)},
    "1": 0x1E,
    "2": 0x1F,
    "3": 0x20,
    "4": 0x21,
    "5": 0x22,
    "6": 0x23,
    "7": 0x24,
    "8": 0x25,
    "9": 0x26,
    "0": 0x27,
    "enter": 0x28,
    "escape": 0x29,
    "backspace": 0x2A,
    "tab": 0x2B,
    "space": 0x2C,
    "minus": 0x2D,
    "equal": 0x2E,
    "left_bracket": 0x2F,
    "right_bracket": 0x30,
    "backslash": 0x31,
    "semicolon": 0x33,
    "quote": 0x34,
    "grave": 0x35,
    "comma": 0x36,
    "period": 0x37,
    "slash": 0x38,
    "caps_lock": 0x39,
    "f1": 0x3A,
    "f2": 0x3B,
    "f3": 0x3C,
    "f4": 0x3D,
    "f5": 0x3E,
    "f6": 0x3F,
    "f7": 0x40,
    "f8": 0x41,
    "f9": 0x42,
    "f10": 0x43,
    "f11": 0x44,
    "f12": 0x45,
    "right": 0x4F,
    "left": 0x50,
    "down": 0x51,
    "up": 0x52,
}

_KEYBOARD_MODIFIERS = {
    "left_ctrl": 0x01,
    "left_shift": 0x02,
    "left_alt": 0x04,
    "left_gui": 0x08,
    "right_ctrl": 0x10,
    "right_shift": 0x20,
    "right_alt": 0x40,
    "right_gui": 0x80,
}


@dataclass(frozen=True)
class FactoryRuntimeResult:
    image: bytes
    manifest: dict[str, Any]


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def compile_factory_profile(profile: dict[str, Any]) -> FactoryRuntimeResult:
    profile_id = _require_dict(profile, "identity").get("profile_id")
    if profile_id != "factory_default":
        raise ValueError("factory profile identity.profile_id must be factory_default")

    _validate_main_key_assignment(profile)
    trigger = _compile_global_trigger(profile)
    report_policy = _compile_report_policy(profile)
    key_outputs, warnings = _compile_key_outputs(profile)

    image = bytearray(FACTORY_RUNTIME_SIZE)
    _HEADER_STRUCT.pack_into(image, 0, FACTORY_RUNTIME_MAGIC, FACTORY_RUNTIME_SIZE, 0)
    _TRIGGER_STRUCT.pack_into(image, 8, *trigger)
    _REPORT_STRUCT.pack_into(image, 26, *report_policy)

    offset = 30
    for usage, modifier in key_outputs:
        image[offset] = usage
        image[offset + 1] = modifier
        offset += 2

    image[184] = 0
    image[185] = 0
    struct.pack_into("<H", image, _CRC_OFFSET, crc16_ccitt(bytes(image[:_CRC_OFFSET])))

    return FactoryRuntimeResult(
        image=bytes(image),
        manifest={
            "profile_id": profile_id,
            "format": "AKPF",
            "size": FACTORY_RUNTIME_SIZE,
            "key_count": KEY_COUNT,
            "global_trigger": {
                "released_adc": trigger[0],
                "pressed_adc": trigger[1],
                "press_pm": trigger[2],
                "release_pm": trigger[3],
                "rt_press_delta_pm": trigger[4],
                "rt_release_delta_pm": trigger[5],
                "deadzone_pm": trigger[6],
                "filter_shift": trigger[7],
                "mode": "rapid_trigger",
                "trigger_flags": trigger[9],
                "retrigger_before_reset": bool(trigger[9] & TRIGGER_FLAG_RETRIGGER_BEFORE_RESET),
            },
            "report_policy": {
                "usb_report_rate_hz": report_policy[0],
                "ch585_wireless_report_rate_hz": report_policy[1],
            },
            "local_assignment_count": 0,
            "source_sha256": _canonical_sha256(profile),
            "warnings": warnings,
        },
    )


def load_compile_write(
    source: Path,
    output_bin: Path,
    output_manifest: Path | None = None,
    output_c: Path | None = None,
    output_h: Path | None = None,
) -> FactoryRuntimeResult:
    profile = json.loads(source.read_text(encoding="utf-8"))
    result = compile_factory_profile(profile)

    output_bin.parent.mkdir(parents=True, exist_ok=True)
    output_bin.write_bytes(result.image)

    if output_manifest is not None:
        output_manifest.parent.mkdir(parents=True, exist_ok=True)
        output_manifest.write_text(
            json.dumps(result.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if output_c is not None:
        if output_h is None:
            output_h = output_c.with_suffix(".h")
        _write_c_image(output_c, output_h, result.image)

    return result


def _compile_global_trigger(profile: dict[str, Any]) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
    defaults = _require_dict(profile, "defaults")
    triggers = _require_dict(defaults, "triggers")
    akey = _require_dict(triggers, "akey")
    common = _require_dict(akey, "common")
    normal = _require_dict(akey, "normal")
    rapid = _require_dict(akey, "rapid_trigger")

    press_pm = _require_int(normal, "press_threshold_norm_i16")
    release_pm = _require_int(normal, "release_threshold_norm_i16")
    reset_pm = _require_int(rapid, "reset_threshold_norm_i16")
    press_delta = _require_int(rapid, "press_delta_norm_i16")
    release_delta = _require_int(rapid, "release_delta_norm_i16")
    deadzone_pm = _require_int(common, "deadzone_norm_i16")
    retrigger_before_reset = _require_bool(rapid, "retrigger_before_reset")

    _require_pm("press_threshold_norm_i16", press_pm)
    _require_pm("release_threshold_norm_i16", release_pm)
    _require_pm("reset_threshold_norm_i16", reset_pm)
    _require_pm("press_delta_norm_i16", press_delta)
    _require_pm("release_delta_norm_i16", release_delta)
    _require_pm("deadzone_norm_i16", deadzone_pm)
    if release_pm != reset_pm:
        raise ValueError(
            "factory runtime requires normal.release_threshold_norm_i16 "
            "to match rapid_trigger.reset_threshold_norm_i16"
        )

    trigger_flags = TRIGGER_FLAG_RETRIGGER_BEFORE_RESET if retrigger_before_reset else 0

    return (
        DEFAULT_RELEASED_ADC,
        DEFAULT_PRESSED_ADC,
        press_pm,
        release_pm,
        press_delta,
        release_delta,
        deadzone_pm,
        DEFAULT_FILTER_SHIFT,
        MODE_RAPID_TRIGGER,
        trigger_flags,
        0,
    )


def _compile_report_policy(profile: dict[str, Any]) -> tuple[int, int]:
    policy = _require_dict(profile, "report_rate_policy")
    usb = _require_int(policy, "usb_report_rate_hz")
    wireless = _require_int(policy, "ch585_wireless_report_rate_hz")
    if not (1 <= usb <= 65535):
        raise ValueError("usb_report_rate_hz out of range")
    if not (1 <= wireless <= 65535):
        raise ValueError("ch585_wireless_report_rate_hz out of range")
    return usb, wireless


def _compile_key_outputs(profile: dict[str, Any]) -> tuple[list[tuple[int, int]], list[str]]:
    scopes = _require_dict(profile, "binding_scopes")
    base_scope = _require_dict(scopes, "base")
    bindings = _require_dict(base_scope, "bindings")
    unbound = base_scope.get("unbound", "no_op")
    warnings: list[str] = []
    outputs: list[tuple[int, int]] = []

    for key_id in range(KEY_COUNT):
        control_id = f"key_{key_id:03d}"
        target = bindings.get(control_id, unbound)
        outputs.append(_compile_keyboard_target(control_id, target))

    for control_id, target in bindings.items():
        if control_id.startswith("key_"):
            continue
        if isinstance(target, str) and target != "no_op":
            warnings.append(f"binding {control_id} targets unsupported {target}")

    return outputs, warnings


def _compile_keyboard_target(control_id: str, target: Any) -> tuple[int, int]:
    if target == "no_op":
        return 0, 0
    if not isinstance(target, str) or not target.startswith("keyboard."):
        raise ValueError(f"binding {control_id} must target keyboard.* or no_op")

    name = target[len("keyboard.") :]
    if name in _KEYBOARD_MODIFIERS:
        return 0, _KEYBOARD_MODIFIERS[name]
    if name in _KEYBOARD_USAGES:
        return _KEYBOARD_USAGES[name], 0
    raise ValueError(f"binding {control_id} targets unsupported {target}")


def _validate_main_key_assignment(profile: dict[str, Any]) -> None:
    assignments = profile.get("control_assignments")
    if not isinstance(assignments, list) or len(assignments) != 1:
        raise ValueError("factory profile requires one main key control_assignment")

    assignment = assignments[0]
    if not isinstance(assignment, dict):
        raise ValueError("control_assignment must be an object")
    if assignment.get("controls") != ["@main_keys"]:
        raise ValueError("factory profile only supports @main_keys assignment")
    if assignment.get("type") != "akey":
        raise ValueError("factory profile only supports akey assignment")
    if assignment.get("mode") != "rapid_trigger":
        raise ValueError("factory profile only supports rapid_trigger assignment")
    if assignment.get("params") != {"defaults": True}:
        raise ValueError("non-default assignment params are not supported yet")


def _canonical_sha256(profile: dict[str, Any]) -> str:
    payload = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_c_image(output_c: Path, output_h: Path, image: bytes) -> None:
    output_c.parent.mkdir(parents=True, exist_ok=True)
    output_h.parent.mkdir(parents=True, exist_ok=True)
    header_name = output_h.name

    output_h.write_text(
        "#ifndef V3F_PROFILE_FLASH_IMAGE_H\n"
        "#define V3F_PROFILE_FLASH_IMAGE_H\n\n"
        "#include <stdint.h>\n\n"
        "extern const uint8_t g_v3f_profile_flash_image[];\n"
        "extern const uint32_t g_v3f_profile_flash_image_size;\n\n"
        "#endif\n",
        encoding="ascii",
    )

    rows = []
    for start in range(0, len(image), 12):
        row = ", ".join(f"0x{value:02X}" for value in image[start : start + 12])
        rows.append(f"    {row},")

    output_c.write_text(
        f'#include "{header_name}"\n\n'
        "const uint8_t g_v3f_profile_flash_image[] = {\n"
        + "\n".join(rows)
        + "\n};\n\n"
        f"const uint32_t g_v3f_profile_flash_image_size = {len(image)}U;\n",
        encoding="ascii",
    )


def _require_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _require_int(parent: dict[str, Any], key: str) -> int:
    value = parent.get(key)
    if not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _require_bool(parent: dict[str, Any], key: str) -> bool:
    value = parent.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _require_pm(name: str, value: int) -> None:
    if not (0 <= value <= 1000):
        raise ValueError(f"{name} out of 0..1000 range")
