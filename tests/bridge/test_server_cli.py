from pathlib import Path
import sys


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "src" / "bridge"
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(SRC_DIR))

import server as server_module  # noqa: E402


def _event_loop_policy_helper():
    assert hasattr(server_module, "_configure_windows_event_loop_policy")
    return server_module._configure_windows_event_loop_policy


def test_configure_windows_event_loop_policy_uses_proactor_on_win32(monkeypatch):
    policies = []

    class FakePolicy:
        pass

    monkeypatch.setattr(server_module.sys, "platform", "win32")
    monkeypatch.setattr(server_module.asyncio, "WindowsProactorEventLoopPolicy", FakePolicy, raising=False)
    monkeypatch.setattr(server_module.asyncio, "set_event_loop_policy", policies.append)

    _event_loop_policy_helper()()

    assert len(policies) == 1
    assert isinstance(policies[0], FakePolicy)


def test_configure_windows_event_loop_policy_noops_off_windows(monkeypatch):
    policies = []

    class FakePolicy:
        pass

    monkeypatch.setattr(server_module.sys, "platform", "linux")
    monkeypatch.setattr(server_module.asyncio, "WindowsProactorEventLoopPolicy", FakePolicy, raising=False)
    monkeypatch.setattr(server_module.asyncio, "set_event_loop_policy", policies.append)

    _event_loop_policy_helper()()

    assert policies == []


def test_main_configures_event_loop_policy_before_asyncio_run(monkeypatch, tmpdir):
    order = []
    config_path = Path(str(tmpdir)) / "config.yaml"
    config_path.write_text(
        """
server:
  host: 127.0.0.1
  port: 8765
session:
  cache_size: 10
  cleanup_after_hours: 24
agents: {}
unifier:
  max_delta_size: 1024
""".strip(),
        encoding="utf-8",
    )

    class FakeService:
        def __init__(self, config):
            self.config = config

        async def start(self):
            return None

    def fake_configure():
        order.append("configure")

    def fake_run(coro):
        order.append("run")
        coro.close()

    monkeypatch.setattr(server_module, "_configure_windows_event_loop_policy", fake_configure)
    monkeypatch.setattr(server_module, "LocalCoreServiceMVP", FakeService)
    monkeypatch.setattr(server_module.asyncio, "run", fake_run)
    monkeypatch.setattr(server_module.sys, "argv", ["server.py", "--config", str(config_path)])

    server_module.main()

    assert order == ["configure", "run"]
