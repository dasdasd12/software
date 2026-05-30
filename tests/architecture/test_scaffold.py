import json
import sqlite3
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from agents import AgentInstance, AgentProvider, AgentRegistry, AgentRun, AgentSession, RunState  # noqa: E402
from app import build_runtime  # noqa: E402
from core import CommandEnvelope, CommandSource, EventEnvelope  # noqa: E402
from devices import DeviceProtocolCodec, DeviceSlotMapper, SimulatedTransport  # noqa: E402
from diagnostics import HealthCheck, HealthReporter, HealthStatus  # noqa: E402
from keyboard import (  # noqa: E402
    AgentBinding,
    BindingTrigger,
    KeyboardAction,
    Profile,
    ProfileValidationError,
    validate_profile,
)
from local_api import LocalApiEnvelope  # noqa: E402
from persistence import Migration, SQLiteMigrationManager  # noqa: E402
from security import (  # noqa: E402
    ApprovalMode,
    ApprovalPolicy,
    ApprovalPolicyEngine,
    ClientIdentity,
    ClientKind,
    PolicyDecision,
    RiskLevel,
)


def test_runtime_builds_snapshot_and_routes_events():
    runtime = build_runtime()
    event = runtime.command_router.dispatch(CommandEnvelope(
        command_id="cmd_test",
        type="system.snapshot.request",
        source=CommandSource(kind="test-client", client_id="pytest"),
    ))
    snapshot = runtime.snapshot()

    assert event.seq == 1
    assert event.payload["command_id"] == "cmd_test"
    assert event.type == "system.snapshot.generated"
    assert snapshot.last_event_seq == 1
    assert set(snapshot.to_dict().keys()) == {
        "snapshot_id",
        "last_event_seq",
        "agents",
        "sessions",
        "runs",
        "devices",
        "focus",
        "profiles",
        "notifications",
        "permissions",
        "interactions",
        "active_tools",
    }


def test_runtime_applies_command_event_to_state_store_before_publish():
    runtime = build_runtime()

    event = runtime.command_router.dispatch(CommandEnvelope(
        command_id="cmd_notify",
        type="notification.create",
        source=CommandSource(kind="test-client", client_id="pytest"),
        payload={"notification_id": "note_1", "level": "info", "message": "Ready"},
    ))
    snapshot = runtime.snapshot().to_dict()

    assert event.type == "notification.created"
    assert event.seq == 1
    assert snapshot["last_event_seq"] == 1
    assert snapshot["notifications"] == [
        {"notification_id": "note_1", "level": "info", "message": "Ready"}
    ]


def test_agent_registry_enforces_identity_hierarchy():
    registry = AgentRegistry()
    registry.register_provider(AgentProvider(
        provider_id="codex",
        display_name="Codex",
        capabilities=["streaming_output", "interrupt"],
    ))
    registry.register_instance(AgentInstance(
        instance_id="codex-software",
        provider_id="codex",
        label="Codex Software",
        role="software_developer",
        workspace="software",
        executable="codex",
    ))
    registry.add_session(AgentSession(
        session_id="sess_01",
        provider_id="codex",
        instance_id="codex-software",
        title="Build scaffold",
        workspace="software",
    ))
    registry.add_run(AgentRun(
        run_id="run_01",
        provider_id="codex",
        instance_id="codex-software",
        session_id="sess_01",
        state=RunState.RUNNING,
    ))

    assert registry.get_session_instance("sess_01").label == "Codex Software"
    assert registry.sessions["sess_01"].active_run_id == "run_01"


def test_policy_engine_keeps_keyboard_approvals_bounded():
    engine = ApprovalPolicyEngine()
    policy = ApprovalPolicy(policy_id="policy_standard", mode=ApprovalMode.APPROVE_LOW_RISK)
    keyboard = ClientIdentity(
        kind=ClientKind.DEVICE_TRANSPORT,
        client_id="kbd_01",
        capabilities={"agent.permission.respond"},
    )

    low = engine.evaluate(policy, RiskLevel.LOW, keyboard)
    high = engine.evaluate(policy, RiskLevel.HIGH, keyboard)

    assert low.decision == PolicyDecision.ALLOW
    assert high.decision == PolicyDecision.REQUIRE_DESKTOP_CONFIRM


def test_profile_validation_accepts_agent_binding_and_rejects_unknown_layer():
    binding = AgentBinding(
        id="approve_focused",
        trigger=BindingTrigger(source="key", key="K_ENTER", event="press", layer="layer_fn"),
        action=KeyboardAction(
            type="agent.permission.respond",
            target="focused_permission",
            payload={"decision": "approve"},
        ),
        safety={"allow_high_risk": False},
    )
    profile = Profile(
        id="profile_coding_default",
        name="Coding",
        target_device_family="ai_keyboard_ch32h417",
        layers=[{"id": "layer_fn", "name": "Fn"}],
        agent_bindings=[binding],
    )

    validate_profile(profile)
    assert profile.to_dict()["agent_bindings"][0]["action"]["target"] == "focused_permission"

    invalid = Profile(
        id="bad",
        name="Bad",
        target_device_family="ai_keyboard_ch32h417",
        agent_bindings=[binding],
    )
    with pytest.raises(ProfileValidationError):
        validate_profile(invalid)


def test_device_codec_slot_mapper_and_manager_boundary():
    transport = SimulatedTransport(device_id="kbd_01")
    runtime = build_runtime()
    record = runtime.device_manager.register_transport(transport)
    codec = DeviceProtocolCodec()
    mapper = DeviceSlotMapper(device_id="kbd_01")

    session_slot = mapper.assign_session("sess_01")
    frame = codec.encode_message(
        frame_type="SCREEN_FOCUS_SET",
        payload={"session_slot_id": session_slot},
        device_id="kbd_01",
        generation=mapper.generation,
    )

    assert record.device_id == "kbd_01"
    assert codec.decode_message(frame) == {"session_slot_id": session_slot}
    assert mapper.resolve_session(session_slot, mapper.generation) == "sess_01"
    with pytest.raises(ValueError):
        mapper.resolve_session(session_slot, mapper.generation - 1)


def test_local_api_envelope_round_trip_does_not_use_device_frame_shape():
    raw = LocalApiEnvelope(
        type="list_sessions",
        payload={"agent": "all"},
    ).to_json()
    parsed = LocalApiEnvelope.from_json(raw)

    assert json.loads(raw) == {"type": "list_sessions", "agent": "all"}
    assert parsed.type == "list_sessions"
    assert parsed.payload == {"agent": "all"}


def test_sqlite_migration_manager_records_applied_versions(tmpdir):
    db_path = Path(str(tmpdir)) / "app.db"
    manager = SQLiteMigrationManager(db_path)
    manager.apply([
        Migration(
            version=1,
            description="create profile table",
            sql="CREATE TABLE profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL);",
        )
    ])
    manager.apply([
        Migration(
            version=1,
            description="create profile table",
            sql="CREATE TABLE profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL);",
        )
    ])

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT version, description FROM schema_migrations").fetchall()

    assert rows == [(1, "create profile table")]


def test_health_reporter_summarizes_worst_status():
    reporter = HealthReporter()
    reporter.record(HealthCheck(name="local_api", status=HealthStatus.OK))
    reporter.record(HealthCheck(name="agent", status=HealthStatus.WARNING, message="Codex unavailable"))

    summary = reporter.summarize()

    assert summary["status"] == "warning"
    assert len(summary["checks"]) == 2
