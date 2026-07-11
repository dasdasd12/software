import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from keyboard.akpk import (  # noqa: E402
    ProfileCompileError,
    build_package,
    canonical_json_bytes,
    crc32c,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT_DIR / "config" / "factory_default_profile.json"

PKG_HEADER_SIZE = 68
RT_HEADER_SIZE = 108
RT_SECTION_COUNT = 13


def _default_profile():
    return json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))


def test_crc32c_check_value():
    assert crc32c(b"123456789") == 0xE3069283


def test_package_header_and_crc_roundtrip():
    result = build_package(_default_profile())
    pkg = result.package

    magic, version, header_size, section_count = struct.unpack_from(
        "<4sHHH", pkg, 0
    )
    assert magic == b"AKPK"
    assert version == 1
    assert header_size == PKG_HEADER_SIZE
    assert section_count == 3

    total_size, package_crc = struct.unpack_from("<II", pkg, 60)
    assert total_size == len(pkg)
    zeroed = pkg[:64] + b"\x00\x00\x00\x00" + pkg[68:]
    assert crc32c(zeroed) == package_crc

    source_hash = pkg[28:60]
    assert source_hash == hashlib.sha256(result.canonical_source).digest()


def test_package_sections_sorted_and_checksummed():
    pkg = build_package(_default_profile()).package
    section_count = struct.unpack_from("<H", pkg, 8)[0]
    kinds = []
    for i in range(section_count):
        kind, encoding, _flags, offset, length, section_crc = (
            struct.unpack_from("<HBBIII", pkg, PKG_HEADER_SIZE + i * 16)
        )
        kinds.append(kind)
        assert offset % 4 == 0
        assert offset + length <= len(pkg)
        assert crc32c(pkg[offset:offset + length]) == section_crc
        assert encoding in (0x01, 0x02)
    assert kinds == sorted(kinds)
    assert kinds == [0x0001, 0x0002, 0x0005]


def test_runtime_table_layout():
    result = build_package(_default_profile())
    table = result.runtime_table

    (magic, table_version, abi_version, ir_version, schema_version,
     header_size, section_count, index_width, alignment) = (
        struct.unpack_from("<4sHHHHHHBB", table, 0)
    )
    assert magic == b"AKRT"
    assert table_version == 1
    assert abi_version == 1
    assert ir_version == 1
    assert schema_version == 1
    assert header_size == RT_HEADER_SIZE
    assert section_count == RT_SECTION_COUNT
    assert index_width == 2
    assert alignment == 4

    total_size, table_crc = struct.unpack_from(
        "<II", table, RT_HEADER_SIZE - 8
    )
    assert total_size == len(table)
    zeroed = table[:RT_HEADER_SIZE - 4] + b"\x00\x00\x00\x00" + \
        table[RT_HEADER_SIZE:]
    assert crc32c(zeroed) == table_crc

    counts = {}
    for i in range(RT_SECTION_COUNT):
        kind, entry_size, entry_count, offset, length, section_crc = (
            struct.unpack_from("<HHIIII", table, RT_HEADER_SIZE + i * 20)
        )
        counts[kind] = (entry_size, entry_count)
        if entry_count:
            assert length == entry_size * entry_count
            assert crc32c(table[offset:offset + length]) == section_crc

    assert counts[0x0001] == (16, 79)   # control_index_map
    assert counts[0x0002] == (20, 77)   # trigger_table
    assert counts[0x0007] == (28, 77 * 6)  # mutable_param_slots
    assert counts[0x0009] == (12, 1)    # scope_table (base only)
    assert counts[0x0008] == (32, 1)    # resource_limits
    # 76 key bindings (key_038 is no_op) + 10 local bindings
    assert counts[0x0004] == (16, 86)   # dispatch_table
    assert counts[0x0005][0] == 20      # behavior_table entry size
    assert counts[0x0003] == (16, 0)    # interaction_table empty
    assert counts[0x0006] == (1, 0)     # macro_bytecode empty


def test_canonical_json_is_deterministic_and_integer_only():
    profile = _default_profile()
    a = canonical_json_bytes(profile)
    b = canonical_json_bytes(json.loads(json.dumps(profile)))
    assert a == b

    with pytest.raises(ProfileCompileError):
        canonical_json_bytes({"x": 1.5})


def test_profile_id16_matches_crc32c_truncation():
    result = build_package(_default_profile())
    assert result.profile_id16 == (crc32c(b"factory_default") & 0xFFFF)


def test_missing_revision_defaults_with_warning():
    profile = _default_profile()
    del profile["identity"]["revision"]
    result = build_package(profile)
    assert result.revision == 1
    assert any("revision" in w for w in result.warnings)


def test_unknown_binding_target_rejected():
    profile = _default_profile()
    profile["binding_scopes"]["base"]["bindings"]["key_000"] = \
        "keyboard.not_a_key"
    with pytest.raises(ProfileCompileError):
        build_package(profile)


def test_unknown_control_rejected():
    profile = _default_profile()
    profile["binding_scopes"]["base"]["bindings"]["key_999"] = "keyboard.a"
    with pytest.raises(ProfileCompileError):
        build_package(profile)


def test_per_key_assignment_overrides_defaults():
    profile = _default_profile()
    profile["control_assignments"].append({
        "controls": ["key_000"],
        "type": "akey",
        "mode": "normal",
        "params": {
            "defaults": True,
            "press_threshold_norm_i16": 600,
            "release_threshold_norm_i16": 500,
        },
    })
    result = build_package(profile)
    table = result.runtime_table

    # locate mutable_param_slots
    for i in range(RT_SECTION_COUNT):
        kind, entry_size, entry_count, offset, _length, _crc = (
            struct.unpack_from("<HHIIII", table, RT_HEADER_SIZE + i * 20)
        )
        if kind == 0x0007:
            break
    assert kind == 0x0007

    # key_000 owns the first 6 slots; press threshold is param_order 0
    slot0 = struct.unpack_from("<HHHHHHBBBBiii", table, offset)
    assert slot0[4] == 0x0003          # param_id press_threshold
    assert slot0[12] == 600            # initial_value

    # key_001 keeps the profile default
    slot6 = struct.unpack_from(
        "<HHHHHHBBBBiii", table, offset + 6 * entry_size
    )
    assert slot6[12] == 400
