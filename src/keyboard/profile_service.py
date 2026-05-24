"""Profile CRUD and capability validation service."""

from typing import Any, Dict, Iterable, List, Optional

from .compiler import compile_profile_for_device
from .profile import Profile, validate_profile


class ProfileService:
    def __init__(self, profiles: Optional[Iterable[Profile]] = None):
        self._profiles: Dict[str, Profile] = {}
        for profile in profiles or []:
            self.upsert(profile)

    def upsert(self, profile: Profile) -> None:
        validate_profile(profile)
        self._profiles[profile.id] = profile

    def get(self, profile_id: str) -> Optional[Profile]:
        return self._profiles.get(profile_id)

    def list(self) -> List[Profile]:
        return [self._profiles[profile_id] for profile_id in sorted(self._profiles)]

    def delete(self, profile_id: str) -> bool:
        return self._profiles.pop(profile_id, None) is not None

    def validate_for_sync(self, profile_id: str, device_capabilities: Any) -> Dict[str, Any]:
        profile = self._profiles[profile_id]
        return compile_profile_for_device(profile, device_capabilities)
