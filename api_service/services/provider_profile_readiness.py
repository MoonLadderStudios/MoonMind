from __future__ import annotations

from typing import Any

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
    SecretStatus,
)
from moonmind.auth.secret_refs import SecretBackend, SecretReferenceError, parse_secret_ref


def provider_profile_launch_ready(
    row: ManagedAgentProviderProfile,
    *,
    managed_secret_statuses: dict[str, str] | None = None,
) -> bool:
    """Return the canonical launch predicate for DB-backed profile routing."""

    if not row.enabled:
        return False
    if row.auth_state != ProviderProfileAuthState.CONNECTED:
        return False
    if row.disabled_reason is not None:
        return False
    if not row.max_parallel_runs or row.max_parallel_runs <= 0:
        return False
    if _codex_oauth_capacity_invalid(
        runtime_id=row.runtime_id,
        credential_source=row.credential_source,
        materialization_mode=row.runtime_materialization_mode,
        max_parallel_runs=row.max_parallel_runs,
    ):
        return False
    if row.cooldown_after_429_seconds is None or row.cooldown_after_429_seconds < 0:
        return False
    if not _credential_bindings_launch_ready(
        row,
        managed_secret_statuses=managed_secret_statuses or {},
    ):
        return False
    return _provider_validation_launch_ready(row.command_behavior)


def provider_profile_launch_ready_from_payload(profile: dict[str, Any]) -> bool:
    """Return launch readiness for adapter/manager profile payloads."""

    if profile.get("enabled") is False:
        return False
    if profile.get("launch_ready") is False or profile.get("launchReady") is False:
        return False
    if _codex_oauth_capacity_invalid(
        runtime_id=profile.get("runtime_id", profile.get("runtimeId")),
        credential_source=profile.get(
            "credential_source", profile.get("credentialSource")
        ),
        materialization_mode=profile.get(
            "runtime_materialization_mode",
            profile.get("runtimeMaterializationMode"),
        ),
        max_parallel_runs=profile.get(
            "max_parallel_runs", profile.get("maxParallelRuns")
        ),
    ):
        return False

    readiness = profile.get("readiness")
    if isinstance(readiness, dict):
        launch_ready = readiness.get("launch_ready")
        if launch_ready is None:
            launch_ready = readiness.get("launchReady")
        if launch_ready is False:
            return False

    command_behavior = profile.get("command_behavior")
    if isinstance(command_behavior, dict):
        return _provider_validation_launch_ready(command_behavior)

    return True


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _codex_oauth_capacity_invalid(
    *,
    runtime_id: Any,
    credential_source: Any,
    materialization_mode: Any,
    max_parallel_runs: Any,
) -> bool:
    return (
        _enum_value(runtime_id) == "codex_cli"
        and _enum_value(credential_source) == "oauth_volume"
        and _enum_value(materialization_mode) == "oauth_home"
        and max_parallel_runs != 1
    )


def _credential_bindings_launch_ready(
    row: ManagedAgentProviderProfile,
    *,
    managed_secret_statuses: dict[str, str],
) -> bool:
    credential_source = row.credential_source
    materialization_mode = row.runtime_materialization_mode
    if credential_source == ProviderCredentialSource.OAUTH_VOLUME or (
        materialization_mode == RuntimeMaterializationMode.OAUTH_HOME
    ):
        return bool(row.volume_ref and row.volume_mount_path)

    if credential_source != ProviderCredentialSource.SECRET_REF:
        return True

    if not isinstance(row.secret_refs, dict) or not row.secret_refs:
        return False
    for secret_ref in row.secret_refs.values():
        if not isinstance(secret_ref, str) or not secret_ref:
            return False
        try:
            parsed = parse_secret_ref(secret_ref)
        except SecretReferenceError:
            return False
        if parsed.backend == SecretBackend.DB_ENCRYPTED:
            if managed_secret_statuses.get(parsed.locator) != SecretStatus.ACTIVE.value:
                return False
    return True


def _provider_validation_launch_ready(command_behavior: Any) -> bool:
    if not isinstance(command_behavior, dict):
        return True
    readiness = command_behavior.get("auth_readiness")
    if not isinstance(readiness, dict):
        return True
    launch_ready = readiness.get("launch_ready")
    if launch_ready is None:
        launch_ready = readiness.get("launchReady")
    return launch_ready is not False
