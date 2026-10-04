"""Artifact publication boundary for Omnigent bridge execution.

Owned by MM-1158: this module publishes bridge evidence as MoonMind artifact
refs and assembles the terminal ``AgentRunResult`` returned to workflows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os.path
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from re import sub
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from moonmind.omnigent.bridge_security import redact_raw_events
from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.control_plane import spans as control_plane_spans
from moonmind.omnigent.failure_classification import (
    OmnigentFailureReason,
    classify_omnigent_failure,
    failure_class_for_terminal_status,
)
from moonmind.omnigent.settings import resolved_server_url
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.utils.logging import redact_sensitive_payload
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

_MAX_OMNIGENT_HARVEST_ITEMS = 100
_MAX_OMNIGENT_CONTENT_BYTES = 10 * 1024 * 1024


def _content_limit_reason(content: bytes) -> str | None:
    if len(content) <= _MAX_OMNIGENT_CONTENT_BYTES:
        return None
    return (
        f"content exceeds the {_MAX_OMNIGENT_CONTENT_BYTES}-byte harvest limit "
        f"({len(content)} bytes)"
    )


_MAX_OMNIGENT_PREVIEW_BYTES = 256 * 1024
_OMNIGENT_HARVEST_TIMEOUT_SECONDS = 30
_OMNIGENT_HARVEST_MAX_ATTEMPTS = 3
_CAPTURE_MANIFEST_SCHEMA_VERSION = "moonmind.omnigent.capture_manifest.v1"
_RESOURCE_PROJECTION_SCHEMA_VERSION = "moonmind.omnigent.resource_projection.v1"


class OmnigentContractError(RuntimeError):
    """Raised when Omnigent emits an unsupported adapter contract value."""


class OmnigentArtifactError(RuntimeError):
    """Raised when Omnigent artifact evidence cannot be read or written."""


_INPUT_LINK_PRODUCER = "omnigent.admit_execution_inputs"
_MAX_INPUT_MANIFEST_BYTES = 4 * 1024 * 1024


def _input_label(request: AgentExecutionRequest) -> str:
    identity = request.step_execution
    if identity is None:
        raise OmnigentArtifactError(
            "artifact read requires admitted Step Execution authority"
        )
    binding = request.omnigent_execution_plan or identity.omnigent_execution_plan
    plan_ref = binding.plan_ref if binding else request.parameters.get("executionPlanRef")
    return plan_ref or "step-inputs:sha256:" + hashlib.sha256(
        identity.step_execution_id.encode("utf-8")
    ).hexdigest()


def _matches_input_link(link, *, namespace, workflow_id, run_id, label):
    return (
        link.namespace == namespace
        and link.workflow_id == workflow_id
        and link.run_id == run_id
        and link.link_type == "input.execution_plan"
        and link.label == label
        and link.created_by_activity_type == _INPUT_LINK_PRODUCER
    )


def _check_input_metadata(artifact, expected_digest=None):
    from moonmind.core.artifacts import TemporalArtifactStatus

    if artifact.status != TemporalArtifactStatus.COMPLETE:
        raise OmnigentArtifactError("admitted input artifact is not complete")
    expires = artifact.expires_at
    if (
        expires is not None
        and expires.replace(tzinfo=expires.tzinfo or UTC) <= datetime.now(UTC)
    ):
        raise OmnigentArtifactError("admitted input artifact has expired")
    if expected_digest and expected_digest != "sha256:" + str(artifact.sha256):
        raise OmnigentArtifactError("admitted input artifact digest conflicts")


async def _read_input_manifest(service, artifact_id, *, workflow_id, trusted_root=False):
    artifact = await service._repository.get_artifact(artifact_id)
    _check_input_metadata(artifact)
    if artifact.size_bytes is None or artifact.size_bytes > _MAX_INPUT_MANIFEST_BYTES:
        raise OmnigentArtifactError("admitted input manifest exceeds its size bound")
    if trusted_root:
        # Only the admission owner calls this after checking the immutable plan
        # pointer and exact root. No owner delegation escapes into a runtime
        # reader, and preview/restricted content cannot become raw authority.
        from moonmind.core.artifacts import TemporalArtifactRedactionLevel

        if artifact.redaction_level != TemporalArtifactRedactionLevel.NONE:
            raise OmnigentArtifactError("admitted manifest has no raw input permission")
    _artifact, body = await service.read(
        artifact_id=artifact_id,
        principal="service:omnigent-generic-host",
        admitted_principal=(
            artifact.created_by_principal if trusted_root else f"workflow:{workflow_id}"
        ),
    )
    if (
        len(body) > _MAX_INPUT_MANIFEST_BYTES
        or hashlib.sha256(body).hexdigest() != artifact.sha256
    ):
        raise OmnigentArtifactError(
            "admitted input manifest bytes conflict with their digest"
        )
    return json.loads(body)


def _skill_content_refs(manifest):
    from moonmind.schemas.agent_skill_models import ResolvedSkillSet
    from moonmind.workflows.skills.run_projection import _strip_legacy_skill_version_fields

    resolved = ResolvedSkillSet.model_validate(
        _strip_legacy_skill_version_fields(manifest)
    )
    return {
        TemporalOmnigentArtifactGateway._artifact_id(skill.content_ref): skill.content_digest
        for skill in resolved.skills
        if skill.content_ref
    }


def _task_attachment_refs(snapshot):
    # This is the persisted product input contract, not a recursive ref scan of
    # arbitrary issue text, tool output or agent-supplied owner strings.
    refs = {
        TemporalOmnigentArtifactGateway._artifact_id(item["artifactId"]): item.get("digest")
        for item in snapshot.get("attachmentRefs", [])
        if isinstance(item, dict) and item.get("artifactId")
    }
    draft = snapshot.get("draft") or {}
    workflow = draft.get("workflow") or {}
    workspace = draft.get("workspaceSpec") or workflow.get("workspace") or {}
    source = workspace.get("workspaceSource") or {}
    for ref in (
        *workspace.get("restoreInputRefs", ()),
        workspace.get("workspaceCheckpointRestoreRef"),
        workspace.get("workspaceArtifactRef"),
        source.get("artifactRef"),
        source.get("checkpointRef"),
    ):
        if ref:
            refs[TemporalOmnigentArtifactGateway._artifact_id(ref)] = None
    base_ref = source.get("artifactRef") or source.get("checkpointRef")
    if base_ref:
        refs[TemporalOmnigentArtifactGateway._artifact_id(base_ref)] = source.get("artifactDigest")
    return refs


async def _link_input(service, artifact_id, *, namespace, workflow_id, run_id, label):
    from moonmind.workflows.temporal.artifacts import ExecutionRef

    if any(
        _matches_input_link(
            link, namespace=namespace, workflow_id=workflow_id,
            run_id=run_id, label=label,
        )
        for link in await service._repository.list_links(artifact_id)
    ):
        return
    await service.link_artifact(
        artifact_id=artifact_id,
        principal="service:omnigent-generic-host",
        execution_ref=ExecutionRef(
            namespace=namespace, workflow_id=workflow_id, run_id=run_id,
            link_type="input.execution_plan", label=label,
            created_by_activity_type=_INPUT_LINK_PRODUCER,
        ),
    )


async def link_verified_execution_plan_inputs(
    *, session_factory, plan, binding, workflow_id: str, run_id: str,
    namespace: str | None = None,
    runtime_input_refs: tuple[str, ...] = (),
) -> None:
    """Link exact verified inputs at a trusted execution admission boundary."""
    from moonmind.omnigent.harness_platform.execution_plan import (
        verify_execution_plan_envelope,
    )
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    plan = verify_execution_plan_envelope(plan)
    if not workflow_id or not run_id:
        raise OmnigentArtifactError(
            "plan input delegation requires a concrete execution"
        )
    if binding is None or (
        binding.plan_ref != plan.planRef
        or binding.plan_digest != "sha256:" + plan.planRef.rsplit(":", 1)[-1]
        or (
            plan.payload.authority is not None
            and (
                binding.task_input_snapshot_ref
                != plan.payload.authority.taskInputSnapshotRef
                or binding.task_input_snapshot_digest
                != plan.payload.authority.taskInputSnapshotDigest
            )
        )
    ):
        raise OmnigentArtifactError(
            "plan input delegation conflicts with admitted authority"
        )
    from moonmind.config.settings import settings
    namespace = namespace or settings.temporal.namespace
    refs = {
        binding.plan_artifact_ref,
        binding.task_input_snapshot_ref,
        plan.payload.agentProfileSnapshotRef,
        plan.payload.policySnapshotRef,
        plan.payload.effectiveLaunchSnapshotRef,
        plan.payload.resolvedSkills.get("resolvedSkillSetRef"),
        *(
            access["artifactRef"]
            for access in plan.payload.resolvedTools.get(
                "repositoryAccess", {}
            ).values()
        ),
    }
    async with session_factory() as session:
        repository = TemporalArtifactRepository(session)
        # The compact plan artifact pointer is validated before attaching any
        # execution grant. A wrong pointer must not expose an unrelated blob
        # while the subsequent plan comparison reports the mismatch.
        plan_artifact = await repository.get_artifact(
            TemporalOmnigentArtifactGateway._artifact_id(binding.plan_artifact_ref)
        )
        expected_digest = hashlib.sha256(
            json.dumps(
                plan.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if (
            plan_artifact.sha256 != expected_digest
            or (plan_artifact.metadata_json or {}).get("artifact_class")
            != "omnigent.execution_plan"
        ):
            raise OmnigentArtifactError(
                "plan artifact pointer conflicts with verified execution authority"
            )
        service = TemporalArtifactService(repository)
        for ref in sorted(value for value in refs if value):
            # Historical non-artifact policy refs are not artifact authority.
            if not str(ref).startswith(("artifact:", "art_")):
                continue
            artifact_id = TemporalOmnigentArtifactGateway._artifact_id(ref)
            _check_input_metadata(await repository.get_artifact(artifact_id))
        # Exact immutable manifests are admitted roots. Their digest-checked
        # content closure receives execution association at this same owner;
        # blobs, owners and historical links remain unchanged (#4633).
        closure = {}
        skill_ref = plan.payload.resolvedSkills.get("resolvedSkillSetRef")
        if skill_ref:
            skill_id = TemporalOmnigentArtifactGateway._artifact_id(skill_ref)
            manifest = await _read_input_manifest(service, skill_id, workflow_id=workflow_id, trusted_root=True)
            declared = plan.payload.resolvedSkills.get("resolvedSkillSetDigest")
            artifact = await repository.get_artifact(skill_id)
            canonical_digest = hashlib.sha256(json.dumps(manifest, sort_keys=True,
                separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            # Retained late-bound plans used a digest of the ref. They require
            # actual producer lineage; an unlinked historical ref cannot use it.
            legacy_digest = "sha256:" + hashlib.sha256(str(skill_ref).encode()).hexdigest()
            if declared not in {"sha256:" + artifact.sha256, "sha256:" + canonical_digest}:
                links = await repository.list_links(skill_id)
                if declared != legacy_digest or not any(
                    link.namespace == namespace and link.workflow_id == workflow_id
                    and link.run_id == run_id and link.link_type == "input.skill_snapshot"
                    for link in links
                ):
                    raise OmnigentArtifactError("Skill manifest lacks verified admitted provenance")
            closure.update(_skill_content_refs(manifest))
        snapshot_id = TemporalOmnigentArtifactGateway._artifact_id(binding.task_input_snapshot_ref)
        _check_input_metadata(await repository.get_artifact(snapshot_id), binding.task_input_snapshot_digest)
        snapshot = await _read_input_manifest(service, snapshot_id, workflow_id=workflow_id, trusted_root=True)
        closure.update(_task_attachment_refs(snapshot))
        immutable_inputs = {
            *closure,
            *(TemporalOmnigentArtifactGateway._artifact_id(ref)
              for ref in refs if ref and str(ref).startswith(("artifact:", "art_"))),
        }
        # Late prepared context is a recorded launch input, not immutable task
        # data. Require its native producer in this exact execution before the
        # trusted admission owner associates it with the plan.
        for artifact_id in runtime_input_refs:
            if artifact_id in immutable_inputs:
                continue
            links = await repository.list_links(artifact_id)
            if not any(
                link.namespace == namespace and link.workflow_id == workflow_id
                and link.run_id == run_id and link.link_type != "input.execution_plan"
                for link in links
            ):
                raise OmnigentArtifactError("runtime input lacks verified execution lineage")
            closure[artifact_id] = None
        for artifact_id, expected_digest in closure.items():
            _check_input_metadata(await repository.get_artifact(artifact_id), expected_digest)
        admitted_ids = {*closure, *(TemporalOmnigentArtifactGateway._artifact_id(ref)
            for ref in refs if ref and str(ref).startswith(("artifact:", "art_")))}
        for artifact_id in sorted(admitted_ids):
            await _link_input(service, artifact_id, namespace=namespace,
                workflow_id=workflow_id, run_id=run_id, label=plan.planRef)


@dataclass(slots=True)
class OmnigentCaptureBundle:
    """MoonMind artifact refs captured for one Omnigent session."""

    output_refs: list[str] = field(default_factory=list)
    diagnostics_ref: str = ""
    capture_manifest_ref: str = ""
    external_state_ref: str = ""
    metadata_refs: dict[str, str] = field(default_factory=dict)
    optional_harvest_failed: bool = False
    resource_harvest_failure_class: str | None = None
    resource_projection: dict[str, Any] = field(default_factory=dict)


class OmnigentArtifactGateway:
    """Minimal artifact boundary needed by the Omnigent activity."""

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        raise NotImplementedError

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        raise NotImplementedError

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        raise NotImplementedError

    async def read_text(self, artifact_ref: str) -> str:
        raise NotImplementedError

    async def read_bytes(self, artifact_ref: str) -> bytes:
        return (await self.read_text(artifact_ref)).encode("utf-8")

    def for_request(self, request: AgentExecutionRequest):
        """Bind a reader to an admitted request; local gateways own their refs."""
        return self

    async def read_repository_access_snapshot(
        self, artifact_ref: str, *, request: AgentExecutionRequest
    ) -> bytes:
        """Read admitted repository metadata through the gateway's authority."""
        return await self.read_bytes(artifact_ref)

    async def admit_execution_plan_inputs(self, *, request, plan) -> None:
        """Local gateways already own their readable input references."""

    async def restore_checkpoint(self, *, restore_request, authority_root):
        raise OmnigentArtifactError("This artifact gateway does not provide durable checkpoint restoration")


class TemporalOmnigentArtifactGateway(OmnigentArtifactGateway):
    """Persist generic-host evidence through MoonMind's durable artifact store."""

    def __init__(
        self,
        session_factory: Any,
        *,
        principal: str = "service:omnigent-generic-host",
        store: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._principal = principal
        self._store = store
        self._request_payload: str | None = None

    def for_request(self, request: AgentExecutionRequest):
        reader = TemporalOmnigentArtifactGateway(self._session_factory, principal=self._principal, store=self._store)
        # Serialize once: later mutation of a launch request cannot change an
        # in-flight reader, and concurrent executions never share this context.
        from pydantic import ValidationError
        try:
            reader._request_payload = request.model_dump_json(by_alias=True)
            AgentExecutionRequest.model_validate_json(reader._request_payload)
        except ValidationError as exc:
            raise OmnigentArtifactError("request conflicts with admitted execution authority") from exc
        return reader

    async def admit_runtime_inputs(self, *, request: AgentExecutionRequest, plan=None):
        """Associate recorded managed-launch inputs at the trusted Activity.

        A pre-plan managed run needs exact native producer links. Unlinked
        product snapshots use the verified plan route instead; owner strings
        and workflow-name prefixes never establish provenance here.
        """
        from moonmind.config.settings import settings
        from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository, TemporalArtifactService

        identity = request.step_execution
        label = _input_label(request)
        if request.omnigent_execution_plan or identity.omnigent_execution_plan:
            # Planned admission has a single owner (link_verified...); readers
            # cannot manufacture an admission by replaying a supplied binding.
            return
        refs = self._runtime_refs(request)
        if plan is not None:
            persisted = await self._verified_request_plan(request)
            if persisted != plan:
                raise OmnigentArtifactError("runtime inputs conflict with the persisted plan")
            refs.update(self._plan_refs(plan))
        async with self._session_factory() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=self._store)
            for artifact_id in refs:
                artifact = await service._repository.get_artifact(artifact_id)
                _check_input_metadata(artifact)
                links = await service._repository.list_links(artifact_id)
                if not any(link.namespace == settings.temporal.namespace
                           and link.workflow_id == identity.workflow_id
                           and link.run_id == identity.run_id
                           and link.link_type != "input.execution_plan" for link in links):
                    raise OmnigentArtifactError("runtime input lacks verified execution lineage")
            closure = {}
            if request.resolved_skillset_ref:
                manifest = await _read_input_manifest(service, self._artifact_id(request.resolved_skillset_ref),
                    workflow_id=identity.workflow_id)
                closure = _skill_content_refs(manifest)
                for artifact_id, expected_digest in closure.items():
                    _check_input_metadata(await service._repository.get_artifact(artifact_id), expected_digest)
            for artifact_id in {*refs, *closure}:
                await _link_input(service, artifact_id, namespace=settings.temporal.namespace,
                    workflow_id=identity.workflow_id, run_id=identity.run_id, label=label)

    @classmethod
    def _runtime_refs(cls, request):
        identity = request.step_execution
        if identity and identity.resolved_skillset_ref and (
            cls._artifact_id(identity.resolved_skillset_ref) != cls._artifact_id(request.resolved_skillset_ref)
        ):
            raise OmnigentArtifactError("request Skill ref conflicts with admitted Step Execution")
        spec = request.workspace_spec or {}
        source = spec.get("workspaceSource") or {}
        refs = [request.resolved_skillset_ref, *request.input_refs,
            *(identity.prepared_input_refs if identity else ()),
            request.parameters.get("gateResultRef"), request.parameters.get("remainingWorkRef"),
            *spec.get("restoreInputRefs", ()), spec.get("workspaceCheckpointRestoreRef"),
            spec.get("workspaceArtifactRef"), source.get("artifactRef"), source.get("checkpointRef")]
        return {cls._artifact_id(ref): None for ref in refs if ref}

    @classmethod
    def _plan_refs(cls, plan):
        return {cls._artifact_id(ref): None for ref in (
            plan.payload.agentProfileSnapshotRef, plan.payload.policySnapshotRef,
            plan.payload.effectiveLaunchSnapshotRef, plan.payload.resolvedSkills.get("resolvedSkillSetRef"),
            *(access["artifactRef"] for access in plan.payload.resolvedTools.get("repositoryAccess", {}).values()),
        ) if ref and str(ref).startswith(("artifact:", "art_"))}

    async def _verified_request_plan(self, request):
        from moonmind.omnigent.harness_platform.execution_plan import verify_execution_plan_envelope
        from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore

        identity = request.step_execution
        binding = request.omnigent_execution_plan or identity.omnigent_execution_plan
        plan_ref = binding.plan_ref if binding else request.parameters.get("executionPlanRef")
        if not plan_ref:
            return None
        persisted = await DbExecutionPlanStore(self._session_factory).load(plan_ref)
        if persisted is None:
            raise OmnigentArtifactError("admitted input plan is unavailable")
        plan = verify_execution_plan_envelope(persisted)
        if binding and (binding.plan_digest != "sha256:" + plan.planRef.rsplit(":", 1)[-1] or (
            plan.payload.authority is not None and (
                binding.task_input_snapshot_ref != plan.payload.authority.taskInputSnapshotRef
                or binding.task_input_snapshot_digest != plan.payload.authority.taskInputSnapshotDigest
            )
        )) or (request.parameters.get("executionPlanRef") or plan.planRef) != plan.planRef:
            raise OmnigentArtifactError("admitted input plan binding conflicts")
        return plan

    async def _admitted_refs(self, service, request):
        from moonmind.config.settings import settings

        identity = request.step_execution
        label = _input_label(request)
        plan = await self._verified_request_plan(request)
        binding = request.omnigent_execution_plan or identity.omnigent_execution_plan
        if binding is None:
            refs = self._runtime_refs(request)
            if plan is not None:
                refs.update(self._plan_refs(plan))
            skill_ref = request.resolved_skillset_ref
            snapshot_ref = None
        else:
            binding = request.omnigent_execution_plan or identity.omnigent_execution_plan
            skill_ref = plan.payload.resolvedSkills.get("resolvedSkillSetRef")
            if request.resolved_skillset_ref and (
                not skill_ref or self._artifact_id(request.resolved_skillset_ref) != self._artifact_id(skill_ref)
            ):
                raise OmnigentArtifactError("request Skill ref is not admitted by its plan")
            refs = {self._artifact_id(ref): None for ref in (
                binding.plan_artifact_ref, binding.task_input_snapshot_ref,
                plan.payload.agentProfileSnapshotRef, plan.payload.policySnapshotRef,
                plan.payload.effectiveLaunchSnapshotRef, skill_ref,
                *(access["artifactRef"] for access in plan.payload.resolvedTools.get("repositoryAccess", {}).values()),
            ) if ref and str(ref).startswith(("artifact:", "art_"))}
            artifact = await service._repository.get_artifact(self._artifact_id(binding.plan_artifact_ref))
            canonical = json.dumps(plan.model_dump(mode="json", by_alias=True),
                sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            _check_input_metadata(artifact, "sha256:" + hashlib.sha256(canonical).hexdigest())
            snapshot_ref = binding.task_input_snapshot_ref
            refs[self._artifact_id(snapshot_ref)] = binding.task_input_snapshot_digest
        # Every root must still have its exact durable admission association.
        # Revoking a link or changing namespace/run fails before reading bytes.
        for artifact_id, expected_digest in refs.items():
            _check_input_metadata(await service._repository.get_artifact(artifact_id), expected_digest)
            links = await service._repository.list_links(artifact_id)
            if not any(_matches_input_link(link, namespace=settings.temporal.namespace,
                        workflow_id=identity.workflow_id, run_id=identity.run_id, label=label) for link in links):
                raise OmnigentArtifactError("input has no current admitted execution authority")
        if skill_ref:
            manifest = await _read_input_manifest(service, self._artifact_id(skill_ref), workflow_id=identity.workflow_id)
            refs.update(_skill_content_refs(manifest))
        if snapshot_ref:
            snapshot = await _read_input_manifest(service, self._artifact_id(snapshot_ref), workflow_id=identity.workflow_id)
            refs.update(_task_attachment_refs(snapshot))
        refs.update({ref: refs.get(ref) for ref in self._runtime_refs(request)})
        return refs

    async def _read(self, method, *, artifact_id, **kwargs):
        from moonmind.config.settings import settings
        from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository, TemporalArtifactService

        if not self._principal.startswith("service:") or self._principal != "service:omnigent-generic-host":
            raise OmnigentArtifactError("invalid machine artifact authority")
        artifact_id = self._artifact_id(artifact_id)
        async with self._session_factory() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session), store=self._store)
            artifact = await service._repository.get_artifact(artifact_id)
            from moonmind.core.artifacts import TemporalArtifactRedactionLevel
            if method != "get_metadata" and artifact.redaction_level == TemporalArtifactRedactionLevel.PREVIEW_ONLY:
                raise OmnigentArtifactError("preview permission does not admit raw input bytes")
            admitted = None
            if self._request_payload is None:
                # Unbound transport reads are only for its own captured output;
                # disabled browser auth never widens the runtime input reader.
                if service._owner_principal(artifact) != self._principal or (
                    (artifact.metadata_json or {}).get("link_type", "").startswith("input.")
                    or any(link.link_type.startswith("input.") for link in await service._repository.list_links(artifact_id))
                ):
                    raise OmnigentArtifactError("artifact input read requires admitted execution authority")
            else:
                request = AgentExecutionRequest.model_validate_json(self._request_payload)
                refs = await self._admitted_refs(service, request)
                identity = request.step_execution
                links = await service._repository.list_links(artifact_id)
                own_output = service._owner_principal(artifact) == self._principal and any(
                    link.namespace == settings.temporal.namespace and link.workflow_id == identity.workflow_id
                    and link.run_id == identity.run_id and not link.link_type.startswith("input.") for link in links
                )
                if own_output:
                    return await getattr(service, method)(artifact_id=artifact_id, principal=self._principal, **kwargs)
                if artifact_id not in refs or not any(_matches_input_link(link,
                    namespace=settings.temporal.namespace, workflow_id=identity.workflow_id,
                    run_id=identity.run_id, label=_input_label(request)) for link in links):
                    raise OmnigentArtifactError("artifact ref is not admitted by the execution")
                _check_input_metadata(artifact, refs[artifact_id])
                admitted = f"workflow:{identity.workflow_id}"
            result = await getattr(service, method)(artifact_id=artifact_id,
                principal=self._principal, admitted_principal=admitted, **kwargs)
            if method == "read" and hashlib.sha256(result[1]).hexdigest() != artifact.sha256:
                raise OmnigentArtifactError("admitted input bytes conflict with their digest")
            return result

    async def admit_execution_plan_inputs(self, *, request, plan) -> None:
        identity = request.step_execution
        if identity is None:
            raise OmnigentArtifactError(
                "repository plan delegation requires Step Execution authority"
            )
        binding = request.omnigent_execution_plan or identity.omnigent_execution_plan
        plan_skill_ref = plan.payload.resolvedSkills.get("resolvedSkillSetRef")
        if request.resolved_skillset_ref and (
            not plan_skill_ref
            or self._artifact_id(request.resolved_skillset_ref)
            != self._artifact_id(plan_skill_ref)
        ):
            raise OmnigentArtifactError(
                "runtime Skill ref conflicts with its admitted plan"
            )
        if binding is None:
            # The surviving pre-plan managed/generic path has recorded native
            # producer authority, rather than a product plan artifact binding.
            await self.admit_runtime_inputs(request=request, plan=plan)
            return
        await link_verified_execution_plan_inputs(
            session_factory=self._session_factory,
            plan=plan,
            binding=binding,
            workflow_id=identity.workflow_id,
            run_id=identity.run_id,
            runtime_input_refs=tuple(self._runtime_refs(request)),
        )

    @staticmethod
    def _artifact_id(artifact_ref: str) -> str:
        value = str(artifact_ref or "").strip()
        if value.startswith("artifact:"):
            value = value.removeprefix("artifact:")
        if value.startswith("//"):
            value = value.removeprefix("//")
        if not value.startswith("art_"):
            raise OmnigentArtifactError(
                f"Unsupported durable artifact ref: {artifact_ref}"
            )
        return value

    @staticmethod
    def _execution_link(
        request: AgentExecutionRequest,
        *,
        name: str,
        link_type: str,
    ) -> Any | None:
        if request.step_execution is None:
            return None
        from moonmind.workflows.temporal.artifacts import ExecutionRef

        from moonmind.config.settings import settings
        return ExecutionRef(
            namespace=settings.temporal.namespace,
            workflow_id=request.step_execution.workflow_id,
            run_id=request.step_execution.run_id,
            link_type=link_type,
            label=_safe_artifact_name(name),
            created_by_activity_type="integration.omnigent.execute",
        )

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        body = (
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
        ).encode("utf-8")
        return await self.write_bytes(
            request=request,
            name=name,
            payload=body,
            link_type=link_type,
            content_type="application/json",
        )

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        return await self.write_bytes(
            request=request,
            name=name,
            payload=payload.encode("utf-8"),
            link_type=link_type,
            content_type=content_type,
        )

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        digest = hashlib.sha256(payload).hexdigest()
        execution_link = self._execution_link(
            request, name=name, link_type=link_type
        )
        async with self._session_factory() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            artifact, _upload = await service.create(
                principal=self._principal,
                content_type=content_type,
                size_bytes=len(payload),
                sha256=digest,
                link=execution_link,
                metadata_json={
                    "artifact_type": "omnigent.generic_host_evidence",
                    "name": _safe_artifact_name(name),
                    "link_type": str(link_type)[:255],
                    "correlation_id": str(request.correlation_id)[:255],
                },
            )
            completed = await service.write_payload_complete(
                artifact_id=artifact.artifact_id,
                principal=self._principal,
                payload=payload,
                content_type=content_type,
            )
        return f"artifact:{completed.artifact_id}"

    async def read_text(self, artifact_ref: str) -> str:
        return (await self.read_bytes(artifact_ref)).decode("utf-8")

    async def restore_checkpoint(self, *, restore_request, authority_root):
        from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository, TemporalArtifactService
        from moonmind.workflows.temporal.runtime.checkpoint_restore import ManagedCheckpointRestoreService
        async with self._session_factory() as session:
            service = ManagedCheckpointRestoreService(
                authority_root=authority_root,
                artifact_service=TemporalArtifactService(TemporalArtifactRepository(session)),
            )
            return await service.restore(restore_request, admitted_principal=self._principal)

    async def read_bytes(self, artifact_ref: str) -> bytes:
        _artifact, payload = await self._read("read", artifact_id=artifact_ref)
        return payload

    async def read(self, *, artifact_id: str, principal: str, allow_restricted_raw: bool = False):
        """The service-shaped interface shares the same request authority."""
        return await self._read("read", artifact_id=artifact_id)

    async def read_repository_access_snapshot(
        self, artifact_ref: str, *, request: AgentExecutionRequest
    ) -> bytes:
        reader = self.for_request(request)
        plan = await reader._verified_request_plan(request)
        if plan is None or self._artifact_id(artifact_ref) not in {
            self._artifact_id(access["artifactRef"])
            for access in plan.payload.resolvedTools.get("repositoryAccess", {}).values()
        }:
            raise OmnigentArtifactError("repository snapshot is not admitted by the execution plan")
        return await reader.read_bytes(artifact_ref)

    async def read_chunks(
        self, *, artifact_id: str, principal: str,
        allow_restricted_raw: bool = False, chunk_size: int,
    ) -> tuple[Any, Any]:
        return await self._read("read_chunks", artifact_id=artifact_id, chunk_size=chunk_size)

    async def get_metadata(self, *, artifact_id: str, principal: str) -> tuple[Any, ...]:
        return await self._read("get_metadata", artifact_id=artifact_id)


class LocalOmnigentArtifactGateway(OmnigentArtifactGateway):
    """Local MoonMind artifact gateway for Omnigent evidence capture."""

    def __init__(
        self,
        *,
        root: str | Path = "var/artifacts/omnigent",
        readable_refs: dict[str, str] | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        self._readable_refs = dict(readable_refs or {})

    async def write_json(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: Any,
        link_type: str,
    ) -> str:
        data = json.dumps(payload, indent=2, sort_keys=True, default=str)
        return await self.write_text(
            request=request,
            name=name,
            payload=f"{data}\n",
            link_type=link_type,
            content_type="application/json",
        )

    async def write_text(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: str,
        link_type: str,
        content_type: str = "text/plain",
    ) -> str:
        return await self.write_bytes(
            request=request,
            name=name,
            payload=payload.encode("utf-8"),
            link_type=link_type,
            content_type=content_type,
        )

    async def write_bytes(
        self,
        *,
        request: AgentExecutionRequest,
        name: str,
        payload: bytes,
        link_type: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        safe_correlation = _safe_artifact_segment(request.correlation_id)
        safe_name = _safe_artifact_name(name)
        # Re-admissions publish the same named evidence for different attempts.
        # A returned ref must keep its bytes and metadata after the next write.
        object_digest = hashlib.sha256(
            json.dumps([content_type, link_type]).encode("utf-8") + b"\0" + payload
        ).hexdigest()
        path = (self._root / safe_correlation / object_digest / safe_name).resolve()
        if not path.is_relative_to(self._root):
            raise OmnigentArtifactError("Omnigent artifact path escapes artifact root")
        digest = hashlib.sha256(payload).hexdigest()
        metadata_path = path.with_suffix(f"{path.suffix}.metadata.json")
        metadata_payload = (
            json.dumps(
                {
                    "contentType": content_type,
                    "linkType": link_type,
                    "sha256": digest,
                    "sizeBytes": len(payload),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        # Surface filesystem persistence failures (disk full, permission,
        # missing directory) as OmnigentArtifactError so the §17 required
        # artifact-persistence handler classifies them instead of letting a
        # raw OSError escape the activity.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=path.parent) as staging:
                staged = Path(staging) / "content"
                staged.write_bytes(payload)
                os.replace(staged, path)
                staged.write_text(metadata_payload, encoding="utf-8")
                os.replace(staged, metadata_path)
        except OSError as exc:
            raise OmnigentArtifactError(
                f"Unable to persist Omnigent artifact '{safe_name}': {exc}"
            ) from exc
        return f"artifact://omnigent/{safe_correlation}/{object_digest}/{safe_name}"

    async def read_text(self, artifact_ref: str) -> str:
        if artifact_ref in self._readable_refs:
            return self._readable_refs[artifact_ref]
        prefix = "artifact://omnigent/"
        if artifact_ref.startswith(prefix):
            relative = artifact_ref[len(prefix) :]
            # Normalize the attacker-influenced ref, then require the result to
            # stay under the artifact root before any read (path-traversal guard).
            base = os.path.realpath(self._root)
            fullpath = os.path.realpath(os.path.join(base, relative))
            if not fullpath.startswith(base):
                raise OmnigentArtifactError(
                    f"Omnigent artifact ref escapes artifact root: {artifact_ref}"
                )
            if fullpath != base and not fullpath.startswith(base + os.sep):
                raise OmnigentArtifactError(
                    f"Omnigent artifact ref escapes artifact root: {artifact_ref}"
                )
            if os.path.isfile(fullpath):
                with open(fullpath, encoding="utf-8") as handle:
                    return handle.read()
        raise OmnigentArtifactError(
            f"Unable to dereference artifact ref: {artifact_ref}"
        )

    async def read_bytes(self, artifact_ref: str) -> bytes:
        if artifact_ref in self._readable_refs:
            return self._readable_refs[artifact_ref].encode("utf-8")
        prefix = "artifact://omnigent/"
        if artifact_ref.startswith(prefix):
            relative = artifact_ref[len(prefix) :]
            # Normalize the attacker-influenced ref, then require the result to
            # stay under the artifact root before any read (path-traversal guard).
            base = os.path.realpath(self._root)
            fullpath = os.path.realpath(os.path.join(base, relative))
            if not fullpath.startswith(base):
                raise OmnigentArtifactError(
                    f"Omnigent artifact ref escapes artifact root: {artifact_ref}"
                )
            if fullpath != base and not fullpath.startswith(base + os.sep):
                raise OmnigentArtifactError(
                    f"Omnigent artifact ref escapes artifact root: {artifact_ref}"
                )
            if os.path.isfile(fullpath):
                with open(fullpath, "rb") as handle:
                    return handle.read()
        raise OmnigentArtifactError(
            f"Unable to dereference artifact ref: {artifact_ref}"
        )


def _safe_artifact_segment(value: object) -> str:
    text = sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    if text in {".", ".."}:
        return "segment"
    return text[:120] or "run"


def _safe_artifact_name(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip().strip("/")
    parts = [_safe_artifact_segment(part) for part in text.split("/") if part.strip()]
    return "/".join(parts) or "artifact"


async def _capture_artifact_json(
    gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    refs: dict[str, str],
    *,
    key: str,
    name: str,
    payload: Any,
    link_type: str,
) -> str:
    # Artifact payloads are durable evidence. Apply the common structured
    # redactor at the gateway boundary as defense in depth for diagnostics and
    # other capture paths that should never receive host credential material.
    safe_payload = redact_sensitive_payload(payload)
    ref = await gateway.write_json(
        request=request,
        name=name,
        payload=safe_payload,
        link_type=link_type,
    )
    refs[key] = ref
    return ref


async def capture_artifact_json(
    gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    refs: dict[str, str],
    *,
    key: str,
    name: str,
    payload: Any,
    link_type: str,
) -> str:
    return await _capture_artifact_json(
        gateway,
        request,
        refs,
        key=key,
        name=name,
        payload=payload,
        link_type=link_type,
    )


def _compact_summary(value: object | None, *, fallback: str) -> str:
    text = str(value or fallback).strip() or fallback
    return text[:4096]


# Diff/patch capture is capability-probed and never fatal on its own (§12.3), so
# `workspaceDiffsUnavailable`/`patchUnavailable` are intentionally excluded here.
_HARVEST_UNAVAILABLE_KEYS = (
    "changedFilesUnavailable",
    "workspaceFilesUnavailable",
    "sessionFilesUnavailable",
)


def _capture_requires_full_evidence(capture_policy: dict[str, Any] | None) -> bool:
    if not capture_policy:
        return False
    return bool(capture_policy.get("requireFullEvidence", False))


def _optional_resource_harvest_failed(manifest: dict[str, Any]) -> bool:
    """True when an optional resource-harvest step recorded an unavailable row.

    Diff/patch capability probes are excluded because §12.3 keeps them
    non-fatal; only changed-file, workspace-file, and session-file harvest
    failures count toward the §17 optional-resource-harvest outcome.
    """

    if any(manifest.get(key) for key in _HARVEST_UNAVAILABLE_KEYS):
        return True
    return any(
        isinstance(item, dict) and item.get("unavailable")
        for group in ("changedFiles", "workspaceFiles", "sessionFiles")
        for item in (manifest.get(group) or [])
    )


def build_omnigent_result(
    *,
    request: AgentExecutionRequest,
    terminal_status: str,
    session_id: str,
    agent_id: str | None,
    final_snapshot: dict[str, Any],
    event_count: int,
    capture_bundle: OmnigentCaptureBundle,
    failure_summary: str | None = None,
    provider_error_code: str | None = None,
    failure_reason: OmnigentFailureReason | None = None,
    require_full_evidence: bool = False,
) -> AgentRunResult:
    """Build compact terminal canonical result for Omnigent.

    ``failure_reason`` selects an explicit §17 classifier row (for example a
    first-message digest mismatch that must map to ``user_error`` even though
    the terminal status is ``failed``). When omitted, the failure class is
    derived from the terminal status via the same §17 classifier.
    """

    output_refs = list(capture_bundle.output_refs)
    diagnostics_ref = capture_bundle.diagnostics_ref
    if not output_refs:
        raise OmnigentContractError(
            "Omnigent result requires MoonMind output artifact refs"
        )
    if not diagnostics_ref:
        raise OmnigentContractError(
            "Omnigent result requires a MoonMind diagnostics artifact ref"
        )
    _assert_no_provider_native_refs(
        [*output_refs, diagnostics_ref, *capture_bundle.metadata_refs.values()]
    )
    failure_class = (
        classify_omnigent_failure(
            failure_reason,
            require_full_evidence=require_full_evidence,
        )
        if failure_reason is not None
        else failure_class_for_terminal_status(terminal_status)
    )
    retry_recommendation = None
    task_error = final_snapshot.get("last_task_error")
    if terminal_status == "failed" and isinstance(task_error, dict):
        failure_summary = failure_summary or task_error.get("message")
        provider_error_code = provider_error_code or task_error.get("code")
    if terminal_status == "failed":
        provider_error_code = provider_error_code or final_snapshot.get(
            "providerErrorCode"
        )
        # Omnigent's native status wire contract uses this code for all native
        # harnesses, including OpenCode. Interpret it at the provider boundary;
        # the workflow consumes the canonical retry recommendation.
        if provider_error_code == "codex_reauth_required":
            failure_class = classify_omnigent_failure(
                OmnigentFailureReason.AUTH_FAILURE
            )
            retry_recommendation = "reauthenticate"

    # A classified failure must never be summarized with the provider's
    # success snapshot text (for example a full-evidence harvest escalation on
    # a "completed" session whose snapshot summary still says "done"). Prefer an
    # explicit failure summary so operators are not told a failed run succeeded.
    if failure_class is not None and failure_summary:
        summary = failure_summary
    else:
        summary = final_snapshot.get("summary") or failure_summary
    if not summary:
        summary = (
            "Omnigent session completed"
            if terminal_status == "completed"
            else "Omnigent session failed"
        )

    metadata = {
        "providerName": "omnigent",
        "normalizedStatus": terminal_status,
        "omnigentSessionId": session_id,
        "idempotencyKey": request.idempotency_key,
        "sseEventsCaptured": event_count,
        "correlationId": request.correlation_id,
    }
    if agent_id:
        metadata["omnigentAgentId"] = agent_id
    if capture_bundle.capture_manifest_ref:
        metadata["captureManifestRef"] = capture_bundle.capture_manifest_ref
    if capture_bundle.external_state_ref:
        metadata["externalStateRef"] = capture_bundle.external_state_ref
        metadata["stateCheckpointRef"] = capture_bundle.external_state_ref
        metadata["checkpointKind"] = "external_state_ref"
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    request_metadata = parameters.get("metadata")
    moonmind_metadata = (
        request_metadata.get("moonmind") if isinstance(request_metadata, dict) else None
    )
    initial_context_pack_ref = (
        str(moonmind_metadata.get("latestContextPackRef") or "").strip()
        if isinstance(moonmind_metadata, dict)
        else ""
    )
    if initial_context_pack_ref:
        metadata["initialContextPackRef"] = initial_context_pack_ref
    metadata.update(capture_bundle.metadata_refs)
    snapshot_metadata_keys = {
        "omnigentAgentName": "omnigent_agent_name",
        "hostType": "host_type",
        "workspace": "workspace",
        "githubPrUrl": "github_pr_url",
    }
    for metadata_key, snake_key in snapshot_metadata_keys.items():
        value = final_snapshot.get(metadata_key) or final_snapshot.get(snake_key)
        if value:
            metadata[metadata_key] = str(value)

    return AgentRunResult(
        outputRefs=output_refs,
        summary=_compact_summary(
            summary,
            fallback="Omnigent session reached a terminal status",
        ),
        diagnosticsRef=str(diagnostics_ref),
        failureClass=failure_class,
        providerErrorCode=provider_error_code,
        retryRecommendation=retry_recommendation,
        metadata=metadata,
    )


def build_omnigent_terminal_refs(
    capture_bundle: OmnigentCaptureBundle,
    *,
    terminal_status: str,
    final_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Build bridge-store terminal refs from published MoonMind artifacts."""

    output_refs = list(capture_bundle.output_refs)
    diagnostics_ref = capture_bundle.diagnostics_ref
    metadata_refs = dict(capture_bundle.metadata_refs)
    _assert_no_provider_native_refs(
        [*output_refs, diagnostics_ref, *metadata_refs.values()]
    )
    summary = _compact_summary(
        final_snapshot.get("summary"),
        fallback=(
            "Omnigent session completed"
            if terminal_status == "completed"
            else "Omnigent session failed"
        ),
    )
    required_evidence_failed = (
        terminal_status == "completed"
        and capture_bundle.resource_harvest_failure_class is not None
    )
    return {
        "outputRefs": output_refs,
        "diagnosticsRef": diagnostics_ref,
        "metadataRefs": metadata_refs,
        "resourceProjection": dict(capture_bundle.resource_projection),
        "failureClass": (
            capture_bundle.resource_harvest_failure_class
            if required_evidence_failed
            else failure_class_for_terminal_status(terminal_status)
        ),
        "failureCode": (
            "omnigent_required_resource_evidence_missing"
            if required_evidence_failed
            else final_snapshot.get("providerErrorCode")
            or final_snapshot.get("failureCode")
        ),
        "summary": (
            "Required Omnigent resource evidence was missing after session "
            "completion"
            if required_evidence_failed
            else summary
        ),
    }


def _assert_no_provider_native_refs(refs: list[str]) -> None:
    bad = [ref for ref in refs if str(ref).startswith("omnigent://")]
    if bad:
        raise OmnigentContractError(
            "Omnigent terminal result cannot expose provider-native refs"
        )


class BridgeResourceHarvester:
    """Harvest Omnigent resources into MoonMind-owned artifact refs."""

    def __init__(
        self,
        *,
        client: Any,
        artifact_gateway: OmnigentArtifactGateway,
        request: AgentExecutionRequest,
        session_id: str,
        manifest: dict[str, Any],
        refs: dict[str, str],
    ) -> None:
        self._client = client
        self._artifact_gateway = artifact_gateway
        self._request = request
        self._session_id = session_id
        self._manifest = manifest
        self._refs = refs

    async def harvest_child_sessions(self, raw_events: list[dict[str, Any]]) -> None:
        child_session_ids = _child_session_ids(
            raw_events,
            parent_session_id=self._session_id,
        )
        self._manifest["childSessions"] = len(child_session_ids)
        if not child_session_ids:
            return
        child_ref = await self._artifact_gateway.write_text(
            request=self._request,
            name="runtime.omnigent.child_sessions.jsonl",
            payload=_jsonl(
                [
                    {"childSessionId": child_session_id}
                    for child_session_id in child_session_ids
                ]
            ),
            link_type="runtime.omnigent.child_sessions",
            content_type="application/x-ndjson",
        )
        self._refs["childSessionsRef"] = child_ref
        self._manifest["childSessionsRef"] = child_ref
        child_snapshots: list[dict[str, str]] = []
        if self._client is not None:
            for child_session_id in child_session_ids:
                try:
                    child_snapshot = await self._client.get_session(child_session_id)
                except Exception as exc:
                    child_snapshots.append(
                        {
                            "childSessionId": child_session_id,
                            "unavailable": _compact_summary(
                                exc,
                                fallback="child session snapshot unavailable",
                            ),
                        }
                    )
                    continue
                child_snapshot_ref = await capture_artifact_json(
                    self._artifact_gateway,
                    self._request,
                    self._refs,
                    key=f"childSessionSnapshotRef:{child_session_id}",
                    name=f"runtime.omnigent.child_sessions/{child_session_id}.json",
                    payload=child_snapshot,
                    link_type="runtime.omnigent.child_session.snapshot",
                )
                child_snapshots.append(
                    {
                        "childSessionId": child_session_id,
                        "snapshotRef": child_snapshot_ref,
                    }
                )
        self._manifest["childSessionEvidence"] = child_snapshots

    async def harvest_resources(
        self,
        *,
        capture_policy: dict[str, Any] | None,
    ) -> None:
        changed_items: list[dict[str, Any]] = []
        if _capture_enabled(capture_policy, "changedFiles"):
            changed_items = await self.harvest_changed_files()
        if _capture_enabled(capture_policy, "workspaceFiles"):
            await self.harvest_workspace_files()
        await self.harvest_workspace_diffs(changed_items=changed_items)
        if _capture_enabled(capture_policy, "sessionFiles"):
            await self.harvest_session_files()
        _reconcile_changed_file_evidence(self._manifest)

    async def harvest_changed_files(self) -> list[dict[str, Any]]:
        try:
            changed = await self._client.list_changed_files(self._session_id)
        except Exception as exc:
            self._manifest["changedFilesUnavailable"] = _compact_summary(
                exc,
                fallback="changed files unavailable",
            )
            return []
        index_ref = await capture_artifact_json(
            self._artifact_gateway,
            self._request,
            self._refs,
            key="changedFilesIndexRef",
            name="output.omnigent.changed_files.index.json",
            payload=changed,
            link_type="output.omnigent.changed_files.index",
        )
        self._manifest["changedFilesIndexRef"] = index_ref
        file_items = _resource_items(changed)[:_MAX_OMNIGENT_HARVEST_ITEMS]
        harvested: list[dict[str, Any]] = []
        for item in file_items:
            path = str(
                item.get("path")
                or item.get("file_path")
                or item.get("filePath")
                or item.get("name")
                or ""
            ).strip()
            if not path:
                continue
            try:
                content = await self._client.get_workspace_file(self._session_id, path)
            except Exception as exc:
                harvested.append(
                    {
                        "path": path,
                        "unavailable": _compact_summary(
                            exc,
                            fallback="changed file content unavailable",
                        ),
                    }
                )
                continue
            if unavailable := _content_limit_reason(content):
                harvested.append({"path": path, "unavailable": unavailable})
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.changed_files/{path}",
                payload=content,
                link_type="output.omnigent.changed_file",
            )
            harvested.append({"path": path, "artifactRef": ref})
        self._manifest["changedFiles"] = harvested
        self._manifest.setdefault("patchUnavailable", True)
        return file_items

    async def harvest_workspace_files(self) -> None:
        try:
            files = await self._client.list_workspace_files(self._session_id)
        except Exception as exc:
            self._manifest["workspaceFilesUnavailable"] = _compact_summary(
                exc,
                fallback="workspace files unavailable",
            )
            return
        index_ref = await capture_artifact_json(
            self._artifact_gateway,
            self._request,
            self._refs,
            key="workspaceFilesIndexRef",
            name="output.omnigent.workspace_files.index.json",
            payload=files,
            link_type="output.omnigent.workspace_files.index",
        )
        self._manifest["workspaceFilesIndexRef"] = index_ref
        harvested: list[dict[str, Any]] = []
        for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
            path = _resource_path(item)
            if not path:
                continue
            if str(item.get("type") or item.get("kind") or "").strip().lower() in {
                "dir",
                "directory",
                "folder",
            }:
                harvested.append({"path": path, "skipped": "directory"})
                continue
            try:
                content = await self._client.get_workspace_file(self._session_id, path)
            except Exception as exc:
                harvested.append(
                    {
                        "path": path,
                        "unavailable": _compact_summary(
                            exc,
                            fallback="workspace file content unavailable",
                        ),
                    }
                )
                continue
            if unavailable := _content_limit_reason(content):
                harvested.append({"path": path, "unavailable": unavailable})
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.workspace_files/{path}",
                payload=content,
                link_type="output.omnigent.workspace_file",
            )
            harvested.append({"path": path, "artifactRef": ref})
        self._manifest["workspaceFiles"] = harvested

    async def harvest_workspace_diffs(
        self,
        *,
        changed_items: list[dict[str, Any]],
    ) -> None:
        paths = [
            path for path in (_resource_path(item) for item in changed_items) if path
        ][:_MAX_OMNIGENT_HARVEST_ITEMS]
        if not paths:
            self._manifest["workspaceDiffs"] = []
            self._manifest["patchUnavailable"] = True
            return
        harvested: list[dict[str, Any]] = []
        for path in paths:
            try:
                diff = await self._client.get_workspace_diff(self._session_id, path)
            except Exception as exc:
                self._manifest["workspaceDiffsUnavailable"] = _compact_summary(
                    exc,
                    fallback="workspace diff capability unavailable",
                )
                self._manifest["patchUnavailable"] = True
                return
            if unavailable := _content_limit_reason(diff):
                harvested.append({"path": path, "unavailable": unavailable})
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.workspace_diffs/{path}.diff",
                payload=diff,
                link_type="output.omnigent.workspace_diff",
                content_type="text/x-diff",
            )
            harvested.append({"path": path, "artifactRef": ref})
        self._manifest["workspaceDiffs"] = harvested
        self._manifest["patchUnavailable"] = not bool(harvested)

    async def harvest_session_files(self) -> None:
        try:
            files = await self._client.list_session_files(self._session_id)
        except Exception as exc:
            self._manifest["sessionFilesUnavailable"] = _compact_summary(
                exc,
                fallback="session files unavailable",
            )
            return
        index_ref = await capture_artifact_json(
            self._artifact_gateway,
            self._request,
            self._refs,
            key="sessionFilesIndexRef",
            name="output.omnigent.session_files.index.json",
            payload=files,
            link_type="output.omnigent.session_files.index",
        )
        self._manifest["sessionFilesIndexRef"] = index_ref
        harvested: list[dict[str, Any]] = []
        for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
            file_id = str(
                item.get("id") or item.get("file_id") or item.get("fileId") or ""
            ).strip()
            filename = str(item.get("filename") or item.get("name") or file_id).strip()
            if not file_id:
                continue
            try:
                content = await self._client.get_session_file_content(
                    self._session_id,
                    file_id,
                )
            except Exception as exc:
                harvested.append(
                    {
                        "fileId": file_id,
                        "filename": filename,
                        "unavailable": _compact_summary(
                            exc,
                            fallback="session file content unavailable",
                        ),
                    }
                )
                continue
            if unavailable := _content_limit_reason(content):
                harvested.append(
                    {
                        "fileId": file_id,
                        "filename": filename,
                        "unavailable": unavailable,
                    }
                )
                continue
            ref = await self._artifact_gateway.write_bytes(
                request=self._request,
                name=f"output.omnigent.session_files/{file_id}/{filename}",
                payload=content,
                link_type="output.omnigent.session_file",
            )
            metadata_ref = await capture_artifact_json(
                self._artifact_gateway,
                self._request,
                self._refs,
                key=f"sessionFileMetadataRef:{file_id}",
                name=f"output.omnigent.session_files/{file_id}/metadata.json",
                payload=item,
                link_type="output.omnigent.session_file.metadata",
            )
            harvested.append(
                {
                    "fileId": file_id,
                    "filename": filename,
                    "artifactRef": ref,
                    "metadataRef": metadata_ref,
                }
            )
        self._manifest["sessionFiles"] = harvested


def _capture_enabled(capture_policy: dict[str, Any] | None, key: str) -> bool:
    if capture_policy is None:
        return True
    return bool(capture_policy.get(key, True))


async def _capture_read(
    read: Callable[..., Awaitable[Any]], *args: Any, deadline: float
) -> Any:
    """Spend one shared remote-read budget without timing out artifact writes."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Omnigent resource harvest deadline exceeded")
    try:
        return await asyncio.wait_for(read(*args), timeout=remaining)
    except TimeoutError as exc:
        raise TimeoutError("Omnigent resource harvest deadline exceeded") from exc


async def _harvest_changed_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
    deadline: float,
) -> list[dict[str, Any]]:
    try:
        changed = await _capture_read(
            client.list_changed_files, session_id, deadline=deadline
        )
    except Exception as exc:
        manifest["changedFilesUnavailable"] = _compact_summary(
            exc,
            fallback="changed files unavailable",
        )
        return []
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="changedFilesIndexRef",
        name="output.omnigent.changed_files.index.json",
        payload=changed,
        link_type="output.omnigent.changed_files.index",
    )
    manifest["changedFilesIndexRef"] = index_ref
    file_items = _resource_items(changed)[:_MAX_OMNIGENT_HARVEST_ITEMS]
    harvested: list[dict[str, Any]] = []
    for item in file_items:
        path = str(
            item.get("path")
            or item.get("file_path")
            or item.get("filePath")
            or item.get("name")
            or ""
        ).strip()
        if not path:
            continue
        try:
            content = await _capture_read(
                client.get_workspace_file, session_id, path, deadline=deadline
            )
        except Exception as exc:
            harvested.append(
                {
                    "path": path,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="changed file content unavailable",
                    ),
                }
            )
            continue
        if unavailable := _content_limit_reason(content):
            harvested.append({"path": path, "unavailable": unavailable})
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.changed_files/{path}",
            payload=content,
            link_type="output.omnigent.changed_file",
        )
        harvested.append(_harvested_resource(path, ref, content))
    manifest["changedFiles"] = harvested
    manifest.setdefault("patchUnavailable", True)
    return file_items


async def _harvest_workspace_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
    deadline: float,
) -> None:
    try:
        files = await _capture_read(
            client.list_workspace_files, session_id, deadline=deadline
        )
    except Exception as exc:
        manifest["workspaceFilesUnavailable"] = _compact_summary(
            exc,
            fallback="workspace files unavailable",
        )
        return
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="workspaceFilesIndexRef",
        name="output.omnigent.workspace_files.index.json",
        payload=files,
        link_type="output.omnigent.workspace_files.index",
    )
    manifest["workspaceFilesIndexRef"] = index_ref
    harvested: list[dict[str, Any]] = []
    for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
        path = _resource_path(item)
        if not path:
            continue
        if str(item.get("type") or item.get("kind") or "").strip().lower() in {
            "dir",
            "directory",
            "folder",
        }:
            harvested.append({"path": path, "skipped": "directory"})
            continue
        try:
            content = await _capture_read(
                client.get_workspace_file, session_id, path, deadline=deadline
            )
        except Exception as exc:
            harvested.append(
                {
                    "path": path,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="workspace file content unavailable",
                    ),
                }
            )
            continue
        if unavailable := _content_limit_reason(content):
            harvested.append({"path": path, "unavailable": unavailable})
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.workspace_files/{path}",
            payload=content,
            link_type="output.omnigent.workspace_file",
        )
        harvested.append(_harvested_resource(path, ref, content))
    manifest["workspaceFiles"] = harvested


async def _harvest_workspace_diffs(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    changed_items: list[dict[str, Any]],
    manifest: dict[str, Any],
    refs: dict[str, str],
    deadline: float,
) -> None:
    paths = [path for path in (_resource_path(item) for item in changed_items) if path][
        :_MAX_OMNIGENT_HARVEST_ITEMS
    ]
    if not paths:
        manifest["workspaceDiffs"] = []
        manifest["patchUnavailable"] = True
        return
    harvested: list[dict[str, Any]] = []
    for path in paths:
        try:
            diff = await _capture_read(
                client.get_workspace_diff, session_id, path, deadline=deadline
            )
        except Exception as exc:
            manifest["workspaceDiffs"] = harvested
            manifest["workspaceDiffsUnavailable"] = _compact_summary(
                exc,
                fallback="workspace diff capability unavailable",
            )
            manifest["patchUnavailable"] = not any(
                item.get("artifactRef") for item in harvested
            )
            return
        if unavailable := _content_limit_reason(diff):
            harvested.append({"path": path, "unavailable": unavailable})
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.workspace_diffs/{path}.diff",
            payload=diff,
            link_type="output.omnigent.workspace_diff",
            content_type="text/x-diff",
        )
        harvested.append(
            _harvested_resource(path, ref, diff, content_type="text/x-diff")
        )
    manifest["workspaceDiffs"] = harvested
    manifest["patchUnavailable"] = not any(item.get("artifactRef") for item in harvested)


async def _harvest_session_files(
    *,
    client: OmnigentHttpClient,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    manifest: dict[str, Any],
    refs: dict[str, str],
    deadline: float,
) -> None:
    try:
        files = await _capture_read(
            client.list_session_files, session_id, deadline=deadline
        )
    except Exception as exc:
        manifest["sessionFilesUnavailable"] = _compact_summary(
            exc,
            fallback="session files unavailable",
        )
        return
    index_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="sessionFilesIndexRef",
        name="output.omnigent.session_files.index.json",
        payload=files,
        link_type="output.omnigent.session_files.index",
    )
    manifest["sessionFilesIndexRef"] = index_ref
    harvested: list[dict[str, Any]] = []
    for item in _resource_items(files)[:_MAX_OMNIGENT_HARVEST_ITEMS]:
        file_id = str(
            item.get("id") or item.get("file_id") or item.get("fileId") or ""
        ).strip()
        filename = str(item.get("filename") or item.get("name") or file_id).strip()
        if not file_id:
            continue
        try:
            content = await _capture_read(
                client.get_session_file_content, session_id, file_id, deadline=deadline
            )
        except Exception as exc:
            harvested.append(
                {
                    "fileId": file_id,
                    "filename": filename,
                    "unavailable": _compact_summary(
                        exc,
                        fallback="session file content unavailable",
                    ),
                }
            )
            continue
        if unavailable := _content_limit_reason(content):
            harvested.append(
                {
                    "fileId": file_id,
                    "filename": filename,
                    "unavailable": unavailable,
                }
            )
            continue
        ref = await artifact_gateway.write_bytes(
            request=request,
            name=f"output.omnigent.session_files/{file_id}/{filename}",
            payload=content,
            link_type="output.omnigent.session_file",
        )
        metadata_ref = await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key=f"sessionFileMetadataRef:{file_id}",
            name=f"output.omnigent.session_files/{file_id}/metadata.json",
            payload=item,
            link_type="output.omnigent.session_file.metadata",
        )
        harvested.append(
            {
                "fileId": file_id,
                "filename": filename,
                "artifactRef": ref,
                "metadataRef": metadata_ref,
                "contentType": _resource_content_type(filename),
                "sizeBytes": len(content),
            }
        )
    manifest["sessionFiles"] = harvested


def _resource_content_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _harvested_resource(
    path: str,
    artifact_ref: str,
    content: bytes,
    *,
    content_type: str | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "artifactRef": artifact_ref,
        "contentType": content_type or _resource_content_type(path),
        "sizeBytes": len(content),
    }


def _resource_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "files", "changes", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _resource_items(value)
            if nested:
                return nested
    return []


def _resource_path(item: dict[str, Any]) -> str:
    return (
        str(
            item.get("path")
            or item.get("file_path")
            or item.get("filePath")
            or item.get("relativePath")
            or item.get("name")
            or ""
        )
        .strip()
        .strip("/")
    )


def _child_session_ids(
    events: list[dict[str, Any]],
    *,
    parent_session_id: str,
) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        event_type = str(event.get("type") or event.get("eventType") or "").lower()
        if "child" not in event_type:
            continue
        stack: list[Any] = [event]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized_key = key.replace("_", "").lower()
                    if normalized_key in {"sessionid", "childsessionid"}:
                        candidate = str(nested or "").strip()
                        if (
                            candidate
                            and candidate != parent_session_id
                            and candidate not in seen
                        ):
                            ids.append(candidate)
                            seen.add(candidate)
                    else:
                        stack.append(nested)
            elif isinstance(value, list):
                stack.extend(value)
    return ids


def _redacted_endpoint_url(value: str | None) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return "redacted"
    if not parsed.scheme or not parsed.hostname:
        return "redacted"
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


def _omnigent_endpoint_ref(request: AgentExecutionRequest) -> str:
    parameters = request.parameters if isinstance(request.parameters, dict) else {}
    omnigent = parameters.get("omnigent")
    if isinstance(omnigent, dict):
        endpoint_ref = str(omnigent.get("endpointRef") or "").strip()
        if endpoint_ref:
            return endpoint_ref
    return "default"


def _payload_digest(payload: Any) -> str | None:
    if payload is None:
        return None
    encoded = json.dumps(
        payload,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_ref_items(items: Any) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    refs: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        artifact_ref = str(item.get("artifactRef") or "").strip()
        if not artifact_ref:
            continue
        path = str(item.get("path") or item.get("filename") or "").strip()
        compact = {"artifactRef": artifact_ref}
        if path:
            compact["path"] = path
        refs.append(compact)
    return refs


def _patch_evidence(manifest: dict[str, Any]) -> dict[str, Any]:
    diff_refs = _artifact_ref_items(manifest.get("workspaceDiffs"))
    evidence: dict[str, Any] = {
        "diffRefs": diff_refs,
        "patchUnavailable": bool(manifest.get("patchUnavailable", not diff_refs)),
    }
    diagnostics: list[dict[str, str]] = []
    if evidence["patchUnavailable"]:
        diagnostics.append(
            {
                "code": "omnigent_patch_unavailable",
                "message": (
                    "Omnigent patch evidence is unavailable; "
                    "see captured diff refs or diagnostics."
                ),
            }
        )
    unavailable = str(manifest.get("workspaceDiffsUnavailable") or "").strip()
    if unavailable:
        diagnostics.append(
            {
                "code": "omnigent_workspace_diffs_unavailable",
                "message": unavailable,
            }
        )
    if diagnostics:
        evidence["diagnostics"] = diagnostics
    return evidence


def _reconcile_changed_file_evidence(manifest: dict[str, Any]) -> None:
    """Durably associate each changed file with its harvested diff outcome."""

    diffs_by_path = {
        str(item.get("path") or ""): str(item.get("artifactRef") or "")
        for item in (manifest.get("workspaceDiffs") or [])
        if isinstance(item, dict) and item.get("path") and item.get("artifactRef")
    }
    unavailable = str(
        manifest.get("workspaceDiffsUnavailable")
        or "No diff artifact was published for this changed file."
    )
    for changed_file in manifest.get("changedFiles") or []:
        if not isinstance(changed_file, dict) or not changed_file.get("path"):
            continue
        diff_ref = diffs_by_path.get(str(changed_file["path"]))
        if diff_ref:
            changed_file["diffArtifactRef"] = diff_ref
            changed_file.pop("diffUnavailable", None)
        else:
            changed_file["diffUnavailable"] = unavailable


def _associate_resource_events(
    manifest: dict[str, Any], normalized_events: list[dict[str, Any]]
) -> None:
    """Attach harvested changed files to the durable announcing event sequence."""

    sequence_by_path: dict[tuple[str, str], int] = {}
    for event in normalized_events:
        event_type = str(event.get("type") or event.get("eventType") or "")
        if event_type not in {"resource.changed_file", "resource.session_file"}:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        path = _resource_path(data)
        sequence = event.get("sequence")
        if path and isinstance(sequence, int):
            sequence_by_path.setdefault((event_type, path), sequence)
    for changed_file in manifest.get("changedFiles") or []:
        if not isinstance(changed_file, dict):
            continue
        sequence = sequence_by_path.get(
            ("resource.changed_file", str(changed_file.get("path") or ""))
        )
        if sequence is not None:
            changed_file["sourceEventSequence"] = sequence
    for session_file in manifest.get("sessionFiles") or []:
        if not isinstance(session_file, dict):
            continue
        sequence = sequence_by_path.get(
            ("resource.session_file", str(session_file.get("filename") or ""))
        )
        if sequence is not None:
            session_file["sourceEventSequence"] = sequence


def _capture_resource_groups(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the manifest into stable UI-oriented evidence groups."""

    definitions = (
        ("changed_files", "Changed files", "changedFiles"),
        ("diffs", "Diffs", "workspaceDiffs"),
        ("workspace_files", "Workspace files", "workspaceFiles"),
        ("session_files", "Session files", "sessionFiles"),
        ("snapshots", "Snapshots", "snapshotEvidence"),
        ("logs_and_journals", "Logs and event journals", "journalEvidence"),
        ("diagnostics", "Diagnostics", "diagnosticEvidence"),
        ("manifests", "Capture and checkpoint manifests", "manifestEvidence"),
    )
    index_items = [
        {
            "label": label,
            "artifactRef": manifest[ref_key],
            "contentType": "application/json",
        }
        for label, ref_key in (
            ("Changed-file index", "changedFilesIndexRef"),
            ("Workspace-file index", "workspaceFilesIndexRef"),
            ("Session-file index", "sessionFilesIndexRef"),
        )
        if manifest.get(ref_key)
    ]
    unavailable_by_group = {
        "changed_files": "changedFilesUnavailable",
        "diffs": "workspaceDiffsUnavailable",
        "workspace_files": "workspaceFilesUnavailable",
        "session_files": "sessionFilesUnavailable",
    }
    groups: list[dict[str, Any]] = []
    for key, title, manifest_key in definitions:
        raw_items = manifest.get(manifest_key, [])
        items = list(raw_items) if isinstance(raw_items, list) else []
        if key == "manifests":
            items.extend(index_items)
        unavailable_key = unavailable_by_group.get(key)
        if unavailable_key and manifest.get(unavailable_key):
            items.append(
                {
                    "label": f"{title} unavailable",
                    "unavailable": str(manifest[unavailable_key]),
                }
            )
        groups.append({"groupKey": key, "title": title, "items": items})
    return groups


def _capture_resource_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded, artifact-ref-only projection consumed by Workflow Detail."""

    groups: list[dict[str, Any]] = []
    for group in _capture_resource_groups(manifest):
        resources: list[dict[str, Any]] = []
        for item in group["items"][:_MAX_OMNIGENT_HARVEST_ITEMS]:
            if not isinstance(item, dict):
                continue
            artifact_ref = str(
                item.get("artifactRef") or item.get("snapshotRef") or ""
            ).strip()
            path = str(item.get("path") or item.get("filename") or "").strip()
            label = str(item.get("label") or path or group["title"]).strip()
            unavailable_reason = str(
                item.get("unavailable")
                or item.get("skipped")
                or item.get("diffUnavailable")
                or ""
            ).strip()
            resource: dict[str, Any] = {
                "label": label[:512],
                "status": (
                    "available"
                    if artifact_ref
                    else "unavailable" if unavailable_reason else "pending"
                ),
                "previewAvailable": bool(artifact_ref)
                and not artifact_ref.startswith("artifact://"),
                "downloadAvailable": bool(artifact_ref)
                and not artifact_ref.startswith("artifact://"),
            }
            for key in ("contentType", "sizeBytes", "sourceEventSequence"):
                if item.get(key) is not None:
                    resource[key] = item[key]
            if artifact_ref:
                resource["artifactRef"] = artifact_ref
            if path:
                resource["path"] = path[:512]
            if unavailable_reason:
                resource["unavailableReason"] = unavailable_reason[:512]
            related = [
                str(item[key])
                for key in ("metadataRef", "diffArtifactRef")
                if item.get(key)
            ]
            if related:
                resource["relatedArtifactRefs"] = related
            resources.append(resource)
        groups.append(
            {
                "groupKey": group["groupKey"],
                "title": group["title"],
                "resources": resources,
            }
        )
    completeness = dict(manifest.get("evidenceCompleteness") or {})
    return {
        "schemaVersion": _RESOURCE_PROJECTION_SCHEMA_VERSION,
        "completeness": completeness.get("status", "complete"),
        "unavailableReasons": dict(completeness.get("unavailableReasons") or {}),
        "groups": groups,
    }


async def _build_capture_bundle_impl(
    *,
    client: OmnigentHttpClient | None,
    artifact_gateway: OmnigentArtifactGateway,
    request: AgentExecutionRequest,
    session_id: str,
    agent_id: str | None,
    initial_snapshot: dict[str, Any] | None,
    final_snapshot: dict[str, Any],
    first_message_request: dict[str, Any] | None,
    first_message_response: dict[str, Any] | None,
    first_message_posted: bool,
    first_message_response_identifiers: dict[str, str] | None,
    raw_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    terminal_status: str,
    diagnostics: dict[str, Any],
    harvest_resources: bool,
    external_state: dict[str, Any] | None = None,
    capture_policy: dict[str, Any] | None = None,
) -> OmnigentCaptureBundle:
    refs: dict[str, str] = {}
    stream_enabled = _capture_enabled(capture_policy, "stream")
    evidence_enabled = _capture_enabled(capture_policy, "evidence")
    if evidence_enabled and first_message_request is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="firstMessageRequestRef",
            name="input.omnigent.first_message.request.json",
            payload=first_message_request,
            link_type="input.omnigent.first_message.request",
        )
    if evidence_enabled and first_message_response is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="firstMessageResponseRef",
            name="input.omnigent.first_message.response.json",
            payload=first_message_response,
            link_type="input.omnigent.first_message.response",
        )
    if evidence_enabled and initial_snapshot is not None:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="initialSnapshotRef",
            name="runtime.omnigent.snapshot.initial.json",
            payload=initial_snapshot,
            link_type="runtime.omnigent.snapshot.initial",
        )
    # §16 rule 5: redact secret-like fields on the raw-event persistence path
    # so the artifact system stays a safe evidence boundary.
    if stream_enabled:
        refs["rawSseStreamRef"] = await artifact_gateway.write_text(
            request=request,
            name="runtime.omnigent.sse.raw.jsonl",
            payload=_jsonl(redact_raw_events(raw_events)),
            link_type="runtime.omnigent.sse.raw",
            content_type="application/x-ndjson",
        )
        refs["normalizedEventStreamRef"] = await artifact_gateway.write_text(
            request=request,
            name="runtime.omnigent.sse.normalized.jsonl",
            payload=_jsonl(normalized_events),
            link_type="runtime.omnigent.sse.normalized",
            content_type="application/x-ndjson",
        )
    if evidence_enabled:
        await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="finalSnapshotRef",
            name="output.omnigent.snapshot.final.json",
            payload=final_snapshot,
            link_type="output.omnigent.snapshot.final",
        )
    manifest: dict[str, Any] = {
        "schemaVersion": _CAPTURE_MANIFEST_SCHEMA_VERSION,
        "sourceIssue": "MoonLadderStudios/MoonMind#3365",
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "omnigentAgentId": agent_id,
        "terminalStatus": terminal_status,
        "artifactRefs": refs,
        "patchUnavailable": True,
        "capturePolicy": {
            "requested": dict(capture_policy or {}),
            "limits": {
                "maxListEntries": _MAX_OMNIGENT_HARVEST_ITEMS,
                "maxHarvestedFiles": _MAX_OMNIGENT_HARVEST_ITEMS,
                "maxContentBytes": _MAX_OMNIGENT_CONTENT_BYTES,
                "maxPreviewBytes": _MAX_OMNIGENT_PREVIEW_BYTES,
            },
            "supportedContentTypes": [
                "text/*",
                "application/json",
                "application/octet-stream",
            ],
            "binaryHandling": "metadata_and_authorized_download; preview_when_supported",
            "timeoutSeconds": _OMNIGENT_HARVEST_TIMEOUT_SECONDS,
            "retry": {"maxAttempts": _OMNIGENT_HARVEST_MAX_ATTEMPTS},
            "optionalEvidenceFailureIsFatal": _capture_requires_full_evidence(
                capture_policy
            ),
        },
    }
    # All remote evidence shares the declared capture deadline. Artifact
    # publication remains outside its cancellation scope and keeps saved work.
    harvest_deadline = time.monotonic() + _OMNIGENT_HARVEST_TIMEOUT_SECONDS
    child_session_ids = _child_session_ids(raw_events, parent_session_id=session_id)
    manifest["childSessions"] = len(child_session_ids)
    if evidence_enabled and child_session_ids:
        child_ref = await artifact_gateway.write_text(
            request=request,
            name="runtime.omnigent.child_sessions.jsonl",
            payload=_jsonl(
                [
                    {"childSessionId": child_session_id}
                    for child_session_id in child_session_ids
                ]
            ),
            link_type="runtime.omnigent.child_sessions",
            content_type="application/x-ndjson",
        )
        refs["childSessionsRef"] = child_ref
        manifest["childSessionsRef"] = child_ref
        child_snapshots: list[dict[str, str]] = []
        if client is not None:
            for child_session_id in child_session_ids:
                try:
                    child_snapshot = await _capture_read(
                        client.get_session, child_session_id, deadline=harvest_deadline
                    )
                except Exception as exc:
                    child_snapshots.append(
                        {
                            "childSessionId": child_session_id,
                            "unavailable": _compact_summary(
                                exc,
                                fallback="child session snapshot unavailable",
                            ),
                        }
                    )
                    continue
                child_snapshot_ref = await _capture_artifact_json(
                    artifact_gateway,
                    request,
                    refs,
                    key=f"childSessionSnapshotRef:{child_session_id}",
                    name=f"runtime.omnigent.child_sessions/{child_session_id}.json",
                    payload=child_snapshot,
                    link_type="runtime.omnigent.child_session.snapshot",
                )
                child_snapshots.append(
                    {
                        "childSessionId": child_session_id,
                        "snapshotRef": child_snapshot_ref,
                    }
                )
        manifest["childSessionEvidence"] = child_snapshots
    if evidence_enabled and harvest_resources and client is not None and session_id:
        changed_items: list[dict[str, Any]] = []
        if _capture_enabled(capture_policy, "changedFiles"):
            changed_items = await _harvest_changed_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
                deadline=harvest_deadline,
            )
        if _capture_enabled(capture_policy, "workspaceFiles"):
            await _harvest_workspace_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
                deadline=harvest_deadline,
            )
        await _harvest_workspace_diffs(
            client=client,
            artifact_gateway=artifact_gateway,
            request=request,
            session_id=session_id,
            changed_items=changed_items,
            manifest=manifest,
            refs=refs,
            deadline=harvest_deadline,
        )
        if _capture_enabled(capture_policy, "sessionFiles"):
            await _harvest_session_files(
                client=client,
                artifact_gateway=artifact_gateway,
                request=request,
                session_id=session_id,
                manifest=manifest,
                refs=refs,
                deadline=harvest_deadline,
            )
    _associate_resource_events(manifest, normalized_events)
    _reconcile_changed_file_evidence(manifest)
    optional_harvest_failed = _optional_resource_harvest_failed(manifest)
    require_full_evidence = _capture_requires_full_evidence(capture_policy)
    resource_harvest_failure_class: str | None = None
    if optional_harvest_failed:
        resource_harvest_failure_class = classify_omnigent_failure(
            OmnigentFailureReason.OPTIONAL_RESOURCE_HARVEST_FAILED,
            require_full_evidence=require_full_evidence,
        )
        manifest["optionalResourceHarvest"] = {
            "failed": True,
            "requireFullEvidence": require_full_evidence,
            "outcome": (
                "required_evidence_missing"
                if resource_harvest_failure_class
                else "completed_with_diagnostics"
            ),
            "failureClass": resource_harvest_failure_class,
        }
    unavailable_reasons = {
        key: value
        for key, value in manifest.items()
        if key.endswith("Unavailable") and value
    }
    manifest["evidenceCompleteness"] = {
        "status": (
            "required_missing"
            if resource_harvest_failure_class
            else "degraded" if optional_harvest_failed else "complete"
        ),
        "unavailableReasons": unavailable_reasons,
    }
    diagnostics_payload = {
        "provider": "omnigent",
        "omnigentSessionId": session_id,
        "terminalStatus": terminal_status,
        "diagnostics": diagnostics,
        "captureManifest": manifest,
    }
    diagnostics_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="diagnosticsRef",
        name="diagnostics.omnigent.json",
        payload=diagnostics_payload,
        link_type="diagnostics.omnigent",
    )
    manifest["snapshotEvidence"] = [
        {"label": label, "artifactRef": refs[ref_key]}
        for label, ref_key in (
            ("Initial session snapshot", "initialSnapshotRef"),
            ("Final session snapshot", "finalSnapshotRef"),
        )
        if ref_key in refs
    ] + list(manifest.get("childSessionEvidence", []))
    manifest["journalEvidence"] = [
        {"label": label, "artifactRef": refs[ref_key]}
        for label, ref_key in (
            ("Raw event journal", "rawSseStreamRef"),
            ("Normalized event journal", "normalizedEventStreamRef"),
            ("Child-session journal", "childSessionsRef"),
        )
        if ref_key in refs
    ]
    manifest["diagnosticEvidence"] = [
        {"label": "Capture diagnostics", "artifactRef": diagnostics_ref}
    ]
    if external_state is not None:
        first_message_state = dict(external_state.get("firstMessage", {}))
        first_message_state.setdefault("requestRef", refs.get("firstMessageRequestRef"))
        first_message_state.setdefault(
            "responseRef", refs.get("firstMessageResponseRef")
        )
        first_message_state["posted"] = (
            first_message_posted or first_message_response is not None
        )
        if first_message_response_identifiers:
            first_message_state["responseIdentifiers"] = dict(
                first_message_response_identifiers
            )
        external_state_payload = {
            "sourceIssue": "MM-1077",
            "provider": "omnigent",
            "checkpointKind": "external_state_ref",
            "endpointRef": external_state.get("endpointRef"),
            "endpoint": {
                "endpointRef": _omnigent_endpoint_ref(request),
                "serverUrl": _redacted_endpoint_url(resolved_server_url()),
            },
            "correlation": {
                "correlationId": request.correlation_id,
                "idempotencyKey": request.idempotency_key,
                "omnigentSessionId": session_id,
                "omnigentAgentId": agent_id,
            },
            "omnigentSessionId": session_id,
            "providerProfileId": external_state.get("providerProfileId"),
            "credentialGeneration": external_state.get("credentialGeneration"),
            "providerLeaseRef": external_state.get("providerLeaseRef"),
            "hostBindingRef": external_state.get("hostBindingRef"),
            "hostLeaseRef": external_state.get("hostLeaseRef"),
            "omnigentHostId": external_state.get("omnigentHostId"),
            "bridgeSessionId": external_state.get("bridgeSessionId"),
            "omnigentAgentId": agent_id,
            "terminalStatus": terminal_status,
            "lastCommittedBridgeEventCursor": (
                str(len(normalized_events)) if normalized_events else None
            ),
            "firstMessage": first_message_state,
            "retry": external_state.get("retry", {}),
            "terminalReconciliation": external_state.get("terminalReconciliation"),
            "reattachState": {
                "idempotencyKey": request.idempotency_key,
                "initialSnapshotRef": refs.get("initialSnapshotRef"),
                "initialSnapshotObserved": initial_snapshot is not None,
            },
            "streamRefs": {
                "rawSseStreamRef": refs.get("rawSseStreamRef"),
                "normalizedEventStreamRef": refs.get("normalizedEventStreamRef"),
            },
            "snapshotRefs": {
                "initialSnapshotRef": refs.get("initialSnapshotRef"),
                "finalSnapshotRef": refs.get("finalSnapshotRef"),
            },
            "terminalResultRefs": {
                "outputRefs": [
                    ref
                    for ref in (
                        refs.get("finalSnapshotRef"),
                        refs.get("normalizedEventStreamRef"),
                    )
                    if ref
                ],
                "finalSnapshotRef": refs.get("finalSnapshotRef"),
                "diagnosticsRef": diagnostics_ref,
                "terminalStatus": terminal_status,
            },
            "patchEvidence": _patch_evidence(manifest),
            "artifactRefs": {
                key: refs[key]
                for key in (
                    "initialSnapshotRef",
                    "finalSnapshotRef",
                    "rawSseStreamRef",
                    "normalizedEventStreamRef",
                    "diagnosticsRef",
                )
                if key in refs
            },
        }
        external_state_payload = {
            key: value
            for key, value in external_state_payload.items()
            if value is not None
        }
        external_state_ref = await _capture_artifact_json(
            artifact_gateway,
            request,
            refs,
            key="externalStateRef",
            name="checkpoint.omnigent.external_state.json",
            payload=external_state_payload,
            link_type="checkpoint.omnigent.external_state_ref",
        )
        manifest["externalStateRef"] = external_state_ref
    manifest["manifestEvidence"] = [
        {
            "label": "External-state checkpoint",
            "artifactRef": refs["externalStateRef"],
        }
        for _ in (0,)
        if "externalStateRef" in refs
    ]
    manifest["resourceGroups"] = _capture_resource_groups(manifest)
    manifest_ref = await _capture_artifact_json(
        artifact_gateway,
        request,
        refs,
        key="captureManifestRef",
        name="output.omnigent.capture_manifest.json",
        payload=manifest,
        link_type="output.omnigent.capture_manifest",
    )
    resource_projection = _capture_resource_projection(manifest)
    manifest_group = next(
        group
        for group in resource_projection["groups"]
        if group["groupKey"] == "manifests"
    )
    manifest_group["resources"].append(
        {
            "label": "Capture manifest",
            "artifactRef": manifest_ref,
            "status": "available",
            "previewAvailable": True,
            "downloadAvailable": True,
        }
    )
    metadata_refs = {"captureManifestRef": manifest_ref}
    for key in (
        "rawSseStreamRef",
        "normalizedEventStreamRef",
        "finalSnapshotRef",
    ):
        if key in refs:
            metadata_refs[key] = refs[key]
    if "externalStateRef" in refs:
        metadata_refs["externalStateRef"] = refs["externalStateRef"]
        metadata_refs["checkpointKind"] = "external_state_ref"
    for optional_key in (
        "firstMessageRequestRef",
        "firstMessageResponseRef",
        "initialSnapshotRef",
        "changedFilesIndexRef",
        "workspaceFilesIndexRef",
        "sessionFilesIndexRef",
        "childSessionsRef",
        "externalStateRef",
    ):
        if optional_key in refs:
            metadata_refs[optional_key] = refs[optional_key]
    output_refs = [
        ref
        for ref in (
            refs.get("finalSnapshotRef"),
            refs.get("normalizedEventStreamRef"),
            manifest_ref,
        )
        if ref
    ]
    return OmnigentCaptureBundle(
        output_refs=output_refs,
        diagnostics_ref=diagnostics_ref,
        capture_manifest_ref=manifest_ref,
        external_state_ref=refs.get("externalStateRef", ""),
        metadata_refs=metadata_refs,
        optional_harvest_failed=optional_harvest_failed,
        resource_harvest_failure_class=resource_harvest_failure_class,
        resource_projection=resource_projection,
    )


async def _build_capture_bundle(**kwargs: Any) -> OmnigentCaptureBundle:
    """Harvest and publish evidence at one observable Activity-side boundary."""

    started = time.monotonic()
    with control_plane_spans.omnigent_span(
        control_plane_spans.EVIDENCE_HARVEST,
        runtime="omnigent",
        provider_status_class=str(kwargs.get("terminal_status") or "unknown"),
    ):
        bundle = await _build_capture_bundle_impl(**kwargs)
    elapsed = time.monotonic() - started
    control_plane_metrics.observe(
        control_plane_metrics.EVIDENCE_HARVEST_LATENCY, elapsed
    )
    control_plane_metrics.observe(
        control_plane_metrics.EVIDENCE_PUBLICATION_LATENCY, elapsed
    )
    return bundle


def _jsonl(events: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(event, sort_keys=True, default=str, separators=(",", ":")) + "\n"
        for event in events
    )


__all__ = [
    "BridgeResourceHarvester",
    "LocalOmnigentArtifactGateway",
    "OmnigentArtifactError",
    "OmnigentArtifactGateway",
    "OmnigentCaptureBundle",
    "OmnigentContractError",
    "build_omnigent_terminal_refs",
    "build_omnigent_result",
    "capture_artifact_json",
    "_build_capture_bundle",
    "_compact_summary",
]
