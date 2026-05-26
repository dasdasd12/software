"""Application composition root for the Local Core Service."""

from .lifecycle import LocalCoreRuntime, build_runtime
from .workspace import resolve_workspace

__all__ = ["LocalCoreRuntime", "build_runtime", "resolve_workspace"]
