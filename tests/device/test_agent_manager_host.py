import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tests" / "device" / "test_agent_manager_host.c"


def find_compiler():
    for name in ("gcc", "clang", "cc"):
        path = shutil.which(name)
        if path:
            return name, path
    cl = shutil.which("cl")
    if cl:
        return "cl", cl
    return None, None


def test_agent_manager_host_parser(tmpdir):
    compiler, compiler_path = find_compiler()
    if not compiler_path:
        pytest.skip("No host C compiler found; install gcc, clang, or MSVC cl to run device C tests")

    exe = Path(str(tmpdir)) / ("agent_manager_host_test.exe" if os.name == "nt" else "agent_manager_host_test")

    if compiler == "cl":
        cmd = [
            compiler_path,
            "/nologo",
            "/std:c11",
            "/W4",
            str(SOURCE),
            f"/Fe:{exe}",
        ]
    else:
        cmd = [
            compiler_path,
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(SOURCE),
            "-o",
            str(exe),
        ]

    compile_result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if compile_result.returncode != 0:
        stderr = compile_result.stderr or ""
        if os.name == "nt" and "default-manifest.o" in stderr and "No such file" in stderr:
            pytest.skip("MinGW linker cannot run from the current Conda path containing spaces")
        raise AssertionError(stderr)

    result = subprocess.run([str(exe)], cwd=str(ROOT), text=True, capture_output=True, check=True)
    assert "agent_manager host tests passed" in result.stdout
