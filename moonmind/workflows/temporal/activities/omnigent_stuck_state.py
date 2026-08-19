"""Temporal side-effect adapters for MoonLadderStudios/MoonMind#3708."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from api_service.db import models as db_models
from moonmind.omnigent.control_plane.records import SessionRecord
from moonmind.utils.logging import redact_sensitive_payload


class TemporalOmnigentReconcileDispatcher:
    """Wake the canonical #3705 supervisor with an idempotent request id."""

    def __init__(self, client_adapter: Any) -> None:
        self._client = client_adapter

    async def request_reconcile(
        self,
        *,
        session_id: str,
        request_id: str,
        reason_code: str,
        expected_revision: str,
        expected_fencing_generation: str,
    ) -> None:
        # Fencing is durably enforced by the command journal before dispatch and
        # by the supervisor when it reloads canonical authority.  The signal is
        # deliberately reference-only and remains compatible with the compact
        # OmnigentSessionSignal contract from dependency #3705.
        del expected_revision, expected_fencing_generation
        await self._client.signal_workflow(
            f"omnigent-session:{session_id}",
            "operator_reconcile_requested",
            {"requestId": request_id, "reasonCode": reason_code},
        )


class TemporalStuckStateDiagnosticPublisher:
    """Publish a restricted, long-retention diagnostic through artifact authority."""

    _PRINCIPAL = "service:omnigent-stuck-state"

    def __init__(self, artifact_service: Any) -> None:
        self._artifacts = artifact_service

    async def publish(
        self,
        *,
        session: SessionRecord,
        decision_id: str,
        payload: dict[str, object],
    ) -> str:
        safe_payload = redact_sensitive_payload(payload)
        encoded = (
            json.dumps(safe_payload, sort_keys=True, indent=2, default=str) + "\n"
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        link = None
        if session.moonmind_run_id:
            link = {
                "namespace": "default",
                "workflow_id": session.moonmind_workflow_id,
                "run_id": session.moonmind_run_id,
                "link_type": "diagnostics",
                "label": "Omnigent stuck-state diagnostic",
                "created_by_activity_type": "agent_runtime.reconcile_managed_sessions",
            }
        artifact, _upload = await self._artifacts.create(
            principal=self._PRINCIPAL,
            content_type="application/json",
            size_bytes=len(encoded),
            sha256=digest,
            retention_class=db_models.TemporalArtifactRetentionClass.LONG,
            redaction_level=db_models.TemporalArtifactRedactionLevel.RESTRICTED,
            link=link,
            metadata_json={
                "kind": "omnigent.stuck_state.diagnostic",
                "issue": "MoonLadderStudios/MoonMind#3708",
                "sessionId": session.session_id,
                "decisionId": decision_id,
            },
        )
        await self._artifacts.write_complete(
            artifact_id=artifact.artifact_id,
            principal=self._PRINCIPAL,
            payload=encoded,
            content_type="application/json",
        )
        # Timeline links are server-authored from opaque artifact ids; do not
        # persist a presigned URL or storage path in canonical session records.
        return artifact.artifact_id


__all__ = [
    "TemporalOmnigentReconcileDispatcher",
    "TemporalStuckStateDiagnosticPublisher",
]
