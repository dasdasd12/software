"""Resolve profile bindings into pure keyboard actions."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .input import KeyboardInputEvent
from .profile import (
    KeyboardAction,
    Profile,
    iter_keymap_actions,
    iter_layer_actions,
)


@dataclass(frozen=True)
class ResolvedKeyboardAction:
    binding_id: str
    action: KeyboardAction
    key_id: str
    layer_id: Optional[str]
    profile_id: str
    safety: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0


@dataclass(frozen=True)
class _Candidate:
    order: int
    resolved: ResolvedKeyboardAction


class BindingResolver:
    """Pure resolver for service-required keyboard actions.

    Layer priority is the numeric layer ``priority`` field when present.
    Otherwise layer order in the profile is used, with base bindings at 0.
    Matching returns only candidates from the highest matching priority, in
    stable profile order.
    """

    def __init__(self, profile: Profile):
        self.profile = profile
        self._layer_priorities = self._build_layer_priorities(profile)

    def resolve(self, event: KeyboardInputEvent) -> List[ResolvedKeyboardAction]:
        candidates: List[_Candidate] = []
        candidates.extend(self._resolve_agent_bindings(event, start_order=0))
        candidates.extend(self._resolve_keymap_actions(event, start_order=len(candidates)))
        candidates.extend(self._resolve_layer_actions(event, start_order=len(candidates)))
        if not candidates:
            return []

        max_priority = max(candidate.resolved.priority for candidate in candidates)
        highest = [
            candidate
            for candidate in candidates
            if candidate.resolved.priority == max_priority
        ]
        return [
            candidate.resolved
            for candidate in sorted(highest, key=lambda candidate: candidate.order)
        ]

    def _resolve_agent_bindings(
        self,
        event: KeyboardInputEvent,
        *,
        start_order: int,
    ) -> List[_Candidate]:
        candidates: List[_Candidate] = []
        for offset, binding in enumerate(self.profile.agent_bindings):
            trigger = binding.trigger
            if trigger.source != "key":
                continue
            if trigger.event != event.event_type:
                continue
            if trigger.key and trigger.key != event.key_id:
                continue
            if trigger.layer and trigger.layer not in event.active_layers:
                continue
            if not self._is_agent_action(binding.action):
                continue
            layer_id = trigger.layer
            candidates.append(_Candidate(
                order=start_order + offset,
                resolved=ResolvedKeyboardAction(
                    binding_id=binding.id,
                    action=binding.action,
                    key_id=event.key_id,
                    layer_id=layer_id,
                    profile_id=self.profile.id,
                    safety=dict(binding.safety),
                    priority=self._priority_for_layer(layer_id),
                ),
            ))
        return candidates

    def _resolve_keymap_actions(
        self,
        event: KeyboardInputEvent,
        *,
        start_order: int,
    ) -> List[_Candidate]:
        if event.event_type != "press":
            return []
        candidates: List[_Candidate] = []
        for offset, (key_id, action) in enumerate(iter_keymap_actions(self.profile.keymap)):
            if key_id != event.key_id:
                continue
            if not self._is_agent_action(action):
                continue
            candidates.append(_Candidate(
                order=start_order + offset,
                resolved=ResolvedKeyboardAction(
                    binding_id=f"keymap:{key_id}",
                    action=action,
                    key_id=key_id,
                    layer_id=None,
                    profile_id=self.profile.id,
                    priority=0,
                ),
            ))
        return candidates

    def _resolve_layer_actions(
        self,
        event: KeyboardInputEvent,
        *,
        start_order: int,
    ) -> List[_Candidate]:
        if event.event_type != "press":
            return []
        candidates: List[_Candidate] = []
        order = start_order
        for layer in self.profile.layers:
            layer_id = layer.get("id")
            if not isinstance(layer_id, str) or layer_id not in event.active_layers:
                continue
            for key_id, action in iter_layer_actions(layer):
                current_order = order
                order += 1
                if key_id != event.key_id:
                    continue
                if not self._is_agent_action(action):
                    continue
                candidates.append(_Candidate(
                    order=current_order,
                    resolved=ResolvedKeyboardAction(
                        binding_id=f"layer:{layer_id}:{key_id}",
                        action=action,
                        key_id=key_id,
                        layer_id=layer_id,
                        profile_id=self.profile.id,
                        priority=self._priority_for_layer(layer_id),
                    ),
                ))
        return candidates

    @staticmethod
    def _is_agent_action(action: KeyboardAction) -> bool:
        return action.type.startswith("agent.")

    @staticmethod
    def _build_layer_priorities(profile: Profile) -> Dict[str, int]:
        priorities: Dict[str, int] = {}
        for index, layer in enumerate(profile.layers, start=1):
            layer_id = layer.get("id")
            if not isinstance(layer_id, str):
                continue
            raw_priority = layer.get("priority", index)
            try:
                priorities[layer_id] = int(raw_priority)
            except (TypeError, ValueError):
                priorities[layer_id] = index
        return priorities

    def _priority_for_layer(self, layer_id: Optional[str]) -> int:
        if layer_id is None:
            return 0
        return self._layer_priorities.get(layer_id, 0)
