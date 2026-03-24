"""OAuth session service — bridges API layer to Temporal workflows."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def start_oauth_session_workflow(session_model: Any) -> None:
    """Start the Temporal workflow for a new OAuth session.

    Starts an ``MoonMind.OAuthSession`` workflow whose ID is
    ``oauth-session:<session_id>``.  The workflow manages volume
    provisioning, status transitions, and session expiry.
    """
    from moonmind.workflows.temporal.service import get_temporal_client

    session_id: str = session_model.session_id
    workflow_id = f"oauth-session:{session_id}"

    try:
        client = await get_temporal_client()
        from moonmind.workflows.temporal.workflows.oauth_session import (
            WORKFLOW_NAME,
            WORKFLOW_TASK_QUEUE,
        )

        await client.start_workflow(
            WORKFLOW_NAME,
            {
                "session_id": session_id,
                "runtime_id": session_model.runtime_id,
                "profile_id": session_model.profile_id,
                "volume_ref": session_model.volume_ref or "",
                "volume_mount_path": session_model.volume_mount_path or "",
                "requested_by_user_id": session_model.requested_by_user_id or "",
                "profile_settings": session_model.metadata_json or {},
            },
            id=workflow_id,
            task_queue=WORKFLOW_TASK_QUEUE,
        )
        logger.info("Started OAuth session workflow %s", workflow_id)
    except Exception as exc:
        logger.exception(
            "Failed to start OAuth session workflow for %s", session_id
        )
        raise RuntimeError(
            f"Failed to start OAuth session workflow {workflow_id}"
        ) from exc


async def cancel_oauth_session_workflow(session_id: str) -> None:
    """Cancel an existing OAuth session Temporal workflow.

    Sends a ``cancel`` signal to the workflow, allowing it to
    transition gracefully to ``cancelled`` status.
    """
    from moonmind.workflows.temporal.service import get_temporal_client

    workflow_id = f"oauth-session:{session_id}"

    try:
        client = await get_temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal("cancel")
        logger.info("Sent cancel signal to OAuth session workflow %s", workflow_id)
    except Exception:
        logger.exception(
            "Failed to cancel OAuth session workflow %s", session_id
        )
