import asyncio
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices import (  # noqa: E402
    DeviceConfigSyncService,
    DeviceProtocolCodec,
    DeviceTransportError,
    SimulatedTransport,
    build_profile_sync_frames,
)
from keyboard import (  # noqa: E402
    AgentBinding,
    BindingTrigger,
    KeyboardAction,
    LightingConfig,
    LightingLayer,
    Profile,
    ProfileValidationError,
)


def _profile(**overrides):
    data = {
        "id": "profile_dev",
        "name": "Developer",
        "target_device_family": "ai_keyboard_ch32h417",
        "version": 3,
        "keymap": {
            "physical_layout_id": "ansi_75_ai_keyboard",
            "bindings": {
                "K_A": {"type": "hid.key", "usage": "KEY_A"},
                "K_MACRO_1": {"type": "macro.play", "macro_id": "build"},
            },
        },
        "layers": [
            {
                "id": "layer_fn",
                "activation": {"type": "hold_key", "key": "K_FN"},
                "keymap": {
                    "K_ENTER": {"type": "hid.key", "usage": "KEY_ENTER"},
                },
            }
        ],
        "macros": [
            {
                "id": "build",
                "sequence": [{"type": "hid.key", "usage": "KEY_B"}],
            }
        ],
        "agent_bindings": [
            AgentBinding(
                id="approve",
                trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
                action=KeyboardAction(
                    type="agent.permission.respond",
                    target="focused_permission",
                    payload={"decision": "approve"},
                ),
            )
        ],
        "lighting_config": LightingConfig(
            brightness=70,
            layers=[
                LightingLayer(
                    id="base",
                    effect="static",
                    color="#2ad4ff",
                    per_key={"K_ENTER": {"color": "#ffffff"}},
                )
            ],
        ),
    }
    data.update(overrides)
    return Profile(**data)


def _transport(**overrides):
    options = {
        "device_id": "kbd_01",
        "device_family": "ai_keyboard_ch32h417",
        "max_payload_size": 512,
        "supported_profile_features": {
            "hid",
            "layers",
            "macros",
            "lighting",
            "agent_bindings",
        },
        "supports_agent_slots": True,
        "supports_config_sync": True,
    }
    options.update(overrides)
    return SimulatedTransport(**options)


def test_config_sync_rejects_capability_mismatch_before_sending_frames():
    async def run():
        codec = DeviceProtocolCodec()
        service = DeviceConfigSyncService(codec=codec)
        transport = _transport(supports_config_sync=False)
        await transport.open()

        with pytest.raises(DeviceTransportError) as exc_info:
            await service.sync_profile(_profile(), transport)

        assert exc_info.value.code == "CONFIG_SYNC_UNSUPPORTED"
        assert transport.get_status().queued_frames == 0
        assert transport.active_synced_profile_id is None

    asyncio.run(run())


def test_config_sync_rejects_missing_profile_feature_and_family_mismatch_before_sending():
    async def run():
        service = DeviceConfigSyncService()

        missing_lighting = _transport(
            supported_profile_features={"hid", "layers", "macros", "agent_bindings"}
        )
        await missing_lighting.open()
        with pytest.raises(ProfileValidationError, match="lighting"):
            await service.sync_profile(_profile(), missing_lighting)
        assert missing_lighting.get_status().queued_frames == 0
        assert missing_lighting.active_synced_profile_id is None

        wrong_family = _transport(device_family="other_family")
        await wrong_family.open()
        with pytest.raises(ProfileValidationError, match="incompatible"):
            await service.sync_profile(_profile(), wrong_family)
        assert wrong_family.get_status().queued_frames == 0
        assert wrong_family.active_synced_profile_id is None

    asyncio.run(run())


def test_compiled_payload_is_chunked_under_device_payload_limit_in_frame_order():
    profile = _profile(
        macros=[
            {
                "id": f"macro_{index}",
                "sequence": [{"type": "hid.text", "text": "x" * 40}],
            }
            for index in range(10)
        ],
    )
    transport = _transport(max_payload_size=320)

    frames = build_profile_sync_frames(profile, transport.get_capabilities(), codec=DeviceProtocolCodec())

    assert [frames[0].frame_type, frames[-1].frame_type] == ["PROFILE_SYNC_BEGIN", "PROFILE_SYNC_END"]
    assert [frame.frame_type for frame in frames[1:-1]]
    assert {frame.frame_type for frame in frames[1:-1]} == {"PROFILE_SYNC_CHUNK"}
    assert [frame.frame_type for frame in frames] == [
        "PROFILE_SYNC_BEGIN",
        *["PROFILE_SYNC_CHUNK" for _ in frames[1:-1]],
        "PROFILE_SYNC_END",
    ]
    assert all(len(frame.payload) <= transport.get_capabilities().max_payload_size for frame in frames[1:-1])
    assert len(frames) > 3


def test_sync_profile_commits_and_updates_active_synced_profile_marker():
    async def run():
        service = DeviceConfigSyncService()
        transport = _transport()
        await transport.open()

        result = await service.sync_profile(_profile(), transport)

        assert result.status == "committed"
        assert result.committed is True
        assert result.profile_id == "profile_dev"
        assert result.version == 3
        assert result.chunks >= 1
        assert len(result.checksum) == 64
        assert [frame.frame_type for frame in result.frames][0] == "PROFILE_SYNC_BEGIN"
        assert transport.active_synced_profile_id == "profile_dev"
        assert transport.active_synced_profile_version == 3
        assert transport.active_synced_profile_checksum == result.checksum
        assert transport.get_status().queued_frames == len(result.frames)

    asyncio.run(run())


def test_sync_reject_does_not_change_active_synced_profile_marker():
    async def run():
        service = DeviceConfigSyncService()
        transport = _transport()
        await transport.open()
        committed = await service.sync_profile(_profile(version=3), transport)
        marker = (
            transport.active_synced_profile_id,
            transport.active_synced_profile_version,
            transport.active_synced_profile_checksum,
        )

        rejected = await service.sync_profile(_profile(version=4), transport, accept=False)

        assert rejected.status == "rejected"
        assert rejected.committed is False
        assert rejected.profile_id == "profile_dev"
        assert rejected.version == 4
        assert rejected.error_code == "CONFIG_SYNC_REJECTED"
        assert (
            transport.active_synced_profile_id,
            transport.active_synced_profile_version,
            transport.active_synced_profile_checksum,
        ) == marker
        assert committed.checksum != rejected.checksum

    asyncio.run(run())
