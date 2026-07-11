import json
import struct
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from keyboard.factory_profile_runtime import (  # noqa: E402
    FACTORY_RUNTIME_MAGIC,
    FACTORY_RUNTIME_SIZE,
    compile_factory_profile,
    crc16_ccitt,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT_DIR / "config" / "factory_default_profile.json"


def _default_profile():
    return json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))


def _output_at(image, key_id):
    base = 30 + (key_id * 2)
    return image[base], image[base + 1]


def test_default_factory_profile_compiles_to_compact_flash_runtime():
    result = compile_factory_profile(_default_profile())

    assert len(result.image) == FACTORY_RUNTIME_SIZE
    assert result.image[:4] == FACTORY_RUNTIME_MAGIC
    assert struct.unpack_from("<H", result.image, 4)[0] == FACTORY_RUNTIME_SIZE
    assert struct.unpack_from("<H", result.image, 6)[0] == 0
    assert struct.unpack_from("<HHHHHHHBBBB", result.image, 8) == (
        490,
        330,
        400,
        350,
        100,
        100,
        20,
        0,
        1,
        0,
        0,
    )
    assert struct.unpack_from("<HH", result.image, 26) == (8000, 1000)
    assert result.image[184] == 0
    assert result.image[185] == 0
    assert struct.unpack_from("<H", result.image, 186)[0] == crc16_ccitt(result.image[:186])


def test_default_factory_profile_compiles_keyboard_bindings_to_hid_outputs():
    result = compile_factory_profile(_default_profile())

    assert _output_at(result.image, 0) == (0x45, 0)  # keyboard.f12
    assert _output_at(result.image, 21) == (0x1C, 0)  # keyboard.y
    assert _output_at(result.image, 36) == (0, 0x10)  # keyboard.right_ctrl
    assert _output_at(result.image, 38) == (0, 0)  # no_op
    assert _output_at(result.image, 75) == (0, 0x08)  # keyboard.left_gui


def test_default_factory_profile_manifest_reports_non_keyboard_controls_as_unsupported():
    result = compile_factory_profile(_default_profile())

    assert result.manifest["profile_id"] == "factory_default"
    assert result.manifest["key_count"] == 77
    assert result.manifest["local_assignment_count"] == 0
    assert set(result.manifest["warnings"]) == {
        "binding fiveway_000.ccw_step targets unsupported mouse.wheel_down",
        "binding fiveway_000.cw_step targets unsupported mouse.wheel_up",
        "binding fiveway_000.down targets unsupported keyboard.down",
        "binding fiveway_000.left targets unsupported keyboard.left",
        "binding fiveway_000.press targets unsupported keyboard.enter",
        "binding fiveway_000.right targets unsupported keyboard.right",
        "binding fiveway_000.up targets unsupported keyboard.up",
        "binding enc_000.ccw_step targets unsupported consumer.volume_decrement",
        "binding enc_000.cw_step targets unsupported consumer.volume_increment",
        "binding enc_000.press targets unsupported consumer.mute",
    }


def test_compiler_rejects_assignment_params_that_are_not_defaults_only():
    profile = _default_profile()
    profile["control_assignments"][0]["params"] = {"press_delta_norm_i16": 80}

    with pytest.raises(ValueError, match="non-default assignment params"):
        compile_factory_profile(profile)
