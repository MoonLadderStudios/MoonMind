from __future__ import annotations

from typing import Any

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
    SecretStatus,
)
from api_service.services.provider_profile_creation import required_secret_roles
from moonmind.auth.secret_refs import SecretBackend, SecretReferenceError, parse_secret_ref
from moonmind.provider_profiles.oauth_policy import is_codex_oauth_profile


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
    if is_codex_oauth_profile(
        runtime_id=row.runtime_id,
        credential_source=row.credential_source,
        materialization_mode=row.runtime_materialization_mode,
    ) and row.max_parallel_runs != 1:
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
    if is_codex_oauth_profile(
        runtime_id=profile.get("runtime_id", profile.get("runtimeId")),
        credential_source=profile.get(
            "credential_source", profile.get("credentialSource")
        ),
        materialization_mode=profile.get(
            "runtime_materialization_mode",
            profile.get("runtimeMaterializationMode"),
        ),
    ) and profile.get("max_parallel_runs", profile.get("maxParallelRuns")) != 1:
        return False

    credential_source = profile.get(
        "credential_source",
        profile.get("credentialSource"),
    )
    if str(getattr(credential_source, "value", credential_source) or "") == "secret_ref":
        secret_refs = profile.get("secret_refs", profile.get("secretRefs"))
        if not isinstance(secret_refs, dict) or not secret_refs:
            return False
        required_roles = required_secret_roles(
            str(profile.get("runtime_id", profile.get("runtimeId")) or ""),
            str(profile.get("provider_id", profile.get("providerId")) or ""),
        )
        if any(not secret_refs.get(role) for role in required_roles):
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
    required_roles = required_secret_roles(row.runtime_id, row.provider_id)
    if any(not row.secret_refs.get(role) for role in required_roles):
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
