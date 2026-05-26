from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(SRC_DIR))

from app.workspace import resolve_workspace  # noqa: E402


def test_resolve_workspace_prefers_cli_over_env_and_auto_root(tmpdir):
    tmp_path = Path(str(tmpdir))
    project_root = tmp_path / "repo"
    start = project_root / "software" / "service"
    start.mkdir(parents=True)
    env_workspace = tmp_path / "env-workspace"
    env_workspace.mkdir()
    cli_workspace = tmp_path / "cli-workspace"
    cli_workspace.mkdir()

    assert resolve_workspace(
        cli_workspace=str(cli_workspace),
        env={"AI_KEYB_WORKSPACE": str(env_workspace)},
        start=start,
    ) == cli_workspace.resolve()


def test_resolve_workspace_uses_env_before_auto_root(tmpdir):
    tmp_path = Path(str(tmpdir))
    project_root = tmp_path / "repo"
    start = project_root / "software" / "service"
    start.mkdir(parents=True)
    env_workspace = tmp_path / "env-workspace"
    env_workspace.mkdir()

    assert resolve_workspace(
        env={"AI_KEYB_WORKSPACE": str(env_workspace)},
        start=start,
    ) == env_workspace.resolve()


def test_resolve_workspace_auto_finds_parent_containing_software(tmpdir):
    tmp_path = Path(str(tmpdir))
    project_root = tmp_path / "repo"
    start = project_root / "software" / "service"
    start.mkdir(parents=True)

    assert resolve_workspace(start=start) == project_root.resolve()


def test_resolve_workspace_falls_back_to_start_when_no_project_root(tmpdir):
    tmp_path = Path(str(tmpdir))
    start = tmp_path / "service"
    start.mkdir()

    assert resolve_workspace(start=start) == start.resolve()


def test_resolve_workspace_relative_config_default_resolves_against_start(tmpdir):
    tmp_path = Path(str(tmpdir))
    start = tmp_path / "service"
    start.mkdir()

    assert resolve_workspace(
        env={},
        start=start,
        config_default="workspace",
    ) == (start / "workspace").resolve()


def test_resolve_workspace_relative_cli_and_env_resolve_against_start(tmpdir):
    tmp_path = Path(str(tmpdir))
    start = tmp_path / "service"
    start.mkdir()

    assert resolve_workspace(
        cli_workspace="cli",
        env={"AI_KEYB_WORKSPACE": "env"},
        start=start,
    ) == (start / "cli").resolve()
    assert resolve_workspace(
        env={"AI_KEYB_WORKSPACE": "env"},
        start=start,
    ) == (start / "env").resolve()
