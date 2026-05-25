"""Read-only profile validation diagnostics."""

from typing import Any, Dict, Iterable, Optional

from keyboard import Profile, ProfileValidationError, validate_profile


def validate_profile_diagnostics(
    profile: Profile,
    device_capabilities: Optional[Any] = None,
    layout_keys: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Validate a profile and return structured diagnostics instead of raising."""
    try:
        validate_profile(
            profile,
            device_capabilities=device_capabilities,
            layout_keys=layout_keys,
        )
    except ProfileValidationError as exc:
        return {
            "profile_id": getattr(profile, "id", None),
            "valid": False,
            "issues": [
                {
                    "code": "profile_validation_error",
                    "severity": "error",
                    "message": str(exc),
                }
            ],
        }

    return {
        "profile_id": getattr(profile, "id", None),
        "valid": True,
        "issues": [],
    }
