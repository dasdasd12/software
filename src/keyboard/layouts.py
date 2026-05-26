"""Physical keyboard layout registry used by profile validation."""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable


DEFAULT_PHYSICAL_LAYOUT_ID = "ansi_75_ai_keyboard"


@dataclass(frozen=True)
class PhysicalLayout:
    layout_id: str
    key_ids: FrozenSet[str]
    display_name: str = "ANSI 75 AI Keyboard"


def _default_key_ids() -> FrozenSet[str]:
    alpha = [f"K_{letter}" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    digits = [f"K_{number}" for number in range(10)]
    functions = [f"K_F{number}" for number in range(1, 13)]
    arrows = ["K_UP", "K_DOWN", "K_LEFT", "K_RIGHT"]
    modifiers = [
        "K_FN",
        "K_LCTRL",
        "K_LSHIFT",
        "K_LALT",
        "K_LGUI",
        "K_RCTRL",
        "K_RSHIFT",
        "K_RALT",
        "K_RGUI",
    ]
    navigation = [
        "K_ENTER",
        "K_ESC",
        "K_TAB",
        "K_CAPS_LOCK",
        "K_SPACE",
        "K_BACKSPACE",
        "K_DELETE",
        "K_HOME",
        "K_END",
        "K_PAGE_UP",
        "K_PAGE_DOWN",
        "K_INSERT",
        "K_PRINT_SCREEN",
        "K_SCROLL_LOCK",
        "K_PAUSE",
    ]
    agent_keys = [
        "K_LAUNCH",
        "K_TOOL_1",
        "K_TOOL_2",
        "K_TOOL_3",
        "K_TOOL_4",
        "K_CODEX_LAUNCH",
        "K_CLAUDE_LAUNCH",
        "K_APPROVE",
        "K_DENY",
        "K_INTERRUPT",
        "K_CLOSE",
        "K_FOCUS_NEXT",
        "K_TOOL_NEXT",
    ]
    macro_keys = [f"K_MACRO_{number}" for number in range(1, 7)]
    punctuation = [
        "K_MINUS",
        "K_EQUAL",
        "K_LEFT_BRACKET",
        "K_RIGHT_BRACKET",
        "K_BACKSLASH",
        "K_SEMICOLON",
        "K_APOSTROPHE",
        "K_GRAVE",
        "K_COMMA",
        "K_DOT",
        "K_SLASH",
    ]
    return frozenset(alpha + digits + functions + arrows + modifiers + navigation + agent_keys + macro_keys + punctuation)


_LAYOUTS: Dict[str, PhysicalLayout] = {
    DEFAULT_PHYSICAL_LAYOUT_ID: PhysicalLayout(
        layout_id=DEFAULT_PHYSICAL_LAYOUT_ID,
        key_ids=_default_key_ids(),
    )
}


def get_default_physical_layout() -> PhysicalLayout:
    return _LAYOUTS[DEFAULT_PHYSICAL_LAYOUT_ID]


def get_physical_layout(layout_id: str = DEFAULT_PHYSICAL_LAYOUT_ID) -> PhysicalLayout:
    try:
        return _LAYOUTS[layout_id]
    except KeyError as exc:
        raise KeyError(f"unknown physical layout: {layout_id}") from exc


def get_layout_keys(layout_id: str = DEFAULT_PHYSICAL_LAYOUT_ID) -> FrozenSet[str]:
    return get_physical_layout(layout_id).key_ids


def validate_layout_keys(key_ids: Iterable[str], layout_id: str = DEFAULT_PHYSICAL_LAYOUT_ID) -> None:
    known = get_layout_keys(layout_id)
    unknown = sorted(set(key_ids) - set(known))
    if unknown:
        raise ValueError(f"unknown key reference: {unknown[0]}")
