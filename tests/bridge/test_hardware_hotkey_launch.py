import asyncio
from pathlib import Path
import sys
import threading

import pytest
import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
BRIDGE_DIR = SRC_DIR / "bridge"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(SRC_DIR))

from session_manager import AgentType  # noqa: E402
from keyboard.global_hotkeys import WindowsF24HotkeyListener  # noqa: E402
from server import LocalCoreServiceMVP  # noqa: E402


class FakeHotkeyListener:
    def __init__(self, callback):
        self.callback = callback
        self.yes_callback = None
        self.no_callback = None
        self.started = False
        self.stopped = False

    def set_approval_callbacks(self, yes_callback, no_callback):
        self.yes_callback = yes_callback
        self.no_callback = no_callback

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def fire(self):
        self.callback()


class FakeController:
    @staticmethod
    def is_available():
        return True


class FakeForegroundLauncher:
    def __init__(self):
        self.launches = []

    def launch(
        self,
        agent,
        workspace,
        foreground_launch_id=None,
        **kwargs,
    ):
        self.launches.append(
            (agent, workspace, foreground_launch_id, kwargs)
        )

        class Process:
            pid = None

        return Process()


class FakeKernel32:
    @staticmethod
    def GetCurrentThreadId():
        return 1234


class FakeUser32:
    def __init__(self, *, block_startup=False):
        self.block_startup = block_startup
        self.startup_gate = threading.Event()
        self.quit_event = threading.Event()
        self.post_succeeds = True
        self.registered = False
        self.register_count = 0

    def PeekMessageW(self, *_args):
        if self.block_startup:
            self.startup_gate.wait(timeout=1.0)
        return 1

    def RegisterHotKey(self, *_args):
        self.quit_event.clear()
        self.registered = True
        self.register_count += 1
        return 1

    def UnregisterHotKey(self, *_args):
        self.registered = False
        return 1

    def GetMessageW(self, *_args):
        self.quit_event.wait(timeout=1.0)
        return 0

    def PostThreadMessageW(self, *_args):
        if not self.post_succeeds:
            return 0
        self.startup_gate.set()
        self.quit_event.set()
        return 1


def _config():
    path = ROOT_DIR / "src" / "bridge" / "config.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["agents"]["claude"]["enabled"] = False
    config["agents"]["codex"]["enabled"] = False
    config["persistence"]["enabled"] = False
    config["logging"]["console"] = False
    config["logging"]["file"] = ""
    config["hardware_hotkeys"] = {
        "enabled": True,
        "launch_cooldown_ms": 500,
    }
    return config


def test_windows_listener_reserves_hid_f24_without_modifiers():
    assert WindowsF24HotkeyListener.VK_F22 == 0x85
    assert WindowsF24HotkeyListener.VK_F23 == 0x86
    assert WindowsF24HotkeyListener.VK_F24 == 0x87
    assert WindowsF24HotkeyListener.MOD_NOREPEAT == 0x4000


def test_windows_listener_registers_yes_no_and_launch_hotkeys():
    user32 = FakeUser32()
    listener = WindowsF24HotkeyListener(
        lambda: None,
        lambda: None,
        lambda: None,
        user32=user32,
        kernel32=FakeKernel32(),
        startup_timeout_sec=0.2,
        stop_timeout_sec=0.2,
    )

    listener.start()
    assert user32.register_count == 3
    listener.stop()
    assert user32.registered is False


def test_hardware_f24_dispatches_one_structured_claude_launch():
    listeners = []

    def listener_factory(callback):
        listener = FakeHotkeyListener(callback)
        listeners.append(listener)
        return listener

    service = LocalCoreServiceMVP(
        _config(),
        hardware_hotkey_listener_factory=listener_factory,
    )
    launcher = FakeForegroundLauncher()
    service.agents[AgentType.CLAUDE] = FakeController()
    service.agent_runtime.controllers = service.agents
    service.agent_commands._foreground_cli_launcher = launcher
    broadcasts = []
    service._broadcast_core_events = lambda events: broadcasts.extend(events)

    async def run():
        service._start_hardware_hotkeys(asyncio.get_running_loop())
        assert listeners[0].started is True
        assert listeners[0].yes_callback is not None
        assert listeners[0].no_callback is not None

        listeners[0].fire()
        await asyncio.sleep(0.02)
        listeners[0].fire()
        await asyncio.sleep(0.02)

        service._stop_hardware_hotkeys()
        assert listeners[0].stopped is True

    asyncio.run(run())

    assert len(launcher.launches) == 1
    agent, _workspace, launch_id, kwargs = launcher.launches[0]
    assert agent == "claude"
    assert launch_id.startswith("fg_")
    assert kwargs["native_cli"] is True
    assert kwargs["permission_mode"] == "default"
    assert [event.type for event in broadcasts] == ["agent.cli.launched"]


def test_listener_start_timeout_cleans_up_worker_before_raising():
    user32 = FakeUser32(block_startup=True)
    listener = WindowsF24HotkeyListener(
        lambda: None,
        user32=user32,
        kernel32=FakeKernel32(),
        startup_timeout_sec=0.02,
        stop_timeout_sec=0.2,
    )

    with pytest.raises(Exception, match="timed out"):
        listener.start()

    assert listener.is_running is False
    assert user32.registered is False
    assert user32.register_count == 0


def test_listener_stop_failure_preserves_live_thread_for_retry():
    user32 = FakeUser32()
    listener = WindowsF24HotkeyListener(
        lambda: None,
        user32=user32,
        kernel32=FakeKernel32(),
        startup_timeout_sec=0.2,
        stop_timeout_sec=0.05,
    )
    listener.start()
    assert listener.is_running is True
    assert user32.registered is True

    user32.post_succeeds = False
    with pytest.raises(OSError):
        listener.stop()
    assert listener.is_running is True
    assert user32.registered is True

    user32.post_succeeds = True
    listener.stop()
    assert listener.is_running is False
    assert user32.registered is False
