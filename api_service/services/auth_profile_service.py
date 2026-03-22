import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Any

from api_service.db.models import ManagedAgentAuthProfile

logger = logging.getLogger(__name__)

def _manager_profile_payload(row: ManagedAgentAuthProfile) -> dict[str, Any]:
    return {
        "profile_id": row.profile_id,
        "runtime_id": row.runtime_id,
        "auth_mode": row.auth_mode.value if row.auth_mode else None,
        "volume_ref": row.volume_ref,
        "volume_mount_path": row.volume_mount_path,
        "account_label": row.account_label,
        "api_key_ref": row.api_key_ref,
        "max_parallel_runs": row.max_parallel_runs,
        "cooldown_after_429_seconds": row.cooldown_after_429_seconds,
        "rate_limit_policy": (
            row.rate_limit_policy.value if row.rate_limit_policy else None
        ),
        "enabled": row.enabled,
    }

async def sync_auth_profile_manager(
    *,
    session: AsyncSession,
    runtime_id: str,
) -> None:
    """Ensure runtime manager exists and push the latest enabled profiles."""
    stmt = select(ManagedAgentAuthProfile).where(
        ManagedAgentAuthProfile.runtime_id == runtime_id,
        ManagedAgentAuthProfile.enabled.is_(True),
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()
    profiles_payload = [_manager_profile_payload(row) for row in rows]

    try:
        from moonmind.workflows.temporal.client import TemporalClientAdapter
        from moonmind.workflows.temporal.workflows.auth_profile_manager import (
            AuthProfileManagerInput,
            WORKFLOW_NAME,
        )
        from temporalio.exceptions import WorkflowAlreadyStartedError

        workflow_id = f"auth-profile-manager:{runtime_id}"
        temporal_adapter = TemporalClientAdapter()
        temporal_client = await temporal_adapter.get_client()

        try:
            await temporal_client.start_workflow(
                WORKFLOW_NAME,
                AuthProfileManagerInput(runtime_id=runtime_id),
                id=workflow_id,
                task_queue="mm.workflow",
            )
            logger.info("Started AuthProfileManager for runtime=%s", runtime_id)
        except WorkflowAlreadyStartedError:
            logger.debug("AuthProfileManager already running for runtime=%s", runtime_id)

        handle = temporal_client.get_workflow_handle(workflow_id)
        await handle.signal("sync_profiles", {"profiles": profiles_payload})
        logger.info(
            "Synced AuthProfileManager runtime=%s profile_count=%d",
            runtime_id,
            len(profiles_payload),
        )
    except Exception as exc:
        logger.error(
            "Failed to sync AuthProfileManager runtime=%s: %s",
            runtime_id,
            exc,
            exc_info=True,
        )
