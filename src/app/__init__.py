"""Application composition root for the Local Core Service."""

from .lifecycle import LocalCoreRuntime, build_runtime

__all__ = ["LocalCoreRuntime", "build_runtime"]
