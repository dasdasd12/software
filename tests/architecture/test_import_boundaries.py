import ast
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]


def _python_files_under(*relative_roots):
    for relative_root in relative_roots:
        root = REPO_ROOT / relative_root
        if not root.exists():
            continue
        yield from sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_devices_and_keyboard_do_not_import_bridge_or_agent_proxy():
    violations = []

    for path in _python_files_under("src/devices", "src/keyboard"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden_import(alias.name):
                        violations.append((path, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported_names = [alias.name for alias in node.names]
                if _is_forbidden_import(module) or any(_is_forbidden_import(name) for name in imported_names):
                    violations.append((path, node.lineno, module, imported_names))

    assert violations == []


def test_project_sources_do_not_contain_machine_specific_absolute_paths():
    violations = []
    windows_drive = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?![\\/])[^\"'`\s]+")
    posix_home = re.compile(r"/" + "Users" + r"/[^\"'`\s]+")

    for path in _guarded_text_files():
        text = path.read_text(encoding="utf-8")
        for pattern in (windows_drive, posix_home):
            for match in pattern.finditer(text):
                violations.append((path.relative_to(REPO_ROOT).as_posix(), match.group(0)))

    assert violations == []


def _is_forbidden_import(name):
    return name == "bridge" or name.startswith("bridge.") or name == "AgentProxy"


def _guarded_text_files():
    guarded_roots = [
        "src",
        "docs/architecture",
        "scripts",
    ]
    allowed_suffixes = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
    for relative_root in guarded_roots:
        root = REPO_ROOT / relative_root
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix not in allowed_suffixes:
                continue
            yield path
