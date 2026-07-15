"""PyInstaller entry point for the KIIIe desktop JSONL service.

Keeping this tiny wrapper outside ``src`` lets PyInstaller import
``desktop_bridge`` as a package, so its relative imports behave exactly like
the development command ``python -m desktop_bridge``.
"""

from desktop_bridge.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
