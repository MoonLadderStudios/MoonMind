"""Pure policy helpers for profile-owned OAuth credential capacity."""

from __future__ import annotations

from typing import Any


CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR = (
    "Codex OAuth Provider Profiles require max_parallel_runs=1 because the "
    "OAuth home contains mutable refresh-token and credential state."
)


def _normalized_value(value: object) -> object:
    """Normalize enum-shaped values without coupling policy to an enum type."""

    normalized = getattr(value, "value", value)
    if isinstance(normalized, str):
        return normalized.strip()
    return normalized


def is_codex_oauth_profile(
    *,
    runtime_id: object,
    credential_source: object,
    materialization_mode: object,
) -> bool:
    """Return whether the supplied values identify a Codex OAuth-home profile."""

    return (
        _normalized_value(runtime_id) == "codex_cli"
        and _normalized_value(credential_source) == "oauth_volume"
        and _normalized_value(materialization_mode) == "oauth_home"
    )


def validate_codex_oauth_capacity(
    *,
    runtime_id: object,
    credential_source: object,
    materialization_mode: object,
    max_parallel_runs: int,
) -> None:
    """Reject a Codex OAuth profile that is not on the exclusive capacity lane."""

    if is_codex_oauth_profile(
        runtime_id=runtime_id,
        credential_source=credential_source,
        materialization_mode=materialization_mode,
    ) and max_parallel_runs != 1:
        raise ValueError(CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR)


def effective_oauth_capacity_for_finalization(
    *,
    runtime_id: object,
    requested_capacity: Any,
) -> int:
    """Return persisted OAuth capacity, repairing legacy Codex requests to one."""

    if isinstance(requested_capacity, bool):
        raise ValueError("max_parallel_runs must be a positive integer")
    if isinstance(requested_capacity, int):
        normalized_capacity = requested_capacity
    elif (
        isinstance(requested_capacity, str)
        and requested_capacity.strip().isdigit()
    ):
        normalized_capacity = int(requested_capacity.strip())
    else:
        raise ValueError("max_parallel_runs must be a positive integer")
    if normalized_capacity < 1:
        raise ValueError("max_parallel_runs must be a positive integer")
    if _normalized_value(runtime_id) == "codex_cli":
        return 1
    return normalized_capacity


__all__ = [
    "CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR",
    "effective_oauth_capacity_for_finalization",
    "is_codex_oauth_profile",
    "validate_codex_oauth_capacity",
]
