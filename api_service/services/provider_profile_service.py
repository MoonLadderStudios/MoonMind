import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Any

from api_service.db.models import ManagedAgentProviderProfile

logger = logging.getLogger(__name__)

def _manager_profile_payload(row: ManagedAgentProviderProfile) -> dict[str, Any]:
    return {
        "profile_id": row.profile_id,
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
        "env_template": row.env_template or {},
        "file_templates": row.file_templates or [],
        "home_path_overrides": row.home_path_overrides or {},
        "command_behavior": row.command_behavior or {},
        "max_parallel_runs": row.max_parallel_runs,
        "cooldown_after_429_seconds": row.cooldown_after_429_seconds,
        "rate_limit_policy": (
            row.rate_limit_policy.value if row.rate_limit_policy else None
        ),
        "enabled": row.enabled,
        "max_lease_duration_seconds": row.max_lease_duration_seconds,
    }

async def sync_provider_profile_manager(
    *,
    session: AsyncSession,
    runtime_id: str,
) -> None:
    """Ensure runtime manager exists and push the latest enabled profiles."""
    stmt = select(ManagedAgentProviderProfile).where(
        ManagedAgentProviderProfile.runtime_id == runtime_id,
        ManagedAgentProviderProfile.enabled.is_(True),
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    profiles_payload = [_manager_profile_payload(row) for row in rows]

    try:
        from moonmind.workflows.temporal.client import TemporalClientAdapter
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
                task_queue="mm.workflow",
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
