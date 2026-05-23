"""Launch-token authentication and WebSocket origin checks."""

import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set, Tuple

from .client_identity import coerce_client_kind, default_capabilities_for


@dataclass(frozen=True)
class ClientGrant:
    token: Optional[str]
    client_kind: str
    client_id: Optional[str] = None
    capabilities: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SecurityConfig:
    auth_enabled: bool = False
    launch_token: Optional[str] = None
    allowed_origins: Set[str] = field(default_factory=set)
    allow_loopback_without_token: bool = False
    client_capabilities: Dict[str, Set[str]] = field(default_factory=dict)
    client_grants: Tuple[ClientGrant, ...] = ()

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "SecurityConfig":
        cfg = raw or {}
        return cls(
            auth_enabled=bool(cfg.get("auth_enabled", False)),
            launch_token=cfg.get("launch_token") or None,
            allowed_origins=_string_set(cfg.get("allowed_origins", [])),
            allow_loopback_without_token=bool(cfg.get("allow_loopback_without_token", False)),
            client_capabilities=_capability_map(cfg.get("client_capabilities", {})),
            client_grants=tuple(_client_grants(cfg.get("clients", []))),
        )

    def token_required(self, is_loopback_peer: bool) -> bool:
        if not self.auth_enabled:
            return False
        if self.allow_loopback_without_token and is_loopback_peer:
            return False
        return True

    def validate_token(self, token: Optional[str], is_loopback_peer: bool = False) -> bool:
        if not self.token_required(is_loopback_peer):
            return True
        if not token:
            return False
        if self.launch_token and secrets.compare_digest(str(token), self.launch_token):
            return True
        return any(
            grant.token and secrets.compare_digest(str(token), str(grant.token))
            for grant in self.client_grants
        )

    def origin_allowed(self, origin: Optional[str]) -> bool:
        if not self.auth_enabled or not self.allowed_origins:
            return True
        return bool(origin) and origin in self.allowed_origins

    def granted_capabilities(
        self,
        token: Optional[str],
        client_kind: str,
        client_id: str,
        is_loopback_peer: bool = False,
    ) -> Set[str]:
        kind = coerce_client_kind(client_kind)
        matching_grants = [
            grant
            for grant in self.client_grants
            if grant.token
            and token
            and secrets.compare_digest(str(token), str(grant.token))
        ]
        if matching_grants:
            capabilities: Set[str] = set()
            for grant in matching_grants:
                if grant.client_kind != client_kind:
                    continue
                if grant.client_id and grant.client_id != client_id:
                    continue
                capabilities.update(grant.capabilities)
            if not capabilities:
                raise ValueError("launch token is not valid for requested client identity")
            return capabilities

        if self.token_required(is_loopback_peer):
            if not token or not self.launch_token or not secrets.compare_digest(str(token), self.launch_token):
                raise ValueError("invalid or missing launch token")

        configured = self.client_capabilities.get(client_kind)
        if configured is not None:
            return set(configured)
        return default_capabilities_for(kind)


def _string_set(values: Iterable[Any]) -> Set[str]:
    if isinstance(values, str):
        return {values}
    return {value for value in values if isinstance(value, str) and value}


def _capability_map(raw: Any) -> Dict[str, Set[str]]:
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, Set[str]] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        result[key] = _string_set(value if isinstance(value, list) else [])
    return result


def _client_grants(raw: Any) -> Iterable[ClientGrant]:
    if not isinstance(raw, list):
        return []
    grants = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        client_kind = item.get("client_kind")
        if not isinstance(client_kind, str) or not client_kind:
            continue
        grants.append(ClientGrant(
            token=item.get("token") or None,
            client_kind=client_kind,
            client_id=item.get("client_id") or None,
            capabilities=_string_set(item.get("capabilities", [])),
        ))
    return grants
