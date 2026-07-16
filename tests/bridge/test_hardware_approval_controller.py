import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
BRIDGE_DIR = SRC_DIR / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(SRC_DIR))

from core import CommandEnvelope, CommandSource  # noqa: E402
from devices.transports.serial_cdc import DeviceError  # noqa: E402
from security import (  # noqa: E402
    CAP_PERMISSION_RESPOND,
    ClientIdentity,
    ClientKind,
    RiskLevel,
)
from server import (  # noqa: E402
    LocalCoreServiceMVP,
    PendingClaudeHookDecision,
    PendingPermission,
)
from session_manager import AgentType  # noqa: E402


class FakeApprovalSender:
    def __init__(self, *, fail_show=False):
        self.fail_show = fail_show
        self.shown = []
        self.cleared = []

    async def show(self, tag, risk, tool, summary):
        self.shown.append((tag, risk, tool, summary))
        if self.fail_show:
            raise OSError("COM port unavailable")
        return "APPROVAL"

    async def clear(self, tag):
        self.cleared.append(tag)
        return "APPROVAL"


class FlakyApprovalSender(FakeApprovalSender):
    def __init__(self):
        super().__init__()
        self.failures_remaining = 1

    async def show(self, tag, risk, tool, summary):
        self.shown.append((tag, risk, tool, summary))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise OSError("device still enumerating")
        return "APPROVAL"

    async def clear(self, tag):
        self.cleared.append(tag)
        raise DeviceError(
            3,
            "approval-inactive",
            f"AK APPROVAL CLEAR {tag}",
        )


class FlakyClearApprovalSender(FakeApprovalSender):
    def __init__(self):
        super().__init__()
        self.clear_failures_remaining = 1

    async def clear(self, tag):
        self.cleared.append(tag)
        if self.clear_failures_remaining:
            self.clear_failures_remaining -= 1
            raise OSError("COM port briefly busy")
        return "APPROVAL"


def _config():
    config = yaml.safe_load(
        (ROOT_DIR / "src" / "bridge" / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    config["agents"]["claude"]["enabled"] = False
    config["agents"]["codex"]["enabled"] = False
    config["persistence"]["enabled"] = False
    config["logging"]["console"] = False
    config["logging"]["file"] = ""
    return config


def _hardware_session(service):
    session = service.session_mgr.create(AgentType.CLAUDE)
    session.launch_surface = "foreground_cli"
    session.control_mode = "native_cli"
    service._hardware_hotkey_session_ids.add(session.session_id)
    return session


def _add_permission(
    service,
    session,
    request_id,
    *,
    created_at,
    description="python -m pytest",
):
    loop = asyncio.get_running_loop()
    created_at_value = (
        time.time() + (float(created_at) / 1000.0)
        if float(created_at) < 1_000_000_000
        else float(created_at)
    )
    pending = PendingPermission(
        request_id=request_id,
        session_id=session.session_id,
        agent=AgentType.CLAUDE,
        created_at=created_at_value,
        timeout_sec=30,
        tool="Bash",
        description=description,
        risk_level=RiskLevel.HIGH,
        native={
            "adapter": "claude_code_hook",
            "tool_input": {"command": description},
        },
    )
    key = service._pending_permission_key(
        request_id,
        session.session_id,
        None,
        None,
    )
    result_future = loop.create_future()
    delivered_future = loop.create_future()
    delivered_future.set_result({"response_written": True})
    service.pending_permissions[key] = pending
    service._claude_hook_decisions[key] = PendingClaudeHookDecision(
        request_id=request_id,
        session_id=session.session_id,
        hook_input={
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": description},
        },
        created_at=created_at_value,
        result_future=result_future,
        delivered_future=delivered_future,
    )
    return key, result_future


def test_foreground_registration_maps_and_cleans_hardware_session():
    service = LocalCoreServiceMVP(
        _config(),
        hardware_approval_sender=FakeApprovalSender(),
    )
    session = service.session_mgr.create(AgentType.CLAUDE)
    session.launch_surface = "foreground_cli"
    session.control_mode = "native_cli"
    launch_id = "fg_hardware"
    service._hardware_hotkey_launch_ids.add(launch_id)
    queue = asyncio.Queue()

    service._track_foreground_cli_session(
        CommandEnvelope(
            type="agent.session.register_foreground",
            source=CommandSource(kind="desktop-ui"),
            payload={
                "launch_surface": "foreground_cli",
                "control_mode": "native_cli",
            },
        ),
        SimpleNamespace(
            payload={
                "session_id": session.session_id,
                "foreground_launch_id": launch_id,
            }
        ),
        queue,
    )

    assert session.session_id in service._hardware_hotkey_session_ids
    assert launch_id not in service._hardware_hotkey_launch_ids

    service._track_foreground_cli_session(
        CommandEnvelope(
            type="agent.session.foreground_exited",
            source=CommandSource(kind="desktop-ui"),
        ),
        SimpleNamespace(payload={"session_id": session.session_id}),
        queue,
    )
    assert session.session_id not in service._hardware_hotkey_session_ids


def test_registration_that_beats_launch_ack_is_still_hardware_owned():
    service = LocalCoreServiceMVP(
        _config(),
        hardware_approval_sender=FakeApprovalSender(),
    )
    session = service.session_mgr.create(AgentType.CLAUDE)
    session.launch_surface = "foreground_cli"
    session.control_mode = "native_cli"
    launch_id = "fg_registered_early"
    queue = asyncio.Queue()
    service._hardware_hotkey_launch_dispatches = 1

    service._track_foreground_cli_session(
        CommandEnvelope(
            type="agent.session.register_foreground",
            source=CommandSource(kind="desktop-ui"),
            payload={
                "launch_surface": "foreground_cli",
                "control_mode": "native_cli",
            },
        ),
        SimpleNamespace(
            payload={
                "session_id": session.session_id,
                "foreground_launch_id": launch_id,
            }
        ),
        queue,
    )

    assert session.session_id not in service._hardware_hotkey_session_ids
    assert service._hardware_hotkey_early_registrations == {
        launch_id: session.session_id,
    }

    service._remember_hardware_hotkey_launch(launch_id)

    assert session.session_id in service._hardware_hotkey_session_ids
    assert launch_id not in service._hardware_hotkey_launch_ids
    assert service._hardware_hotkey_early_registrations == {}


def test_only_earliest_hardware_owned_claude_permission_is_armed():
    async def run():
        sender = FakeApprovalSender()
        service = LocalCoreServiceMVP(
            _config(),
            hardware_approval_sender=sender,
        )
        hardware_session = _hardware_session(service)
        other_session = service.session_mgr.create(AgentType.CLAUDE)
        other_session.launch_surface = "foreground_cli"
        other_session.control_mode = "native_cli"
        _add_permission(
            service,
            other_session,
            "req_not_hardware",
            created_at=0.0,
        )
        first_key, first_result = _add_permission(
            service,
            hardware_session,
            "req_first",
            created_at=1.0,
        )
        _add_permission(
            service,
            hardware_session,
            "req_second",
            created_at=2.0,
        )

        await service._refresh_hardware_approval_once()
        current = service._hardware_approval_current
        assert current is not None
        assert current.pending_key == first_key
        assert current.display.risk == 2
        assert len(sender.shown) == 1

        await service._submit_hardware_approval_decision(True)
        assert first_result.result()["hookSpecificOutput"]["decision"]["behavior"] == "allow"
        assert sender.cleared == [sender.shown[0][0]]

        await service._refresh_hardware_approval_once()
        assert service._hardware_approval_current.request_id == "req_second"
        assert len(sender.shown) == 2

    asyncio.run(run())


def test_ordinary_device_transport_still_cannot_approve_high_risk():
    async def run():
        service = LocalCoreServiceMVP(
            _config(),
            hardware_approval_sender=FakeApprovalSender(),
        )
        session = _hardware_session(service)
        pending_key, _result = _add_permission(
            service,
            session,
            "req_device_policy",
            created_at=time.time(),
        )
        pending = service.pending_permissions[pending_key]
        device_client = ClientIdentity(
            kind=ClientKind.DEVICE_TRANSPORT,
            client_id="ordinary-device",
            capabilities={CAP_PERMISSION_RESPOND},
        )

        allowed, code, _reason = (
            service._can_submit_permission_response_for_client(
                device_client,
                pending,
                True,
            )
        )

        assert allowed is False
        assert code in {"POLICY_DENIED", "REQUIRE_DESKTOP_CONFIRM"}

    asyncio.run(run())


def test_failed_show_never_arms_and_hotkey_decision_is_ignored():
    async def run():
        sender = FakeApprovalSender(fail_show=True)
        service = LocalCoreServiceMVP(
            _config(),
            hardware_approval_sender=sender,
        )
        session = _hardware_session(service)
        _key, result_future = _add_permission(
            service,
            session,
            "req_show_failed",
            created_at=1.0,
        )

        await service._refresh_hardware_approval_once()
        assert service._hardware_approval_current is None

        service._create_hardware_approval_decision_task(True)
        await asyncio.sleep(0)
        assert service._hardware_approval_decision_task is None
        assert result_future.done() is False

    asyncio.run(run())


def test_failed_show_retries_until_ok_without_arming_early():
    async def run():
        config = _config()
        config["hardware_approval"]["retry_delay_ms"] = 10
        sender = FlakyApprovalSender()
        service = LocalCoreServiceMVP(
            config,
            hardware_approval_sender=sender,
        )
        service._hardware_hotkey_loop = asyncio.get_running_loop()
        session = _hardware_session(service)
        _add_permission(
            service,
            session,
            "req_retry",
            created_at=time.time(),
        )

        service._request_hardware_approval_refresh()
        await asyncio.sleep(0)
        assert service._hardware_approval_current is None

        for _ in range(20):
            await asyncio.sleep(0.01)
            if service._hardware_approval_current is not None:
                break

        assert len(sender.shown) == 2
        assert service._hardware_approval_current.request_id == "req_retry"
        assert sender.cleared == [sender.shown[0][0]]
        service._cancel_hardware_approval_retry()

    asyncio.run(run())


def test_failed_show_is_cleared_without_reshow_after_request_expires():
    async def run():
        sender = FakeApprovalSender(fail_show=True)
        service = LocalCoreServiceMVP(
            _config(),
            hardware_approval_sender=sender,
        )
        service._hardware_hotkey_loop = asyncio.get_running_loop()
        session = _hardware_session(service)
        pending_key, _result = _add_permission(
            service,
            session,
            "req_show_expired",
            created_at=time.time(),
        )

        await service._refresh_hardware_approval_once()
        assert len(sender.shown) == 1
        uncertain_tag = sender.shown[0][0]
        assert uncertain_tag in service._hardware_approval_pending_clear_tags

        service.pending_permissions[pending_key].created_at = (
            time.time()
            - service.pending_permissions[pending_key].timeout_sec
            - 1
        )
        await service._refresh_hardware_approval_once()

        assert sender.cleared == [uncertain_tag]
        assert len(sender.shown) == 1
        assert service._hardware_approval_current is None
        assert service._hardware_approval_pending_clear_tags == set()
        assert service._hardware_approval_retry_handle is None

    asyncio.run(run())


def test_failed_clear_retries_until_old_display_is_removed():
    async def run():
        config = _config()
        config["hardware_approval"]["retry_delay_ms"] = 10
        sender = FlakyClearApprovalSender()
        service = LocalCoreServiceMVP(
            config,
            hardware_approval_sender=sender,
        )
        service._hardware_hotkey_loop = asyncio.get_running_loop()
        session = _hardware_session(service)
        _add_permission(
            service,
            session,
            "req_clear_retry",
            created_at=time.time(),
        )
        await service._refresh_hardware_approval_once()
        armed = service._hardware_approval_current
        assert armed is not None

        service._create_hardware_approval_decision_task(False)
        decision_task = service._hardware_approval_decision_task
        assert decision_task is not None
        await decision_task
        await asyncio.sleep(0)
        assert service._hardware_approval_current is None
        assert armed.display.tag8hex in service._hardware_approval_pending_clear_tags

        for _ in range(20):
            await asyncio.sleep(0.01)
            if not service._hardware_approval_pending_clear_tags:
                break

        assert sender.cleared == [
            armed.display.tag8hex,
            armed.display.tag8hex,
        ]
        assert service._hardware_approval_pending_clear_tags == set()
        service._cancel_hardware_approval_retry()

    asyncio.run(run())


def test_clear_ack_loss_errors_are_treated_as_already_cleared():
    class AckLostClearSender(FakeApprovalSender):
        def __init__(self, detail):
            super().__init__()
            self.detail = detail

        async def clear(self, tag):
            self.cleared.append(tag)
            raise DeviceError(
                3,
                self.detail,
                f"AK APPROVAL CLEAR {tag}",
            )

    async def run():
        for detail in ("approval-inactive", "approval-tag"):
            sender = AckLostClearSender(detail)
            service = LocalCoreServiceMVP(
                _config(),
                hardware_approval_sender=sender,
            )
            tag = "deadbeef"
            service._hardware_approval_pending_clear_tags.add(tag)

            assert await service._clear_hardware_approval_tag(tag) is True
            assert sender.cleared == [tag]
            assert service._hardware_approval_pending_clear_tags == set()
            assert service._hardware_approval_retry_handle is None

    asyncio.run(run())


def test_stale_or_unregistered_hook_request_cannot_be_approved():
    async def run():
        sender = FakeApprovalSender()
        service = LocalCoreServiceMVP(
            _config(),
            hardware_approval_sender=sender,
        )
        session = _hardware_session(service)
        pending_key, result_future = _add_permission(
            service,
            session,
            "req_stale",
            created_at=1.0,
        )
        await service._refresh_hardware_approval_once()
        armed = service._hardware_approval_current
        assert armed is not None

        service._claude_hook_decisions.pop(pending_key)
        await service._submit_hardware_approval_decision(False)

        assert service._hardware_approval_current is None
        assert result_future.done() is False
        assert sender.cleared == [armed.display.tag8hex]

    asyncio.run(run())
