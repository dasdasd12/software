from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from devices import DeviceCapabilities, SimulatedTransport  # noqa: E402
from diagnostics import HealthReporter, validate_profile_diagnostics  # noqa: E402
from keyboard import AgentBinding, BindingTrigger, KeyboardAction, Profile  # noqa: E402


def _capabilities(**overrides):
    data = {
        "device_id": "kbd_01",
        "transport_kind": "simulated",
        "protocol_version": 1,
        "max_payload_size": 512,
        "supported_message_types": {"PROFILE_SYNC_BEGIN", "PROFILE_SYNC_CHUNK", "PROFILE_SYNC_END"},
        "device_family": "ai_keyboard_ch32h417",
        "supported_profile_features": {"hid", "layers", "agent_bindings"},
        "supports_agent_slots": True,
        "supports_config_sync": True,
    }
    data.update(overrides)
    return DeviceCapabilities(**data)


def test_health_reporter_collects_backend_status_without_mutating_transport():
    reporter = HealthReporter()
    transport = SimulatedTransport(device_id="kbd_01")

    reporter.record_local_api(is_running=True, clients=2)
    reporter.record_database(is_connected=True, path="app.db")
    reporter.record_device_transport(transport.get_status(), transport.get_capabilities())
    reporter.record_profile_validation(
        "profile_dev",
        {"valid": False, "issues": [{"code": "profile_invalid", "message": "bad profile"}]},
    )
    reporter.record_config_sync(
        active_profile_id="profile_dev",
        active_synced_profile_id="profile_old",
        pending_changes=True,
    )

    summary = reporter.summarize()

    assert summary["status"] == "warning"
    checks = {check["name"]: check for check in summary["checks"]}
    assert checks["local_api"]["status"] == "ok"
    assert checks["database"]["details"]["path"] == "app.db"
    assert checks["device_transport"]["details"]["device_id"] == "kbd_01"
    assert checks["device_transport"]["details"]["is_open"] is False
    assert checks["profile_validation"]["status"] == "warning"
    assert checks["config_sync"]["details"]["pending_changes"] is True
    assert transport.get_status().queued_frames == 0


def test_profile_diagnostics_returns_structured_issues_for_invalid_profile():
    profile = Profile(
        id="profile_dev",
        name="Developer",
        target_device_family="ai_keyboard_ch32h417",
        layers=[{"id": "layer_fn"}],
        agent_bindings=[
            AgentBinding(
                id="approve",
                trigger=BindingTrigger(source="key", key="K_UNKNOWN", event="press", layer="layer_fn"),
                action=KeyboardAction(type="agent.permission.respond", target="focused_permission"),
            )
        ],
    )

    result = validate_profile_diagnostics(
        profile,
        device_capabilities=_capabilities(supports_config_sync=False),
        layout_keys={"K_ENTER"},
    )

    assert result["valid"] is False
    assert result["profile_id"] == "profile_dev"
    assert result["issues"] == [
        {
            "code": "profile_validation_error",
            "severity": "error",
            "message": "unknown key reference: K_UNKNOWN",
        }
    ]


def test_diagnostic_export_redacts_nested_tokens_and_api_keys():
    reporter = HealthReporter()
    reporter.record_local_api(
        is_running=True,
        clients=1,
        details={
            "token": "local-token",
            "nested": {
                "api_key": "api-key-value",
                "OPENAI_API_KEY": "openai-key-value",
                "Authorization": "Bearer secret",
                "session_token": "session-token-value",
                "x-api-key": "header-api-key-value",
                "clientSecret": "client-secret-value",
                "apiKeyValue": "api-key-suffix-value",
                "accessTokenValue": "access-token-suffix-value",
                "refreshTokenValue": "refresh-token-suffix-value",
                "sessionTokenValue": "session-token-suffix-value",
                "idTokenValue": "id-token-suffix-value",
                "bearerTokenValue": "bearer-token-suffix-value",
                "authorizationHeader": "Bearer header-secret",
                "safe": "visible",
                "token_count": 123,
                "secretariat": "visible-office",
                "monkey": "visible-animal",
            },
            "items": [{"secret": "hidden"}, {"name": "kept"}],
        },
    )

    exported = reporter.export()
    check = exported["checks"][0]

    assert check["details"]["token"] == "<redacted>"
    assert check["details"]["nested"]["api_key"] == "<redacted>"
    assert check["details"]["nested"]["OPENAI_API_KEY"] == "<redacted>"
    assert check["details"]["nested"]["Authorization"] == "<redacted>"
    assert check["details"]["nested"]["session_token"] == "<redacted>"
    assert check["details"]["nested"]["x-api-key"] == "<redacted>"
    assert check["details"]["nested"]["clientSecret"] == "<redacted>"
    assert check["details"]["nested"]["apiKeyValue"] == "<redacted>"
    assert check["details"]["nested"]["accessTokenValue"] == "<redacted>"
    assert check["details"]["nested"]["refreshTokenValue"] == "<redacted>"
    assert check["details"]["nested"]["sessionTokenValue"] == "<redacted>"
    assert check["details"]["nested"]["idTokenValue"] == "<redacted>"
    assert check["details"]["nested"]["bearerTokenValue"] == "<redacted>"
    assert check["details"]["nested"]["authorizationHeader"] == "<redacted>"
    assert check["details"]["nested"]["safe"] == "visible"
    assert check["details"]["nested"]["token_count"] == 123
    assert check["details"]["nested"]["secretariat"] == "visible-office"
    assert check["details"]["nested"]["monkey"] == "visible-animal"
    assert check["details"]["items"][0]["secret"] == "<redacted>"
    assert check["details"]["items"][1]["name"] == "kept"
