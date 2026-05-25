"""Per-device active keyboard tool state."""

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple


DEFAULT_TOOL_IDS = (
    "agent_control",
    "session_list",
    "permissions",
    "profile_config",
    "device_status",
)


@dataclass(frozen=True)
class ToolSwitchResult:
    device_id: str
    tool_id: str
    previous_tool_id: Optional[str]


class ToolStateManager:
    """Tracks the backend control tool selected by each keyboard device."""

    def __init__(
        self,
        *,
        default_tools: Iterable[str] = DEFAULT_TOOL_IDS,
        configured_tools: Optional[Mapping[str, Iterable[str]]] = None,
    ) -> None:
        self._default_tools = self._normalize_tools(default_tools)
        self._configured_tools: Dict[str, Tuple[str, ...]] = {
            device_id: self._normalize_tools(tools)
            for device_id, tools in (configured_tools or {}).items()
        }
        self._active_tools: Dict[str, str] = {}

    def switch(self, device_id: str, tool_id: str) -> ToolSwitchResult:
        if tool_id not in self.configured_tools(device_id):
            raise ValueError(f"unknown tool for device {device_id}: {tool_id}")
        previous = self._active_tools.get(device_id)
        self._active_tools[device_id] = tool_id
        return ToolSwitchResult(
            device_id=device_id,
            tool_id=tool_id,
            previous_tool_id=previous,
        )

    def next(self, device_id: str) -> ToolSwitchResult:
        tools = self.configured_tools(device_id)
        current = self._active_tools.get(device_id)
        if current in tools:
            next_index = (tools.index(current) + 1) % len(tools)
        else:
            next_index = 0
        return self.switch(device_id, tools[next_index])

    def get(self, device_id: str) -> Optional[str]:
        return self._active_tools.get(device_id)

    def all(self) -> Dict[str, str]:
        return dict(self._active_tools)

    def configured_tools(self, device_id: str) -> Tuple[str, ...]:
        return self._configured_tools.get(device_id, self._default_tools)

    def configure_device_tools(self, device_id: str, tools: Iterable[str]) -> None:
        normalized = self._normalize_tools(tools)
        active = self._active_tools.get(device_id)
        if active is not None and active not in normalized:
            self._active_tools.pop(device_id, None)
        self._configured_tools[device_id] = normalized

    @staticmethod
    def _normalize_tools(tools: Iterable[str]) -> Tuple[str, ...]:
        normalized = tuple(tool for tool in tools if isinstance(tool, str) and tool)
        if not normalized:
            raise ValueError("at least one tool id is required")
        if len(set(normalized)) != len(normalized):
            raise ValueError("tool ids must be unique")
        return normalized
