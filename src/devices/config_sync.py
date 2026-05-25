"""Transport-independent device profile/config synchronization."""

import base64
import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Tuple, Union

from keyboard.compiler import compile_profile_for_device
from keyboard.profile import Profile, ProfileValidationError

from .device_transport import DeviceCapabilities, DeviceFrame, DeviceTransport, DeviceTransportError
from .protocol_codec import DeviceProtocolCodec

PROFILE_SYNC_BEGIN = "PROFILE_SYNC_BEGIN"
PROFILE_SYNC_CHUNK = "PROFILE_SYNC_CHUNK"
PROFILE_SYNC_END = "PROFILE_SYNC_END"
PROFILE_SYNC_FRAME_TYPES = (PROFILE_SYNC_BEGIN, PROFILE_SYNC_CHUNK, PROFILE_SYNC_END)


@dataclass(frozen=True)
class ConfigSyncResult:
    status: str
    profile_id: str
    version: int
    frames: Tuple[DeviceFrame, ...]
    chunks: int
    checksum: str
    committed: bool
    error_code: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class ConfigSyncSimulator:
    accept: bool = True
    error_code: str = "CONFIG_SYNC_REJECTED"
    message: str = "Config sync rejected by simulator policy"

    def __call__(
        self,
        profile: Profile,
        capabilities: DeviceCapabilities,
        frames: Iterable[DeviceFrame],
    ) -> bool:
        return self.accept


AcceptPolicy = Union[
    bool,
    ConfigSyncSimulator,
    Callable[[Profile, DeviceCapabilities, Tuple[DeviceFrame, ...]], Union[bool, Any]],
]


def build_profile_sync_frames(
    profile: Profile,
    capabilities: DeviceCapabilities,
    codec: Optional[DeviceProtocolCodec] = None,
) -> Tuple[DeviceFrame, ...]:
    """Compile and chunk a profile into PROFILE_SYNC_* frames."""

    codec = codec or DeviceProtocolCodec()
    _validate_config_sync_capabilities(capabilities)
    compiled = compile_profile_for_device(profile, capabilities)
    _validate_compiled_payload_features(compiled, capabilities)

    payload_bytes = json.dumps(
        compiled,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    checksum = hashlib.sha256(payload_bytes).hexdigest()
    chunks = _chunk_payload_bytes(payload_bytes, profile, capabilities, codec, checksum)

    begin = codec.encode_message(
        PROFILE_SYNC_BEGIN,
        {
            "profile_id": profile.id,
            "version": profile.version,
            "checksum": checksum,
            "total_bytes": len(payload_bytes),
            "total_chunks": len(chunks),
        },
        device_id=capabilities.device_id,
    )
    chunk_frames = tuple(
        codec.encode_message(
            PROFILE_SYNC_CHUNK,
            _chunk_envelope(profile, checksum, index, len(chunks), chunk),
            device_id=capabilities.device_id,
        )
        for index, chunk in enumerate(chunks)
    )
    end = codec.encode_message(
        PROFILE_SYNC_END,
        {
            "profile_id": profile.id,
            "version": profile.version,
            "checksum": checksum,
            "total_chunks": len(chunks),
        },
        device_id=capabilities.device_id,
    )
    frames = (begin, *chunk_frames, end)
    _validate_frame_payload_sizes(frames, capabilities)
    return frames


class DeviceConfigSyncService:
    def __init__(self, codec: Optional[DeviceProtocolCodec] = None):
        self.codec = codec or DeviceProtocolCodec()

    async def sync_profile(
        self,
        profile: Profile,
        transport: DeviceTransport,
        accept: AcceptPolicy = True,
    ) -> ConfigSyncResult:
        capabilities = transport.get_capabilities()
        frames = build_profile_sync_frames(profile, capabilities, self.codec)
        checksum = _checksum_from_begin(self.codec.decode_message(frames[0]))
        chunk_count = sum(1 for frame in frames if frame.frame_type == PROFILE_SYNC_CHUNK)

        for frame in frames:
            await transport.send_frame(frame)

        accepted = await _resolve_accept_policy(accept, profile, capabilities, frames)
        if accepted:
            marker = getattr(transport, "mark_profile_synced", None)
            if callable(marker):
                marker(profile.id, profile.version, checksum)
            return ConfigSyncResult(
                status="committed",
                profile_id=profile.id,
                version=profile.version,
                frames=frames,
                chunks=chunk_count,
                checksum=checksum,
                committed=True,
            )

        return ConfigSyncResult(
            status="rejected",
            profile_id=profile.id,
            version=profile.version,
            frames=frames,
            chunks=chunk_count,
            checksum=checksum,
            committed=False,
            error_code=getattr(accept, "error_code", "CONFIG_SYNC_REJECTED"),
            message=getattr(accept, "message", "Config sync rejected by simulator policy"),
        )


def _validate_config_sync_capabilities(capabilities: DeviceCapabilities) -> None:
    if not capabilities.supports_config_sync:
        raise DeviceTransportError(
            code="CONFIG_SYNC_UNSUPPORTED",
            message="Device does not support config sync",
            transport_kind=capabilities.transport_kind,
            device_id=capabilities.device_id,
            recoverable=True,
        )
    missing = [
        frame_type
        for frame_type in PROFILE_SYNC_FRAME_TYPES
        if frame_type not in capabilities.supported_message_types
    ]
    if missing:
        raise DeviceTransportError(
            code="CONFIG_SYNC_UNSUPPORTED",
            message=f"Device does not support config sync frame types: {', '.join(missing)}",
            transport_kind=capabilities.transport_kind,
            device_id=capabilities.device_id,
            recoverable=True,
            details={"missing_frame_types": missing},
        )


def _validate_compiled_payload_features(compiled: dict, capabilities: DeviceCapabilities) -> None:
    supported = set(capabilities.supported_profile_features or set())
    offline = compiled.get("offline") or {}
    required = set()
    if offline.get("hid"):
        required.add("hid")
    if offline.get("layers"):
        required.add("layers")
    if offline.get("macros"):
        required.add("macros")
    if offline.get("lighting") is not None:
        required.add("lighting")
    if compiled.get("service_required_actions"):
        required.add("agent_bindings")

    for action in _iter_compiled_actions(offline):
        action_type = str(action.get("type", ""))
        if action_type.startswith("profile."):
            required.add("profiles")
        elif action_type.startswith("screen."):
            required.add("screen")
        elif action_type.startswith("device."):
            required.add("device")

    missing = sorted(required - supported)
    if missing:
        raise ProfileValidationError(
            f"device capability missing profile feature: {', '.join(missing)}"
        )


def _iter_compiled_actions(offline: dict) -> Iterable[dict]:
    for action in (offline.get("keymap") or {}).values():
        if isinstance(action, dict):
            yield action
    for layer in offline.get("layers") or []:
        for action in (layer.get("keymap") or {}).values():
            if isinstance(action, dict):
                yield action


def _chunk_payload_bytes(
    payload_bytes: bytes,
    profile: Profile,
    capabilities: DeviceCapabilities,
    codec: DeviceProtocolCodec,
    checksum: str,
) -> Tuple[bytes, ...]:
    max_payload_size = capabilities.max_payload_size
    max_candidate = min(max_payload_size, max(1, len(payload_bytes)))
    for chunk_size in range(max_candidate, 0, -1):
        chunks = tuple(
            payload_bytes[offset:offset + chunk_size]
            for offset in range(0, len(payload_bytes), chunk_size)
        ) or (b"",)
        if all(
            len(
                codec.encode_message(
                    PROFILE_SYNC_CHUNK,
                    _chunk_envelope(profile, checksum, index, len(chunks), chunk),
                    device_id=capabilities.device_id,
                ).payload
            ) <= max_payload_size
            for index, chunk in enumerate(chunks)
        ):
            return chunks
    raise DeviceTransportError(
        code="CONFIG_SYNC_PAYLOAD_TOO_LARGE",
        message="Profile sync chunk envelope cannot fit within max_payload_size",
        transport_kind=capabilities.transport_kind,
        device_id=capabilities.device_id,
        frame_type=PROFILE_SYNC_CHUNK,
        recoverable=True,
    )


def _chunk_envelope(
    profile: Profile,
    checksum: str,
    index: int,
    total_chunks: int,
    chunk: bytes,
) -> dict:
    return {
        "profile_id": profile.id,
        "version": profile.version,
        "chunk_index": index,
        "total_chunks": total_chunks,
        "checksum": checksum,
        "data_b64": base64.b64encode(chunk).decode("ascii"),
    }


def _validate_frame_payload_sizes(
    frames: Tuple[DeviceFrame, ...],
    capabilities: DeviceCapabilities,
) -> None:
    for frame in frames:
        if len(frame.payload) <= capabilities.max_payload_size:
            continue
        raise DeviceTransportError(
            code="CONFIG_SYNC_PAYLOAD_TOO_LARGE",
            message=(
                f"{frame.frame_type} payload size {len(frame.payload)} exceeds max "
                f"{capabilities.max_payload_size}"
            ),
            transport_kind=capabilities.transport_kind,
            device_id=capabilities.device_id,
            frame_type=frame.frame_type,
            recoverable=True,
        )


def _checksum_from_begin(payload: dict) -> str:
    checksum = payload.get("checksum")
    if not isinstance(checksum, str):
        raise DeviceTransportError(
            code="CONFIG_SYNC_INVALID_MANIFEST",
            message="PROFILE_SYNC_BEGIN missing checksum",
            transport_kind="unknown",
            device_id=str(payload.get("profile_id", "unknown-device")),
            frame_type=PROFILE_SYNC_BEGIN,
            recoverable=False,
        )
    return checksum


async def _resolve_accept_policy(
    accept: AcceptPolicy,
    profile: Profile,
    capabilities: DeviceCapabilities,
    frames: Tuple[DeviceFrame, ...],
) -> bool:
    if isinstance(accept, bool):
        return accept
    result = accept(profile, capabilities, frames)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)
