import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ManagedSecret,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
)
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from moonmind.utils.logging import redact_profile_file_templates, redact_sensitive_payload

logger = logging.getLogger(__name__)

async def normalize_runtime_default_profile(
    *,
    session: AsyncSession,
    runtime_id: str,
    preferred_profile_id: str | None = None,
) -> str | None:
    """Ensure exactly one provider profile is marked default for a runtime."""

    normalized_runtime_id = str(runtime_id or "").strip()
    if not normalized_runtime_id:
        raise ValueError("runtime_id is required")

    stmt = (
        select(ManagedAgentProviderProfile)
        .where(ManagedAgentProviderProfile.runtime_id == normalized_runtime_id)
        .order_by(
            case((ManagedAgentProviderProfile.enabled.is_(True), 1), else_=0).desc(),
            case((ManagedAgentProviderProfile.is_default.is_(True), 1), else_=0).desc(),
            ManagedAgentProviderProfile.priority.desc(),
            ManagedAgentProviderProfile.profile_id.asc(),
        )
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if not rows:
        return None

    managed_secret_statuses = await _managed_secret_statuses_for_profiles(
        session=session,
        rows=rows,
    )
    launchable_rows = [
        row
        for row in rows
        if provider_profile_launch_ready(
            row,
            managed_secret_statuses=managed_secret_statuses,
        )
    ]
    if not launchable_rows:
        rows_to_clear = [row for row in rows if row.is_default]
        for row in rows_to_clear:
            row.is_default = False
        if rows_to_clear:
            await session.flush()
        return None
    candidates = launchable_rows

    selected = None
    if preferred_profile_id:
        selected = next(
            (row for row in candidates if row.profile_id == preferred_profile_id),
            None,
        )
    if selected is None:
        selected = next((row for row in candidates if row.is_default), None)
    if selected is None:
        selected = candidates[0]

    selected_id = selected.profile_id

    rows_to_clear = [
        row for row in rows if row.profile_id != selected_id and row.is_default
    ]
    for row in rows_to_clear:
        row.is_default = False
    if rows_to_clear:
        await session.flush()

    if not selected.is_default:
        selected.is_default = True
        await session.flush()

    return selected_id

def _manager_profile_payload(
    row: ManagedAgentProviderProfile,
    *,
    managed_secret_statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "profile_id": row.profile_id,
        "is_default": row.is_default,
        "runtime_id": row.runtime_id,
        "provider_id": row.provider_id,
        "provider_label": row.provider_label,
        "default_model": row.default_model,
        "model_overrides": row.model_overrides or {},
        "credential_source": row.credential_source.value if row.credential_source else None,
        "runtime_materialization_mode": row.runtime_materialization_mode.value if row.runtime_materialization_mode else None,
        "volume_ref": row.volume_ref,
        "volume_mount_path": row.volume_mount_path,
        "account_label": row.account_label,
        "tags": row.tags or [],
        "priority": row.priority,
        "secret_refs": row.secret_refs or {},
        "clear_env_keys": row.clear_env_keys or [],
        "env_template": redact_sensitive_payload(row.env_template or {}),
        "file_templates": redact_profile_file_templates(row.file_templates or []),
        "home_path_overrides": row.home_path_overrides or {},
        "command_behavior": redact_sensitive_payload(row.command_behavior or {}),
        "max_parallel_runs": row.max_parallel_runs,
        "cooldown_after_429_seconds": row.cooldown_after_429_seconds,
        "rate_limit_policy": (
            row.rate_limit_policy.value if row.rate_limit_policy else None
        ),
        "enabled": row.enabled,
        "max_lease_duration_seconds": row.max_lease_duration_seconds,
        "auth_state": row.auth_state.value if row.auth_state else None,
        "disabled_reason": (
            row.disabled_reason.value if row.disabled_reason else None
        ),
        "launch_ready": provider_profile_launch_ready(
            row,
            managed_secret_statuses=managed_secret_statuses,
        ),
        "first_authenticated_at": (
            row.first_authenticated_at.isoformat()
            if row.first_authenticated_at
            else None
        ),
        "last_validated_at": (
            row.last_validated_at.isoformat() if row.last_validated_at else None
        ),
        "last_auth_method": (
            row.last_auth_method.value if row.last_auth_method else None
        ),
    }

async def sync_provider_profile_manager(
    *,
    session: AsyncSession,
    runtime_id: str,
) -> None:
    """Ensure runtime manager exists and push the latest enabled profiles."""
    stmt = (
        select(ManagedAgentProviderProfile)
        .where(
            ManagedAgentProviderProfile.runtime_id == runtime_id,
            ManagedAgentProviderProfile.enabled.is_(True),
        )
        .order_by(
            ManagedAgentProviderProfile.is_default.desc(),
            ManagedAgentProviderProfile.priority.desc(),
            ManagedAgentProviderProfile.profile_id.asc(),
        )
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    managed_secret_statuses = await _managed_secret_statuses_for_profiles(
        session=session,
        rows=rows,
    )
    profiles_payload = [
        _manager_profile_payload(
            row,
            managed_secret_statuses=managed_secret_statuses,
        )
        for row in rows
        if provider_profile_launch_ready(
            row,
            managed_secret_statuses=managed_secret_statuses,
        )
    ]

    try:
        from moonmind.workflows.temporal.client import TemporalClientAdapter
        from moonmind.workflows.temporal.activity_catalog import get_workflow_task_queue
        from moonmind.workflows.temporal.workflows.provider_profile_manager import (
            ProviderProfileManagerInput,
            WORKFLOW_NAME,
            workflow_id_for_runtime,
        )
        from temporalio.exceptions import WorkflowAlreadyStartedError

        workflow_id = workflow_id_for_runtime(runtime_id)
        temporal_adapter = TemporalClientAdapter()
        temporal_client = await temporal_adapter.get_client()

        try:
            await temporal_client.start_workflow(
                WORKFLOW_NAME,
                ProviderProfileManagerInput(
                    runtime_id=runtime_id
                ),
                id=workflow_id,
                task_queue=get_workflow_task_queue(),
            )
            logger.info("Started ProviderProfileManager for runtime=%s", runtime_id)
        except WorkflowAlreadyStartedError:
            logger.debug("ProviderProfileManager already running for runtime=%s", runtime_id)

        handle = temporal_client.get_workflow_handle(workflow_id)
        await handle.signal("sync_profiles", {"profiles": profiles_payload})
        logger.info(
            "Synced ProviderProfileManager runtime=%s profile_count=%d",
            runtime_id,
            len(profiles_payload),
        )
    except Exception as exc:
        logger.error(
            "Failed to sync ProviderProfileManager runtime=%s: %s",
            runtime_id,
            exc,
            exc_info=True,
        )


async def _managed_secret_statuses_for_profiles(
    *,
    session: AsyncSession,
    rows: list[ManagedAgentProviderProfile],
) -> dict[str, str]:
    slugs: set[str] = set()
    for row in rows:
        if not isinstance(row.secret_refs, dict):
            continue
        for secret_ref in row.secret_refs.values():
            if not isinstance(secret_ref, str) or not secret_ref.startswith("db://"):
                continue
            slug = secret_ref.removeprefix("db://").strip()
            if slug:
                slugs.add(slug)
    if not slugs:
        return {}

    result = await session.execute(
        select(ManagedSecret).where(ManagedSecret.slug.in_(slugs))
    )
    return {
        row.slug: row.status.value if row.status else ""
        for row in result.scalars().all()
    }


# ---------------------------------------------------------------------------
# First-party OAuth lifecycle helpers
#
# OAuth finalization, validation, and disconnect are generalized across the
# big-three first-party runtimes (Claude, Codex, Gemini). These helpers hold
# the per-runtime metadata and the canonical command_behavior shaping so the
# OAuth session finalize path and the provider-profile lifecycle endpoints
# produce identical, transport-neutral profile state.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FirstPartyOAuthProfile:
    """Per-runtime metadata for a first-party OAuth-backed provider profile."""

    runtime_id: str
    provider_id: str
    label_prefix: str
    auth_strategy: str
    home_path_keys: tuple[str, ...]


FIRST_PARTY_OAUTH_PROFILES: dict[tuple[str, str], FirstPartyOAuthProfile] = {
    ("claude_code", "anthropic"): FirstPartyOAuthProfile(
        runtime_id="claude_code",
        provider_id="anthropic",
        label_prefix="Claude",
        auth_strategy="claude_credential_methods",
        home_path_keys=("CLAUDE_HOME",),
    ),
    ("codex_cli", "openai"): FirstPartyOAuthProfile(
        runtime_id="codex_cli",
        provider_id="openai",
        label_prefix="Codex",
        auth_strategy="codex_credential_methods",
        home_path_keys=("CODEX_HOME",),
    ),
    ("gemini_cli", "google"): FirstPartyOAuthProfile(
        runtime_id="gemini_cli",
        provider_id="google",
        label_prefix="Gemini",
        auth_strategy="gemini_credential_methods",
        home_path_keys=("GEMINI_HOME", "GEMINI_CLI_HOME"),
    ),
}


def get_first_party_oauth_profile(
    runtime_id: str | None,
    provider_id: str | None,
) -> FirstPartyOAuthProfile | None:
    """Return the first-party OAuth mapping for a runtime/provider pair."""
    return FIRST_PARTY_OAUTH_PROFILES.get((runtime_id or "", provider_id or ""))


def oauth_auth_actions_for_profile(
    profile: ManagedAgentProviderProfile,
) -> list[str]:
    """Compute the credential-method actions surfaced for an OAuth profile."""
    actions = ["use_api_key"]
    if profile.volume_ref or profile.volume_mount_path:
        actions.insert(0, "connect_oauth")
        actions.extend(["validate_oauth", "disconnect_oauth"])
    return actions


def update_oauth_command_behavior(
    profile: ManagedAgentProviderProfile,
    *,
    mapping: FirstPartyOAuthProfile | None,
    auth_state: str,
    status_label: str,
    readiness: dict[str, Any],
) -> None:
    """Merge canonical OAuth credential-method metadata into command_behavior."""
    behavior = dict(profile.command_behavior or {})
    update: dict[str, Any] = {
        "auth_state": auth_state,
        "auth_actions": oauth_auth_actions_for_profile(profile),
        "auth_status_label": status_label,
        "auth_readiness": readiness,
    }
    if mapping is not None:
        update["auth_strategy"] = mapping.auth_strategy
    behavior.update(update)
    profile.command_behavior = behavior


def oauth_home_path_overrides(
    mapping: FirstPartyOAuthProfile,
    volume_mount_path: str | None,
) -> dict[str, str]:
    """Documented home-path env overrides for an OAuth-after-setup profile."""
    mount_path = str(volume_mount_path or "").strip()
    if not mount_path:
        return {}
    return {key: mount_path for key in mapping.home_path_keys}


def apply_oauth_connected_state(
    profile: ManagedAgentProviderProfile,
    *,
    mapping: FirstPartyOAuthProfile | None,
    validated_at: datetime,
) -> None:
    """Stamp connected command_behavior + home overrides after OAuth success."""
    if mapping is not None:
        profile.home_path_overrides = {
            **(profile.home_path_overrides or {}),
            **oauth_home_path_overrides(mapping, profile.volume_mount_path),
        }
    update_oauth_command_behavior(
        profile,
        mapping=mapping,
        auth_state="connected",
        status_label=(
            f"{mapping.label_prefix} OAuth ready" if mapping else "OAuth ready"
        ),
        readiness={
            "connected": True,
            "last_validated_at": validated_at.isoformat(),
            "backing_secret_exists": True,
            "launch_ready": True,
        },
    )


def apply_oauth_validation_failure(
    profile: ManagedAgentProviderProfile,
    *,
    mapping: FirstPartyOAuthProfile | None,
    reason: str | None,
    failed_at: datetime,
) -> None:
    """Leave a profile visibly disabled after failed OAuth verification.

    Sets the canonical ``auth_state=validation_failed`` /
    ``disabled_reason=auth_invalid`` row state (not just session or
    command_behavior metadata) so the profile remains visible in Settings
    with diagnostics and a retry action.
    """
    profile.enabled = False
    profile.auth_state = ProviderProfileAuthState.VALIDATION_FAILED
    profile.disabled_reason = ProviderProfileDisabledReason.AUTH_INVALID
    profile.last_validated_at = failed_at
    update_oauth_command_behavior(
        profile,
        mapping=mapping,
        auth_state="validation_failed",
        status_label=(
            f"{mapping.label_prefix} OAuth validation failed"
            if mapping
            else "OAuth validation failed"
        ),
        readiness={
            "connected": False,
            "last_validated_at": failed_at.isoformat(),
            "backing_secret_exists": False,
            "launch_ready": False,
            "failure_reason": redact_sensitive_payload(str(reason or "unknown")),
        },
    )
