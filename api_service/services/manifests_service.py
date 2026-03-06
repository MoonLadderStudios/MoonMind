"""Service helpers for manifest registry operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ManifestRecord, MoonMindWorkflowState
from moonmind.schemas.manifest_ingest_models import manifest_node_counts_from_nodes
from moonmind.workflows.agent_queue import models as queue_models
from moonmind.workflows.agent_queue.job_types import MANIFEST_JOB_TYPE
from moonmind.workflows.agent_queue.manifest_contract import (
    normalize_manifest_job_payload,
)
from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.workflows.temporal import (
    ManifestIngestValidationError,
    TemporalManifestActivities,
    TemporalArtifactService,
    TemporalExecutionService,
    TemporalExecutionValidationError,
    build_artifact_ref,
    plan_nodes_to_runtime_nodes,
    start_manifest_child_runs,
)


class ManifestRegistryNotFoundError(RuntimeError):
    """Raised when a manifest registry entry does not exist."""


@dataclass(slots=True)
class ManifestRunSubmission:
    """Materialized manifest submission metadata for queue or Temporal runtimes."""

    source: str
    status: str
    job: queue_models.AgentJob | None = None
    workflow_id: str | None = None
    run_id: str | None = None
    workflow_type: str | None = None
    temporal_status: str | None = None
    manifest_artifact_ref: str | None = None


class ManifestsService:
    """Orchestrates manifest registry CRUD and run submission."""

    def __init__(
        self,
        session: AsyncSession,
        queue_service: AgentQueueService | None,
        *,
        execution_service: TemporalExecutionService | None = None,
        artifact_service: TemporalArtifactService | None = None,
    ) -> None:
        self._session = session
        self._queue_service = queue_service
        self._execution_service = execution_service
        self._artifact_service = artifact_service

    async def list_manifests(
        self,
        *,
        limit: int = 50,
        search: str | None = None,
    ) -> list[ManifestRecord]:
        stmt = select(ManifestRecord)
        if search:
            pattern = f"{search.strip()}%"
            stmt = stmt.where(ManifestRecord.name.ilike(pattern))
        stmt = stmt.order_by(ManifestRecord.name.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_manifest(self, name: str) -> ManifestRecord | None:
        stmt = select(ManifestRecord).where(ManifestRecord.name == name)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def require_manifest(self, name: str) -> ManifestRecord:
        record = await self.get_manifest(name)
        if record is None:
            raise ManifestRegistryNotFoundError(f"Manifest '{name}' was not found")
        return record

    async def upsert_manifest(
        self,
        *,
        name: str,
        content: str,
    ) -> ManifestRecord:
        normalized = normalize_manifest_job_payload(
            {
                "manifest": {
                    "name": name,
                    "action": "plan",
                    "source": {"kind": "inline", "content": content},
                }
            }
        )
        manifest_hash = normalized["manifestHash"]
        manifest_version = normalized["manifestVersion"]

        now = datetime.now(UTC)
        record = await self.get_manifest(name)
        if record is None:
            record = ManifestRecord(
                name=name,
                content=content,
                content_hash=manifest_hash,
                version=manifest_version,
                created_at=now,
                updated_at=now,
            )
            self._session.add(record)
        else:
            record.content = content
            record.content_hash = manifest_hash
            record.version = manifest_version
            record.updated_at = now
        await self._session.flush()
        await self._session.refresh(record)
        await self._session.commit()
        return record

    async def submit_manifest_run(
        self,
        *,
        name: str,
        action: str,
        options: dict[str, Any] | None,
        user_id: UUID | None,
        title: str | None = None,
        failure_policy: str | None = None,
        max_concurrency: int | None = None,
        tags: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        system_payload: dict[str, Any] | None = None,
    ) -> ManifestRunSubmission:
        if self._execution_service is not None and self._artifact_service is not None:
            return await self._submit_temporal_manifest_run(
                name=name,
                action=action,
                options=options,
                user_id=user_id,
                title=title,
                failure_policy=failure_policy,
                max_concurrency=max_concurrency,
                tags=tags,
                idempotency_key=idempotency_key,
                system_payload=system_payload,
            )
        if self._queue_service is None:
            raise RuntimeError("Manifest submission runtime is not configured")
        return await self._submit_queue_manifest_run(
            name=name,
            action=action,
            options=options,
            user_id=user_id,
            system_payload=system_payload,
        )

    async def _submit_queue_manifest_run(
        self,
        *,
        name: str,
        action: str,
        options: dict[str, Any] | None,
        user_id: UUID | None,
        system_payload: dict[str, Any] | None = None,
    ) -> ManifestRunSubmission:
        record = await self.require_manifest(name)

        payload = {
            "manifest": {
                "name": record.name,
                "action": action,
                "source": {
                    "kind": "registry",
                    "name": record.name,
                    "content": record.content,
                },
            }
        }
        if options:
            payload["manifest"]["options"] = options
        if system_payload:
            payload["system"] = dict(system_payload)

        assert self._queue_service is not None
        job = await self._queue_service.create_job(
            job_type=MANIFEST_JOB_TYPE,
            payload=payload,
            priority=0,
            created_by_user_id=user_id,
            requested_by_user_id=user_id,
        )

        record.last_run_job_id = job.id
        record.last_run_source = "queue"
        record.last_run_status = job.status.value
        record.last_run_workflow_id = None
        record.last_run_temporal_run_id = None
        record.last_run_manifest_ref = None
        record.last_run_started_at = job.created_at
        record.last_run_finished_at = None
        record.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.commit()
        return ManifestRunSubmission(
            source="queue",
            status=job.status.value,
            job=job,
        )

    async def _submit_temporal_manifest_run(
        self,
        *,
        name: str,
        action: str,
        options: dict[str, Any] | None,
        user_id: UUID | None,
        title: str | None,
        failure_policy: str | None,
        max_concurrency: int | None,
        tags: dict[str, str] | None,
        idempotency_key: str | None,
        system_payload: dict[str, Any] | None = None,
    ) -> ManifestRunSubmission:
        assert self._execution_service is not None
        assert self._artifact_service is not None

        record = await self.require_manifest(name)
        principal = str(user_id) if user_id is not None else "system:manifest-ingest"
        manifest_payload = record.content.encode("utf-8")
        manifest_sha256 = (
            record.content_hash.removeprefix("sha256:")
            if record.content_hash.startswith("sha256:")
            else None
        )

        artifact, _upload = await self._artifact_service.create(
            principal=principal,
            content_type="application/yaml",
            size_bytes=len(manifest_payload),
            sha256=manifest_sha256,
            metadata_json={
                "manifest_name": record.name,
                "manifest_version": record.version,
                "manifest_hash": record.content_hash,
                "action": action,
                "source": "registry",
            },
        )
        artifact = await self._artifact_service.write_complete(
            artifact_id=artifact.artifact_id,
            principal=principal,
            payload=manifest_payload,
            content_type="application/yaml",
        )
        manifest_artifact_ref = build_artifact_ref(artifact).artifact_id
        requested_by = {
            "type": "user" if user_id is not None else "system",
            "id": str(user_id) if user_id is not None else "system",
        }
        parameters: dict[str, Any] = {
            "manifestName": record.name,
            "manifestRef": manifest_artifact_ref,
            "requestedBy": requested_by,
            "action": action,
            "tags": dict(tags or {}),
            "executionPolicy": {},
        }
        if options:
            parameters["options"] = dict(options)
        if max_concurrency is not None:
            parameters["executionPolicy"]["maxConcurrency"] = max_concurrency
        if failure_policy:
            parameters["executionPolicy"]["failurePolicy"] = failure_policy
        if system_payload:
            parameters["system"] = dict(system_payload)
        if not parameters["executionPolicy"]:
            parameters.pop("executionPolicy")

        execution = await self._execution_service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=user_id,
            title=title or f"Manifest ingest: {record.name}",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=manifest_artifact_ref,
            failure_policy=failure_policy,
            initial_parameters=parameters,
            idempotency_key=idempotency_key,
        )
        try:
            manifest_activities = TemporalManifestActivities(
                artifact_service=self._artifact_service
            )
            execution_ref = {
                "namespace": execution.namespace,
                "workflow_id": execution.workflow_id,
                "run_id": execution.run_id,
                "link_type": "output.primary",
                "label": record.name,
            }
            manifest_text = await manifest_activities.manifest_read(
                principal=principal,
                manifest_ref=manifest_artifact_ref,
            )
            compile_result = await manifest_activities.manifest_compile(
                principal=principal,
                manifest_ref=manifest_artifact_ref,
                manifest_payload=manifest_text,
                action=action,
                options=options,
                requested_by=execution.parameters["requestedBy"],
                execution_policy=execution.parameters["executionPolicy"],
                execution_ref=execution_ref,
            )
            execution.plan_ref = compile_result.plan_ref.artifact_id
            child_nodes = plan_nodes_to_runtime_nodes(
                compile_result.nodes,
                requested_by=execution.parameters["requestedBy"],
            )
            child_starts = await start_manifest_child_runs(
                execution_service=self._execution_service,
                parent_execution=execution,
                requested_by=execution.parameters["requestedBy"],
                nodes=child_nodes,
                limit=min(
                    len(child_nodes),
                    int(execution.parameters["executionPolicy"]["maxConcurrency"]),
                ),
            )
            starts_by_node = {item.node_id: item for item in child_starts}
            for node in child_nodes:
                start = starts_by_node.get(node.node_id)
                if start is None:
                    continue
                node.state = "running"
                node.child_workflow_id = start.workflow_id
                node.child_run_id = start.run_id
                node.started_at = datetime.now(UTC)
            summary_ref, run_index_ref = (
                await manifest_activities.manifest_write_summary(
                    principal=principal,
                    workflow_id=execution.workflow_id,
                    state="executing",
                    phase="executing",
                    manifest_ref=manifest_artifact_ref,
                    plan_ref=execution.plan_ref,
                    nodes=[
                        node.model_dump(by_alias=True, mode="json")
                        for node in child_nodes
                    ],
                    execution_ref=execution_ref,
                )
            )
        except ManifestIngestValidationError as exc:
            raise TemporalExecutionValidationError(str(exc)) from exc
        artifact_refs = list(execution.artifact_refs or [])
        for ref in (
            compile_result.plan_ref.artifact_id,
            summary_ref.artifact_id,
            run_index_ref.artifact_id,
        ):
            if ref not in artifact_refs:
                artifact_refs.append(ref)
        execution.artifact_refs = artifact_refs
        execution.parameters["manifestDigest"] = compile_result.manifest_digest
        execution.parameters["manifestNodes"] = [
            node.model_dump(by_alias=True, mode="json") for node in child_nodes
        ]
        execution.memo["summary_artifact_ref"] = summary_ref.artifact_id
        execution.memo["run_index_artifact_ref"] = run_index_ref.artifact_id
        execution.memo["manifest_counts"] = manifest_node_counts_from_nodes(
            child_nodes
        ).model_dump(by_alias=True)
        execution.state = MoonMindWorkflowState.EXECUTING
        execution.search_attributes["mm_state"] = MoonMindWorkflowState.EXECUTING.value
        execution.search_attributes["mm_updated_at"] = datetime.now(UTC).isoformat()
        execution.updated_at = datetime.now(UTC)

        if execution.manifest_ref and execution.manifest_ref != manifest_artifact_ref:
            await self._artifact_service.soft_delete(
                artifact_id=artifact.artifact_id,
                principal=principal,
            )
            manifest_artifact_ref = execution.manifest_ref
        else:
            await self._artifact_service.link_artifact(
                artifact_id=artifact.artifact_id,
                principal=principal,
                execution_ref={
                    "namespace": execution.namespace,
                    "workflow_id": execution.workflow_id,
                    "run_id": execution.run_id,
                    "link_type": "input.manifest",
                    "label": record.name,
                },
            )

        record.last_run_job_id = None
        record.last_run_source = "temporal"
        record.last_run_status = execution.state.value
        record.last_run_workflow_id = execution.workflow_id
        record.last_run_temporal_run_id = execution.run_id
        record.last_run_manifest_ref = manifest_artifact_ref
        record.last_run_started_at = execution.started_at
        record.last_run_finished_at = execution.closed_at
        record.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.commit()
        return ManifestRunSubmission(
            source="temporal",
            status=execution.state.value,
            workflow_id=execution.workflow_id,
            run_id=execution.run_id,
            workflow_type=execution.workflow_type.value,
            temporal_status=_temporal_status(execution.close_status),
            manifest_artifact_ref=manifest_artifact_ref,
        )

    async def update_manifest_state(
        self,
        *,
        name: str,
        state_json: dict[str, Any],
        last_run_job_id: UUID | None = None,
        last_run_status: str | None = None,
        last_run_started_at: datetime | None = None,
        last_run_finished_at: datetime | None = None,
    ) -> ManifestRecord:
        """Persist checkpoint state and optional run metadata for one manifest."""

        record = await self.require_manifest(name)
        now = datetime.now(UTC)
        record.state_json = dict(state_json)
        record.state_updated_at = now
        if last_run_job_id is not None:
            record.last_run_job_id = last_run_job_id
            record.last_run_source = "queue"
        if last_run_status is not None:
            normalized_status = str(last_run_status).strip()
            record.last_run_status = normalized_status or None
        if last_run_started_at is not None:
            record.last_run_started_at = last_run_started_at
        if last_run_finished_at is not None:
            record.last_run_finished_at = last_run_finished_at
        record.updated_at = now
        await self._session.flush()
        await self._session.refresh(record)
        await self._session.commit()
        return record


def _temporal_status(close_status: Any) -> str:
    if close_status is None:
        return "running"
    normalized = getattr(close_status, "value", close_status)
    if normalized == "completed":
        return "completed"
    if normalized == "canceled":
        return "canceled"
    return "failed"


__all__ = [
    "ManifestRegistryNotFoundError",
    "ManifestRunSubmission",
    "ManifestsService",
]
