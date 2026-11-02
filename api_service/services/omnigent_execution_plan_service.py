"""Compile and persist Omnigent execution authority at the product boundary.

This module is intentionally an API/service boundary.  It may read deployment
registries and persist artifacts/rows; Temporal workflows only receive the
compact binding returned here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_service.db.models import TemporalArtifactRetentionClass
from api_service.services.provider_profile_runtime import OMNIGENT_RUNTIME_ID
from moonmind.omnigent.evidence_resolver import resolve_execution_evidence
from moonmind.omnigent.execution_support_evidence import (
    load_protected_execution_support_evidence,  # re-export for hermetic test patching
)
from moonmind.omnigent.harness_platform.agent_profile import (
    OmnigentAgentProfileV2,
    stable_imported_content_digest,
    stable_upstream_snapshot_digest,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    HarnessRecord,
    HarnessTrustRecord,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    create_binding_set,
    derive_repository_slot_requirements,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    AdmissionAuthority,
    OmnigentExecutionPlanEnvelope,
    OmnigentExecutionPlanPayload,
    create_execution_plan_envelope,
    logical_step_plan_digests,
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.harness_registry import (
    canonical_harness_id,
    find_harness_registration,
    harness_registration,
)
from moonmind.omnigent.harness_platform.host_classes import (
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    is_api_key_launch_profile,
    materializer_ref_for_profile,
    materializer_ref_for_provider,
)
from moonmind.omnigent.harness_platform.planner import (
    compile_execution_plan,
    selected_realizer_consumes_repository_authority,
)
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    SessionExecutionPlanStore,
)
from moonmind.omnigent.host_services.mounted_tools import deployment_mounted_tool_names
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.schemas.agent_skill_models import AgentSkillFormat
from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet as AgentResolvedSkillSet,
)
from moonmind.schemas.agent_skill_models import (
    RuntimeMaterializationMode,
    SkillSelector,
    SkillSelectorEntry,
)
from moonmind.security.execution_fanout_capabilities import (
    EXECUTION_FANOUT_REQUIRED_CAPABILITY,
)
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    SkillResolutionContext,
)
from moonmind.utils.logging import redact_sensitive_text
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactTransactionOwnership,
)
from moonmind.workflows.temporal.remediation_loop import (
    ToolDescriptor,
    tool_descriptor_selected_skill,
)
from pr_resolver_core import IMPLEMENTATION_CONTRACT

logger = logging.getLogger(__name__)

# Hermetic fixture digests for the approved harnesses. Every other product
# authority ref comes from the one harness registration registry
# (MoonLadderStudios/MoonMind#3711); only the placeholder implementation digest
# is fixture data, because a real digest requires a synchronized catalog.
_HARNESS_FIXTURE_IMPLEMENTATION_DIGESTS: dict[str, str] = {
    "codex-native": "sha256:" + "e" * 64,
    "claude-native": "sha256:" + "d" * 64,
    "opencode-native": "sha256:" + "a" * 64,
    "pi-native": "sha256:" + "c" * 64,
}


def _fixture_harness_config(harness_id: str) -> dict[str, str] | None:
    """Return bounded fixture config for a hermetic caller without a catalog.

    Production planning uses the synchronized catalog and resolved deployment
    state as authoritative authority; this path exists only so unit tests can
    compile a plan without catalog storage.
    """

    registration = find_harness_registration(harness_id)
    digest = _HARNESS_FIXTURE_IMPLEMENTATION_DIGESTS.get(harness_id)
    if registration is None or digest is None:
        return None
    return {
        "hostClassRef": registration.hostClassRef,
        "implementationDigest": digest,
        "materializerRef": registration.materializerRef,
        "authModel": registration.authModel,
        "integrationMode": registration.integrationMode,
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def json_artifact_digest(payload: Mapping[str, Any]) -> str:
    """Return the digest ``persist_json_artifact`` records for ``payload``."""

    return _sha256(_json_bytes(payload))


def _digest_ref(prefix: str, value: Any) -> str:
    body = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return f"{prefix}:sha256:{hashlib.sha256(body).hexdigest()}"


def _image_digest(image_ref: object, *, field_name: str) -> str:
    value = str(image_ref or "").strip()
    _prefix, separator, digest = value.rpartition("@sha256:")
    if not separator or len(digest) != 64:
        raise ValueError(f"{field_name} must be digest-pinned")
    return "sha256:" + digest


async def _try_load_real_harness_config(
    *,
    harness_id: str,
    agent_profile_snapshot: Mapping[str, Any],
    session_factory: Any,
    db_session: Any | None = None,
) -> dict[str, Any] | None:
    """Try to load authoritative harness config from synchronized catalog.

    Returns the product configuration plus the exact catalog and harness
    authorities pinned by the Agent Profile. Hermetic callers without catalog
    storage receive ``None`` and use the bounded fixture configuration.
    """
    if db_session is not None:
        # Schedule admission owns an existing transaction. Borrow its session
        # for catalog reads without closing or committing the caller's work.
        @asynccontextmanager
        async def catalog_session():
            yield db_session

        session_factory = catalog_session
    try:
        from moonmind.omnigent.harness_platform.catalog_service import (
            DbHarnessCatalogRepository,
        )

        # Agent profile snapshot carries catalogRef and implementationRef
        doc = (
            agent_profile_snapshot.get("document")
            if isinstance(agent_profile_snapshot.get("document"), Mapping)
            else {}
        )
        # Try to load via snapshot's catalogRef
        catalog_ref = None
        if isinstance(doc, Mapping):
            harness = doc.get("harness")
            if isinstance(harness, Mapping):
                catalog_ref = str(harness.get("catalogRef") or "").strip()
        if not catalog_ref:
            # Fallback to snapshot's digest field
            catalog_ref = str(agent_profile_snapshot.get("catalogRef") or "").strip()
        # Use DbHarnessCatalogRepository to load; need endpointRef
        endpoint_ref = (
            str(doc.get("endpointRef") or "default")
            if isinstance(doc, Mapping)
            else "default"
        )
        repo = DbHarnessCatalogRepository(session_factory)
        # Load the immutable profile authority first. A separate latest
        # observation supplies freshness/liveness evidence; it must never
        # replace the catalog ref pinned by the Agent Profile.
        catalog_result = None
        if catalog_ref:
            catalog_result = await repo.load(catalog_ref)
            if catalog_result is None:
                raise HarnessPlatformError(
                    "selected Agent Profile catalog snapshot is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
                )
        else:
            catalog_result = await repo.latest(endpoint_ref)
        if catalog_result is None:
            raise HarnessPlatformError(
                "harness catalog observation is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
            )
        latest_catalog_result = await repo.latest(endpoint_ref)
        if latest_catalog_result is None:
            raise HarnessPlatformError(
                "fresh harness catalog observation is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
            )
        harness_record = next(
            (h for h in catalog_result.snapshot.harnesses if h.id == harness_id),
            None,
        )
        if harness_record is None:
            raise HarnessPlatformError(
                "selected harness is absent from its catalog snapshot",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
            )
        freshness_trust_record = next(
            (
                record
                for record in latest_catalog_result.trust_records
                if record.implementationRef
                == harness_record.implementation.implementation_ref()
            ),
            None,
        )
        if freshness_trust_record is None:
            raise HarnessPlatformError(
                "fresh harness trust decision is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED,
            )
        implementation_digest = harness_record.implementation.digest
        # Derive materializer/auth/integration from catalog capabilities
        registration = harness_registration(harness_id)
        auth_model = harness_record.capabilities.authModel or registration.authModel
        integration_mode = (
            harness_record.capabilities.integrationMode or registration.integrationMode
        )
        materializer = registration.materializerRef
        host_ref = registration.hostClassRef
        return {
            "hostClassRef": host_ref,
            "implementationDigest": implementation_digest,
            "materializerRef": materializer,
            "authModel": auth_model,
            "integrationMode": integration_mode,
            "_catalogSnapshot": catalog_result.snapshot,
            "_freshnessCatalogSnapshot": latest_catalog_result.snapshot,
            "_freshnessTrustRecord": freshness_trust_record,
            "_harnessRecord": harness_record,
        }
    except HarnessPlatformError:
        raise
    except Exception as exc:
        if callable(session_factory) or session_factory is None:
            raise HarnessPlatformError(
                "harness catalog authority could not be loaded",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
            ) from exc
        return None


async def _resolve_runtime_policy_snapshot(
    *,
    policy_ref: str,
    session_factory: Any,
    db_session: Any | None,
) -> dict[str, Any]:
    """Read one active, validated policy before plan persistence."""

    from api_service.services.omnigent_policies import (
        OmnigentPolicyService,
        PolicyConflict,
        PolicyNotFound,
    )

    try:
        if db_session is not None:
            return await OmnigentPolicyService(db_session).resolve_runtime_snapshot(
                policy_ref
            )
        if not callable(session_factory):
            raise ValueError(
                "Omnigent execution-plan compilation requires policy storage"
            )
        async with session_factory() as session:
            return await OmnigentPolicyService(session).resolve_runtime_snapshot(
                policy_ref
            )
    except (PolicyConflict, PolicyNotFound) as exc:
        raise HarnessPlatformError(
            "Omnigent launch policy authority is not ready; wait for startup "
            "reconciliation and retry",
            code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
        ) from exc


async def persist_json_artifact(
    *,
    artifact_service: Any,
    principal: str,
    artifact_class: str,
    payload: Mapping[str, Any],
) -> tuple[str, str]:
    """Persist canonical JSON and return its opaque ref and digest."""

    body = _json_bytes(payload)
    digest = _sha256(body)
    artifact, _upload = await artifact_service.create(
        principal=principal,
        content_type="application/json",
        size_bytes=len(body),
        sha256=digest.removeprefix("sha256:"),
        retention_class=TemporalArtifactRetentionClass.LONG,
        metadata_json={
            "artifact_class": artifact_class,
            "schema_name": artifact_class,
            "created_by": principal,
        },
    )
    completed = await artifact_service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=body,
        content_type="application/json",
    )
    return str(completed.artifact_id), digest


async def _persist_binary_artifact(
    *,
    artifact_service: Any,
    principal: str,
    artifact_class: str,
    payload: bytes,
    content_type: str,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    digest = _sha256(payload)
    artifact, _upload = await artifact_service.create(
        principal=principal,
        content_type=content_type,
        size_bytes=len(payload),
        sha256=digest.removeprefix("sha256:"),
        retention_class=TemporalArtifactRetentionClass.LONG,
        metadata_json={
            "artifact_class": artifact_class,
            "schema_name": artifact_class,
            "created_by": principal,
            **dict(metadata or {}),
        },
    )
    completed = await artifact_service.write_complete(
        artifact_id=artifact.artifact_id,
        principal=principal,
        payload=payload,
        content_type=content_type,
    )
    return str(completed.artifact_id), digest


@dataclass(frozen=True)
class PersistedOmnigentExecutionPlan:
    # In-process admission proof; never part of a serialized plan binding.
    artifact_access: ArtifactAccessContext
    envelope: OmnigentExecutionPlanEnvelope
    binding: OmnigentExecutionPlanBinding
    artifact_refs: tuple[str, ...]
    resolved_skillset_ref: str
    resolved_skillset_digest: str
    # Frozen runtime-provider rollout decision the plan was admitted under
    # (MoonLadderStudios/MoonMind#3833). ``None`` only for a plan compiled
    # before the contract existed.
    runtime_provider_rollout: dict[str, Any] | None = None
    step_plans: tuple[PersistedOmnigentExecutionPlan, ...] = ()
    step_parameters: Mapping[str, Mapping[str, Any]] | None = None

    @property
    def execution_parameters(self) -> dict[str, Any]:
        parameters = {
            "omnigentExecutionPlan": self.binding.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "resolvedSkillsetRef": self.resolved_skillset_ref,
            "omnigentStepExecutionPlans": deepcopy(dict(self.step_parameters or {})),
        }
        return parameters


def _workflow_payload(initial_parameters: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read workflow intent, including the persisted schedule task envelope.

    Match execution's precedence without rewriting immutable authored inputs.
    Existing schedule definitions retain their task envelope across refreshes.
    """

    workflow = initial_parameters.get("workflow")
    if isinstance(workflow, Mapping) and workflow:
        return workflow
    task = initial_parameters.get("task")
    return task if isinstance(task, Mapping) else {}


def selected_skill_names(initial_parameters: Mapping[str, Any]) -> list[str]:
    """Collect the workflow's MoonMind Skill intent for one run snapshot."""

    workflow_mapping = _workflow_payload(initial_parameters)
    names: list[str] = []

    def add(raw: Any) -> None:
        if isinstance(raw, Mapping):
            raw = raw.get("name") or raw.get("id")
        candidate = str(raw or "").strip().lower()
        if candidate and candidate != "auto" and candidate not in names:
            names.append(candidate)

    def add_remediation_loop_skills(step: Mapping[str, Any]) -> None:
        """Collect Skills from the typed dynamic remediation contract."""

        annotations = step.get("annotations")
        if not isinstance(annotations, Mapping):
            return
        loop = annotations.get("remediationLoop")
        if not isinstance(loop, Mapping) or loop.get("kind") != "remediation_loop":
            return
        for field_name in ("remediationTool", "verificationTool"):
            descriptor = ToolDescriptor.model_validate(loop.get(field_name))
            add(tool_descriptor_selected_skill(descriptor))

    selectors = workflow_mapping.get("skills")
    if isinstance(selectors, Mapping):
        for entry in selectors.get("include") or []:
            add(entry)
    add(workflow_mapping.get("skill"))
    tool = workflow_mapping.get("tool")
    if isinstance(tool, Mapping) and str(tool.get("type") or "").lower() == "skill":
        add(tool)
    for step in workflow_mapping.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        step_selectors = step.get("skills")
        if isinstance(step_selectors, Mapping):
            for entry in step_selectors.get("include") or []:
                add(entry)
        add(step.get("skill"))
        step_tool = step.get("tool")
        if (
            isinstance(step_tool, Mapping)
            and str(step_tool.get("type") or "").lower() == "skill"
        ):
            add(step_tool)
        add_remediation_loop_skills(step)
    return names


def _skill_selector(initial_parameters: Mapping[str, Any]) -> SkillSelector:
    workflow_mapping = _workflow_payload(initial_parameters)
    selectors = workflow_mapping.get("skills")
    selector_mapping = dict(selectors) if isinstance(selectors, Mapping) else {}
    excluded = sorted(
        {
            str(value or "").strip().lower()
            for value in selector_mapping.get("exclude") or []
            if str(value or "").strip()
        }
    )
    selected = selected_skill_names(initial_parameters)
    conflict = sorted(set(selected).intersection(excluded))
    if conflict:
        raise ValueError(
            "selected Skills cannot also be excluded: " + ", ".join(conflict)
        )
    raw_mode = str(selector_mapping.get("materializationMode") or "").strip()
    materialization_mode = {
        "hybrid": RuntimeMaterializationMode.HYBRID,
        "local": RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        "remote": RuntimeMaterializationMode.PROMPT_BUNDLED,
        "none": RuntimeMaterializationMode.RETRIEVAL,
    }.get(raw_mode)
    return SkillSelector(
        sets=[str(value) for value in selector_mapping.get("sets") or []],
        include=[SkillSelectorEntry(name=name) for name in selected],
        exclude=excluded,
        materialization_mode=materialization_mode,
    )


async def _resolve_and_persist_skills(
    *,
    session_factory: Any,
    artifact_service: Any,
    principal: str,
    workflow_id: str,
    task_input_snapshot_digest: str,
    initial_parameters: Mapping[str, Any],
    db_session: Any | None = None,
    access: ArtifactAccessContext,
    recorded_skillset_ref: str | None = None,
) -> tuple[AgentResolvedSkillSet, str, str, tuple[str, ...]]:
    """Resolve once at admission and persist exact content plus its manifest."""

    if recorded_skillset_ref:
        # Dispatch-time account selection happens after UserWorkflow resolved
        # its Skill. Read that execution-granted immutable snapshot rather
        # than consulting a catalog that may have changed during the wait.
        from moonmind.workflows.temporal.artifacts import ArtifactDependency

        manifest_id = recorded_skillset_ref.removeprefix("artifact://").removeprefix("artifact:")
        artifact, body = await artifact_service.read(artifact_id=manifest_id, access=access)
        frozen = json.loads(body)
        resolved = AgentResolvedSkillSet.model_validate(frozen)
        dependencies = [ArtifactDependency(artifact_id=manifest_id, sha256=artifact.sha256,
                                           required=True, role="input.original_skill_snapshot")]
        for skill in resolved.skills:
            if not skill.content_ref or not str(skill.content_digest or "").startswith("sha256:"):
                raise ValueError("recorded Skill snapshot lacks immutable content evidence")
            content_id = str(skill.content_ref).removeprefix("artifact://").removeprefix("artifact:")
            # A readable manifest is not authority over arbitrary referenced
            # content. Keep the original execution's grants before sealing a
            # new dependency closure.
            _, content = await artifact_service.read(artifact_id=content_id, access=access)
            if "sha256:" + hashlib.sha256(content).hexdigest() != skill.content_digest:
                raise ValueError("recorded Skill content does not match its admitted digest")
            dependencies.append(ArtifactDependency(
                artifact_id=content_id,
                sha256=skill.content_digest.removeprefix("sha256:"), required=True, role="input.skill_content",
            ))
        # Native manifests predate canonical JSON storage. Preserve their
        # exact document in the shared serialization and retain the original
        # artifact in the verified dependency closure.
        shared_ref, shared_digest = await persist_json_artifact(
            artifact_service=artifact_service, principal=principal,
            artifact_class="omnigent.resolved_skill_set", payload=frozen,
        )
        await artifact_service.record_verified_dependencies(
            root_artifact_id=shared_ref, dependencies=tuple(dependencies),
            access=access._with_verified_inputs((shared_ref, *(entry.artifact_id for entry in dependencies))),
        )
        return (resolved.model_copy(update={"manifest_ref": shared_ref}), shared_ref,
                shared_digest, tuple(str(skill.content_ref) for skill in resolved.skills))

    snapshot_seed = hashlib.sha256(
        f"{workflow_id}:{task_input_snapshot_digest}".encode()
    ).hexdigest()[:32]
    selector = _skill_selector(initial_parameters)
    resolved = await AgentSkillResolver().resolve(
        selector,
        SkillResolutionContext(
            snapshot_id=f"skillset_{snapshot_seed}",
            deployment_id=workflow_id,
            # Product admission has no checked-out target repository yet. Repo
            # and local sources therefore cannot be trusted/resolved here; an
            # explicitly selected Skill must come from built-in or deployment
            # authority and otherwise fails before scheduling.
            workspace_root=None,
            allow_repo_skills=False,
            allow_local_skills=False,
            async_session_maker=(
                session_factory if callable(session_factory) else None
            ),
            db_session=db_session,
        ),
    )
    from moonmind.workflows.agent_skills.agent_skills_activities import (
        AgentSkillsActivities,
    )

    content_refs: list[str] = []
    created_refs: list[str] = []
    catalog_refs: list[str] = []
    persisted_entries = []
    for skill in resolved.skills:
        if skill.content_ref:
            if not str(skill.content_digest or "").startswith("sha256:"):
                raise ValueError(
                    f"selected Skill '{skill.skill_name}' lacks content digest evidence"
                )
            content_refs.append(skill.content_ref)
            # These references came from the deployment's Skill catalog, not
            # request-supplied artifact pointers. This admission owner may
            # delegate precisely the selected catalog content to a workflow;
            # the dependency writer still verifies its digest and policy.
            if skill.provenance.source_kind.value == "deployment":
                catalog_refs.append(str(skill.content_ref).removeprefix("artifact://").removeprefix("artifact:"))
            persisted_entries.append(skill)
            continue
        source_path = str(skill.provenance.source_path or "").strip()
        if not source_path:
            raise ValueError(
                f"selected Skill '{skill.skill_name}' lacks immutable content evidence"
            )
        bundle = AgentSkillsActivities._build_skill_bundle_payload(
            Path(source_path),
            include_pr_resolver_core=(
                skill.skill_name == "pr-resolver"
                and skill.provenance.source_kind.value == "built_in"
                and skill.implementation is not None
                and skill.implementation.contract == IMPLEMENTATION_CONTRACT
            ),
        )
        content_ref, content_digest = await _persist_binary_artifact(
            artifact_service=artifact_service,
            principal=principal,
            artifact_class="omnigent.agent_skill_bundle",
            payload=bundle,
            content_type="application/gzip",
            metadata={"skill_name": skill.skill_name},
        )
        content_refs.append(content_ref)
        created_refs.append(content_ref)
        persisted_entries.append(
            skill.model_copy(
                update={
                    "content_ref": content_ref,
                    "content_digest": content_digest,
                    "format": AgentSkillFormat.BUNDLE,
                }
            )
        )
    resolved = resolved.model_copy(update={"skills": persisted_entries})
    manifest_payload = resolved.model_dump(mode="json", exclude_none=True)
    manifest_ref, manifest_digest = await persist_json_artifact(
        artifact_service=artifact_service,
        principal=principal,
        artifact_class="omnigent.resolved_skill_set",
        payload=manifest_payload,
    )
    from moonmind.workflows.temporal.artifacts import ArtifactDependency

    await artifact_service.record_verified_dependencies(
        root_artifact_id=manifest_ref,
        dependencies=tuple(
            ArtifactDependency(
                artifact_id=str(skill.content_ref)
                .removeprefix("artifact://")
                .removeprefix("artifact:"),
                sha256=skill.content_digest.removeprefix("sha256:"),
                required=True,
                role="input.skill_content",
            )
            for skill in persisted_entries
        ),
        access=access._with_verified_inputs((manifest_ref, *created_refs, *catalog_refs)),
    )
    resolved = resolved.model_copy(update={"manifest_ref": manifest_ref})
    return resolved, manifest_ref, manifest_digest, tuple(content_refs)


def _build_v2_profile(
    *,
    snapshot: Mapping[str, Any],
    catalog_ref: str,
    implementation_ref: str,
    harness_id: str,
    auth_model: str,
    additional_tools: tuple[str, ...] = (),
) -> OmnigentAgentProfileV2:
    document = snapshot.get("document")
    if not isinstance(document, Mapping):
        raise ValueError("Agent Profile snapshot document is unavailable")
    source = document.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("Agent Profile source identity is unavailable")
    snapshot_digest = str(snapshot.get("digest") or "").strip()
    if not snapshot_digest.startswith("sha256:"):
        raise ValueError("Agent Profile snapshot digest is invalid")
    # ``stable_upstream_snapshot_digest`` / ``stable_imported_content_digest``
    # own this derivation; launch-time plan verification recomputes the same
    # identity from the same document, so writer and reader cannot diverge.
    if source.get("upstreamId"):
        stable_upstream_digest = stable_upstream_snapshot_digest(
            source, snapshot_digest
        )
        agent_source: dict[str, Any] = {
            "kind": "upstream",
            "upstreamId": str(source["upstreamId"]),
            "upstreamVersion": str(source.get("upstreamVersion") or "0.0.0"),
            "upstreamSnapshotDigest": stable_upstream_digest,
        }
    else:
        bundle_ref = str(source.get("bundleArtifactRef") or "").strip()
        bundle_digest = str(source.get("bundleDigest") or "").strip()
        upstream = snapshot.get("upstreamSnapshot")
        import_receipt = (
            str(upstream.get("importReceiptRef") or "").strip()
            if isinstance(upstream, Mapping)
            else ""
        )
        if not bundle_ref or not bundle_digest or not import_receipt:
            raise ValueError("bundle Agent Profile lacks immutable import authority")
        stable_content_digest = stable_imported_content_digest(source, snapshot_digest)
        stable_agent_id = str(
            source.get("importedAgentId") or snapshot.get("agentId") or ""
        ).strip()
        stable_agent_version = str(
            source.get("importedAgentVersion") or snapshot.get("version") or ""
        ).strip()
        agent_source = {
            "kind": "bundle",
            "bundleArtifactRef": bundle_ref,
            "bundleDigest": bundle_digest,
            "importReceiptRef": import_receipt,
            "importedAgentId": stable_agent_id,
            "importedAgentVersion": stable_agent_version,
            "importedContentDigest": stable_content_digest,
        }
    required = list(document.get("requiredCapabilities") or [])
    workspace = document.get("workspace")
    if isinstance(workspace, Mapping):
        required.extend(workspace.get("requiredCapabilities") or [])
    return OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": str(document.get("endpointRef") or "default"),
            "source": agent_source,
            "harness": {
                "id": harness_id,
                "catalogRef": catalog_ref,
                "implementationRef": implementation_ref,
            },
            "requirements": {
                "harness": {"required": sorted(set(map(str, required)))},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": [auth_model],
                    "acceptedProviderIds": list(
                        (document.get("providerRequirements") or {}).get("providerIds")
                        or []
                    ),
                }
            ],
            "model": dict(document.get("model") or {}),
            "workspace": dict(document.get("workspace") or {}),
            "skills": list(document.get("skills") or []),
            "tools": sorted(
                {
                    *(
                        str(value).strip().lower()
                        for value in document.get("tools") or []
                        if str(value).strip()
                    ),
                    *additional_tools,
                }
            ),
            "capture": dict(document.get("capture") or {}),
            "continuations": dict(document.get("continuations") or {}),
            "publish": dict(document.get("publish") or {}),
            "allowedLaunchPolicyRefs": list(
                snapshot.get("allowedLaunchPolicyRefs") or []
            ),
        }
    )


def _enabled_merge_automation(
    initial_parameters: Mapping[str, Any]
) -> Mapping[str, Any]:
    from moonmind.workflows.executions.routing import (
        _coerce_bool,
        merge_automation_candidates,
    )

    workflow = _workflow_payload(initial_parameters)
    top_publish = initial_parameters.get("publish")
    publication = workflow.get("publish") or {}
    parent_publish = (
        top_publish if isinstance(top_publish, Mapping) and top_publish else publication
    )
    return next(
        (
            candidate
            for candidate in merge_automation_candidates(
                initial_parameters,
                publish_payload=parent_publish,
                task_payload=workflow,
            )
            if isinstance(candidate, Mapping)
            and _coerce_bool(candidate.get("enabled"), default=False)
        ),
        {},
    )


def _native_review_graph(
    initial_parameters: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Recognize only the closed built-in PR-review graph, never generic tools.

    This is compiler-owned consumer classification, not an authored capability.
    Unknown execution fields, dynamic expansion, and additional steps stay on
    the ordinary agent admission path.
    """
    from moonmind.schemas.temporal_models import MergeAutomationConfigModel

    workflow = _workflow_payload(initial_parameters)
    supported_fields = {
        "steps",
        "publish",
        "git",
        "workspace",
        "runtime",
        "title",
        "composition",
        "appliedTemplate",
        "authoredPresets",
        "capabilities",
        "warnings",
        # Expansion stops at the frozen steps when they are present; these
        # fields retain authoring provenance rather than executable children.
        "taskTemplate",
        "task_template",
        "appliedStepTemplates",
        "inputs",
        "mergeAutomation",
        "merge_automation",
    }
    if any(value for key, value in workflow.items() if key not in supported_fields):
        return None
    if initial_parameters.get("instructions") or initial_parameters.get(
        "inputArtifactRef"
    ):
        return None
    steps = workflow.get("steps")
    if not isinstance(steps, list) or len(steps) != 1:
        return None
    step = steps[0]
    if not isinstance(step, Mapping) or step.get("type") != "tool":
        return None
    supported_step_fields = {
        "id",
        "type",
        "title",
        "instructions",
        "tool",
        "repositoryOperation",
        "presetProvenance",
        "source",
    }
    if any(value for key, value in step.items() if key not in supported_step_fields):
        return None
    if step.get("repositoryOperation") not in (None, "read"):
        return None
    tool = step.get("tool")
    if (
        not isinstance(tool, Mapping)
        or tool.get("id") != "github.resolve_pull_request_target"
        or set(tool) - {"id", "inputs", "requiredCapabilities"}
    ):
        return None
    from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
    from moonmind.workflows.temporal.story_output_tools import (
        register_story_output_tool_handlers,
    )

    dispatcher = ToolActivityDispatcher()
    register_story_output_tool_handlers(dispatcher)
    if dispatcher.registered_skill_handler(skill_name=tool["id"]) is None:
        return None
    inputs = tool.get("inputs")
    if (
        not isinstance(inputs, Mapping)
        or set(inputs) != {"repository", "pullRequest"}
        or not isinstance(inputs.get("repository"), str)
        or not isinstance(inputs.get("pullRequest"), (str, int))
        or isinstance(inputs.get("pullRequest"), bool)
        or not str(inputs.get("pullRequest") or "").strip()
        or any(
            marker in str(value) for value in inputs.values() for marker in ("{{", "${")
        )
    ):
        return None
    config = _enabled_merge_automation(initial_parameters)
    finish_mode = config.get("finishMode") or config.get("finish_mode")
    if not isinstance(finish_mode, str) or finish_mode.strip() != "review_only":
        return None
    publication = workflow.get("publish") or {}
    publish_mode = (
        initial_parameters.get("publishMode") or publication.get("mode") or "none"
    )
    if str(publish_mode).strip().lower() != "none":
        return None
    admitted_config = MergeAutomationConfigModel.model_validate(
        {**config, "finishMode": "review_only"}
    )
    return {
        "steps": steps,
        "inputs": workflow.get("inputs") or {},
        "repository": initial_parameters.get("repository"),
        "workspace": (
            initial_parameters.get("workspaceSpec")
            or initial_parameters.get("workspace")
            or workflow.get("workspace")
            or {}
        ),
        "mergeAutomation": admitted_config.model_dump(mode="json", by_alias=True),
    }


async def _verify_native_review_graph(
    *,
    initial_parameters: Mapping[str, Any],
    artifact_service: Any,
    principal: str,
    task_input_snapshot_ref: str,
    task_input_snapshot_digest: str,
    access: ArtifactAccessContext,
) -> bool:
    """Require the compiler's closed graph to match its immutable input bytes."""
    graph = _native_review_graph(initial_parameters)
    if graph is None:
        return False
    _artifact, body = await artifact_service.read(
        artifact_id=task_input_snapshot_ref.removeprefix("artifact://").removeprefix(
            "artifact:"
        ),
        access=access,
    )
    if _sha256(body) != task_input_snapshot_digest:
        raise ValueError("native review graph task-input snapshot digest mismatch")
    frozen = json.loads(body)
    if not isinstance(frozen, Mapping):
        raise ValueError("native review graph requires a frozen task-input snapshot")
    frozen_parameters = frozen.get("draft")
    if not isinstance(frozen_parameters, Mapping):
        target = frozen.get("target")
        frozen_parameters = (
            target.get("initialParameters") if isinstance(target, Mapping) else None
        )
    if (
        not isinstance(frozen_parameters, Mapping)
        or _native_review_graph(frozen_parameters) != graph
    ):
        raise ValueError(
            "native review graph conflicts with frozen task-input snapshot"
        )
    return True


def _authored_plan_github_operations(
    initial_parameters: Mapping[str, Any],
    *,
    publish_mode: Any,
    requires_github: bool,
    resolved_skills: AgentResolvedSkillSet | None,
) -> tuple[str, ...]:
    """Freeze composed actions through the same intent reader used at execution."""
    from moonmind.omnigent.workspace_intent import authored_github_operations
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest

    workflow = _workflow_payload(initial_parameters)
    invocations: list[dict[str, Any]] = []
    for block in (initial_parameters, workflow, *(workflow.get("steps") or [])):
        if not isinstance(block, Mapping):
            continue
        tool = block.get("tool")
        candidates = [block.get("skill")]
        if isinstance(tool, Mapping) and tool.get("type") == "skill":
            candidates.append(tool)
        for raw in candidates:
            if isinstance(raw, str):
                raw = {"name": raw}
            if isinstance(raw, Mapping):
                invocations.append(dict(raw))
    resolved = {
        skill.skill_name: skill
        for skill in (resolved_skills.skills if resolved_skills else ())
    }
    operations: tuple[str, ...] = ()
    for invocation in invocations or [{}]:
        name = str(invocation.get("name") or invocation.get("id") or "").strip().lower()
        selected = resolved.get(name)
        frozen_skill = (
            {
                "name": name,
                "publish": selected.publish,
                "sideEffect": selected.side_effect,
            }
            if selected is not None
            else {}
        )
        parameters = {
            **workflow,
            **initial_parameters,
            "publishMode": publish_mode,
            "requiredCapabilities": list(
                dict.fromkeys(
                    (
                        *(workflow.get("requiredCapabilities") or ()),
                        *(initial_parameters.get("requiredCapabilities") or ()),
                        *(selected.required_capabilities if selected else ()),
                        *(("gh",) if requires_github else ()),
                    )
                )
            ),
            "skill": invocation,
        }
        request = AgentExecutionRequest.model_construct(
            parameters=parameters,
            skill=frozen_skill,
            workspace_spec=(initial_parameters.get("workspaceSpec") or {}),
        )
        operations = tuple(
            dict.fromkeys((*operations, *authored_github_operations(request)))
        )
    return operations


async def _admit_repository_plan_inputs(
    *,
    session_factory: Any,
    db_session: Any,
    artifact_service: Any,
    principal: str,
    workflow_id: str,
    initial_parameters: Mapping[str, Any],
    requires_github: bool,
    parent_plan: OmnigentExecutionPlanEnvelope | None,
    parent_artifact_access: ArtifactAccessContext | None = None,
    typed_authority: bool = True,
    native_review: bool = False,
    resolved_skills: AgentResolvedSkillSet | None = None,
    access: ArtifactAccessContext,
) -> dict[str, Any]:
    """Derive transport/tool roles and freeze selection through existing owners.

    Agent authority requires a repository-capable realizer. A compiler-proven
    frozen native review graph instead admits its collaboration-only consumer;
    ordinary model-only agent selections retain their legacy restrictions.
    """
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.auth.bound_acquisition import AccessMode, select_repository_authority
    from moonmind.omnigent.harness_platform.credential_bindings import (
        attenuate_repository_binding_for_child,
        repository_bindings_of,
    )
    from moonmind.omnigent.repository_sources import normalize_repository_source
    from moonmind.omnigent.workspace_intent import authored_repository_source
    from moonmind.omnigent.workspace_sources import compile_workspace_source
    from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
        RepositoryIdentity,
        admit_scoped_route,
        authored_repository_access,
        github_repository_name_from_value,
    )

    workflow = (
        initial_parameters.get("workflow") or initial_parameters.get("task") or {}
    )
    workspace = dict(
        initial_parameters.get("workspaceSpec")
        or initial_parameters.get("workspace")
        or workflow.get("workspace")
        or {}
    )
    # Check aliases before adapting a canonical target into the runtime shape.
    authored_repository_source(
        AgentExecutionRequest.model_construct(
            workspace_spec=workspace, parameters=dict(initial_parameters)
        )
    )
    target = (
        initial_parameters.get("repository")
        or workspace.get("repositoryTarget")
        or workspace.get("repository")
        or workflow.get("repository")
    )
    if isinstance(target, Mapping) and target.get("provider"):
        workspace["repositoryTarget"] = dict(target)
        workspace.pop("repository", None)
    elif target:
        workspace["repository"] = target
    git = workflow.get("git") or {}
    branch = (
        initial_parameters.get("repositoryBranch")
        or git.get("startingBranch")
        or workflow.get("startingBranch")
    )
    if branch and not workspace.get("branch"):
        workspace["branch"] = branch
    source = compile_workspace_source(
        workspace,
        workflow_id=workflow_id,
        step_execution_id=workflow_id,
        runtime="omnigent",
    )
    result: dict[str, Any] = {
        "bindings": {},
        "declarations": {},
        "access": {},
        "sourceKind": source.kind,
    }
    if not source.repository_ref:
        if native_review:
            raise ValueError(
                "native review graph requires a selected GitHub repository"
            )
        return result
    _remote, kind = normalize_repository_source(source.repository_ref)
    if kind != "github_https":
        if native_review:
            raise ValueError(
                "native review graph requires a selected GitHub repository"
            )
        # Local/content sources never acquire repository transport credentials.
        return result
    repository = github_repository_name_from_value(source.repository_ref)
    connection_ref, anonymous = authored_repository_access(initial_parameters)
    mode = (
        AccessMode.ANONYMOUS
        if anonymous
        else (AccessMode.EXPLICIT if connection_ref else AccessMode.ROUTED)
    )
    publication = workflow.get("publish") or {}
    # Retain false/zero/container inputs for canonical type validation; a
    # truthiness fallback here would silently turn malformed writes into None.
    authored_publish_mode = initial_parameters.get("publishMode")
    if authored_publish_mode is None or authored_publish_mode == "":
        authored_publish_mode = publication.get("mode")
    publish_mode = str(authored_publish_mode or "none").strip().lower()
    merge_automation = _enabled_merge_automation(initial_parameters)
    finish_mode = merge_automation.get("finishMode") or merge_automation.get(
        "finish_mode"
    )
    review_only = isinstance(finish_mode, str) and finish_mode.strip() == "review_only"
    if review_only:
        from moonmind.schemas.temporal_models import MergeAutomationConfigModel

        MergeAutomationConfigModel.model_validate(
            {**merge_automation, "finishMode": "review_only"}
        )
        publish_mode = publish_mode.strip()
        if publish_mode != "none":
            raise ValueError("review_only requires publication mode none")
        if mode == AccessMode.ANONYMOUS:
            raise ValueError("anonymous source cannot admit review-request authority")
    if native_review:
        # No checkout or source/destination credential enters this closed graph.
        native_repository = workflow["steps"][0]["tool"]["inputs"]["repository"]
        if not review_only or native_repository.strip().lower() != repository.lower():
            raise ValueError(
                "native review graph target conflicts with admitted repository"
            )
    # A publishing merge-automation resolver child re-admits Auto collaboration
    # authority against this plan, so a coordinator whose own publication is
    # None must still hold that push authority (fix_only pushes too).
    resolver_publishes = bool(merge_automation) and not review_only
    operations = (
        ("read", "review_request")
        if review_only
        else _authored_plan_github_operations(
            initial_parameters,
            publish_mode="pr" if resolver_publishes else authored_publish_mode,
            requires_github=requires_github or resolver_publishes,
            resolved_skills=resolved_skills,
        )
    )
    # The resolver child narrows from this plan, so a merging gate must hold
    # the merge action it will re-admit; fix_only never merges.
    if resolver_publishes and (
        not isinstance(finish_mode, str) or finish_mode.strip() in {"", "merge"}
    ):
        operations = tuple(dict.fromkeys((*operations, "merge_request")))
    slots = {} if native_review else {"source": ("source_read", ("read",))}
    # Native review gates require collaboration even without an agent gh tool.
    if (
        requires_github
        or review_only
        or resolver_publishes
        or any(operation != "read" for operation in operations)
    ) and mode != AccessMode.ANONYMOUS:
        slots["collaboration"] = ("collaboration", operations)
    if publish_mode in {"branch", "pr"}:
        slots["destination"] = (
            "destination_write",
            (
                ("write", "branch_write", "review_request")
                if publish_mode == "pr"
                else ("write", "branch_write")
            ),
        )
    if mode == AccessMode.ANONYMOUS and publish_mode != "none":
        raise ValueError("anonymous source cannot admit publication authority")
    parent_bindings = (
        repository_bindings_of(parent_plan.payload.credentialBindings)
        if parent_plan
        else {}
    )

    @asynccontextmanager
    async def admission_session():
        if db_session is not None:
            yield db_session
        elif mode == AccessMode.ANONYMOUS:
            yield None
        else:
            async with session_factory() as session:
                yield session

    if any(binding.consumer == "native" for binding in parent_bindings.values()):
        raise ValueError("native repository authority cannot be donated to a child")
    if not typed_authority and not native_review:
        # The profile-bound realizer reads only the legacy GitHub credential,
        # which migration 391 records as the default connection. Any other
        # selected or routed connection would be silently substituted.
        unhonored = None
        if mode == AccessMode.EXPLICIT and connection_ref != DEFAULT_GIT_CONNECTION_REF:
            unhonored = connection_ref
        elif mode == AccessMode.ROUTED:
            async with admission_session() as session:
                candidates = await RepositoryConnectionService(
                    session
                ).launch_candidates(
                    repository,
                    principal_ref=principal,
                    principal_scope=("system", None),
                )
            unhonored = next(
                (
                    candidate.connection.id
                    for candidate in candidates
                    if candidate.connection.id != DEFAULT_GIT_CONNECTION_REF
                ),
                None,
            )
        if unhonored:
            raise ValueError(
                f"repository connection {unhonored} cannot be used by the "
                "profile-bound Codex realizer, which reads only "
                f"{DEFAULT_GIT_CONNECTION_REF}; qualify generic Omnigent Codex "
                "or select the default connection"
            )
        return result

    async with admission_session() as session:
        connections = (
            RepositoryConnectionService(session) if session is not None else None
        )
        selected_connection = None
        assignment = None
        candidates = []
        if mode != AccessMode.ANONYMOUS:
            if parent_bindings:
                inherited_slot = "collaboration" if native_review else "source"
                inherited = parent_bindings.get(inherited_slot)
                if inherited is None:
                    raise ValueError(
                        f"parent has no admitted {inherited_slot} authority"
                    )
                if connection_ref and connection_ref != inherited.connectionRef:
                    raise ValueError(
                        "child cannot change the admitted repository connection"
                    )
                connection_ref = inherited.connectionRef
                mode = AccessMode.EXPLICIT
            if connection_ref:
                selected_connection = await connections.get_connection(
                    connection_ref,
                    principal_ref=principal,
                    principal_scope=("system", None),
                )
                if selected_connection is None:
                    raise ValueError("selected repository connection is unavailable")
                assignment = await connections.launch_assignment(
                    selected_connection, repository
                )
                identity = assignment.identity
            else:
                candidates = await connections.launch_candidates(
                    repository,
                    principal_ref=principal,
                    principal_scope=("system", None),
                )
                identities = {
                    candidate.assignment.identity.route_id() for candidate in candidates
                }
                if len(identities) != 1:
                    raise ValueError(
                        "repository route is missing or ambiguous; select a connection"
                    )
                identity = candidates[0].assignment.identity
        else:
            identity = RepositoryIdentity(
                endpoint="https://github.com",
                canonicalRemote=f"https://github.com/{repository}.git",
                displayName=repository,
            )
        for slot, (role, operations) in slots.items():
            if mode == AccessMode.ROUTED:
                selected_route = admit_scoped_route(
                    identity=identity,
                    requested_operations=operations,
                    candidates=candidates,
                    principal_ref=principal,
                    principal_scope=("system", None),
                )
                assignment = selected_route.assignment
                policy_revision = selected_route.connection.policy_revision
            else:
                policy_revision = (
                    selected_connection.policy_revision if selected_connection else 1
                )
            snapshot = select_repository_authority(
                access_mode=mode,
                principal_ref=principal,
                principal_scope=("system", None),
                identity=identity,
                role=role,
                requested_operations=operations,
                policy_revision=policy_revision,
                explicit_connection=selected_connection,
                explicit_assignment=assignment if mode == AccessMode.EXPLICIT else None,
                candidates=candidates,
            )
            if parent_plan is not None:
                parent_access = parent_plan.payload.resolvedTools.get(
                    "repositoryAccess", {}
                ).get(slot)
                if not parent_access:
                    raise ValueError(
                        f"child cannot synthesize an unbound repository slot {slot}"
                    )
                _artifact, parent_bytes = await artifact_service.read(
                    artifact_id=parent_access["artifactRef"].removeprefix("artifact:"),
                    access=parent_artifact_access or access,
                )
                if (
                    "repository-access-snapshot:" + _sha256(parent_bytes)
                    != parent_access["snapshotRef"]
                ):
                    raise ValueError("parent repository snapshot digest mismatch")
                parent_selection = json.loads(parent_bytes)["selection"]
                if any(
                    snapshot.model_dump(by_alias=True, mode="json")[key]
                    != parent_selection[key]
                    for key in ("endpoint", "routeId", "connectionId", "role")
                ) or not set(operations).issubset(parent_selection["operations"]):
                    raise ValueError(
                        "child repository authority must preserve or narrow the parent"
                    )
            artifact_id, digest = await persist_json_artifact(
                artifact_service=artifact_service,
                principal=principal,
                artifact_class="omnigent.repository_access_snapshot",
                payload={
                    "selection": snapshot.model_dump(by_alias=True, mode="json"),
                    "workflowId": workflow_id,
                    "repositoryIdentity": identity.model_dump(
                        by_alias=True, mode="json"
                    ),
                    "assignmentRevision": assignment.revision if assignment else None,
                },
            )
            snapshot_ref = "repository-access-snapshot:" + digest
            result["access"][slot] = {
                "artifactRef": f"artifact:{artifact_id}",
                "snapshotRef": snapshot_ref,
            }
            if mode == AccessMode.ANONYMOUS:
                result["sourceKind"] = "anonymous"
                result["anonymousSnapshotRef"] = snapshot_ref
                continue
            binding = {
                "authorityKind": "repository_connection",
                "connectionRef": snapshot.connection_id,
                "repositoryAccessSnapshotRef": snapshot_ref,
                "materializerRef": "repository-broker@1",
                "repositoryRole": role,
                **({"consumer": "native"} if native_review else {}),
            }
            if parent_plan is not None:
                if parent_bindings[slot].consumer != (
                    "native" if native_review else "agent"
                ):
                    raise ValueError(
                        "child cannot change repository authority consumer"
                    )
                binding = attenuate_repository_binding_for_child(
                    parent_binding=parent_bindings[slot],
                    child_target_ref=workflow_id,
                    child_attempt_ref=workflow_id,
                    child_snapshot_ref=snapshot_ref,
                ).binding.model_dump(by_alias=True, mode="json")
            result["bindings"][slot] = binding
            result["declarations"][slot] = {
                "allowedRoles": (role,),
                **({"allowedConsumers": ("native",)} if native_review else {}),
                "allowedMaterializers": ("repository-broker@1",),
                "expectedConnectionRef": snapshot.connection_id,
                "allowedSnapshotRefs": (snapshot_ref,),
            }
    return result


async def compile_and_persist_execution_plan(
    *,
    session_factory: Any,
    artifact_service: Any,
    principal: str,
    workflow_id: str,
    agent_profile_snapshot: Mapping[str, Any],
    provider_profile: Any,
    initial_parameters: Mapping[str, Any],
    authored_request_ref: str,
    authored_request_digest: str,
    task_input_snapshot_ref: str,
    task_input_snapshot_digest: str,
    execution_plan_store: Any | None = None,
    db_session: Any | None = None,
    trusted_repository_declarations: Mapping[str, Mapping[str, Any]] | None = None,
    workspace_source_kind: str | None = None,
    workspace_access_snapshot_ref: str | None = None,
    worker_authority_kinds: tuple[str, ...] | list[str] | None = None,
    repository_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    parent_repository_plan: OmnigentExecutionPlanEnvelope | None = None,
    parent_artifact_access: ArtifactAccessContext | None = None,
    access: ArtifactAccessContext,
    _compile_step_plans: bool = True,
    _recorded_step_parameters: Mapping[str, Mapping[str, Any]] | None = None,
    recorded_skillset_ref: str | None = None,
) -> PersistedOmnigentExecutionPlan:
    """Compile and persist one plan before Temporal or provider side effects."""

    target_runtime = str(initial_parameters.get("targetRuntime") or "").strip().lower()
    if target_runtime != OMNIGENT_RUNTIME_ID:
        raise ValueError(
            "Omnigent execution-plan compilation requires " "targetRuntime='omnigent'"
        )
    document = agent_profile_snapshot.get("document")
    if not isinstance(document, Mapping):
        raise ValueError("Agent Profile snapshot document is unavailable")
    harness_id = canonical_harness_id(document.get("harness"))
    # Prefer real synchronized catalog authority; fall back to hard-coded for tests
    real_config = await _try_load_real_harness_config(
        harness_id=harness_id,
        agent_profile_snapshot=agent_profile_snapshot,
        session_factory=session_factory,
        db_session=db_session,
    )
    config = real_config or _fixture_harness_config(harness_id)
    if config is None:
        raise ValueError(f"unsupported trusted Omnigent harness: {harness_id!r}")
    exact_catalog = real_config.get("_catalogSnapshot") if real_config else None
    freshness_catalog = (
        real_config.get("_freshnessCatalogSnapshot") if real_config else None
    )
    exact_harness = real_config.get("_harnessRecord") if real_config else None
    # Production uses the exact catalog pinned by the Agent Profile. The
    # latest lookup only supplies a version to hermetic/legacy profiles that do
    # not carry a catalog ref.
    catalog_omnigent_version = str(
        getattr(exact_catalog, "omnigentVersion", None) or "0.10.0"
    )
    if exact_catalog is None and callable(session_factory):
        try:
            from moonmind.omnigent.harness_platform.catalog_service import (
                DbHarnessCatalogRepository as _CatRepo,
            )

            _cat_repo = _CatRepo(session_factory)
            _latest_cat = await _cat_repo.latest(
                str(document.get("endpointRef") or "default")
            )
            if _latest_cat is not None:
                catalog_omnigent_version = str(_latest_cat.snapshot.omnigentVersion)
        except Exception:
            # The catalog lookup only sharpens the reported Omnigent version.
            # Planning authority comes from the profile document, so an
            # unavailable catalog keeps the documented default rather than
            # failing a plan that is otherwise fully attested.
            pass
    provider_profile_ref = str(
        getattr(provider_profile, "profile_id", None)
        or agent_profile_snapshot.get("providerProfileRef")
        or ""
    ).strip()
    if not provider_profile_ref:
        raise ValueError("Provider Profile selection is unavailable")
    provider_runtime_id = str(
        getattr(provider_profile, "runtime_id", None) or ""
    ).strip()
    provider_id = str(getattr(provider_profile, "provider_id", None) or "").strip()
    api_key_profile = harness_id in {
        "claude-native",
        "codex-native",
    } and is_api_key_launch_profile(provider_profile)
    if harness_id == "opencode-native" or api_key_profile:
        # The registry names the harness's OAuth materializer; these Profiles
        # carry their own credential shape instead.
        materializer_ref = (
            materializer_ref_for_profile(provider_profile)
            if api_key_profile
            else materializer_ref_for_provider(provider_runtime_id, provider_id)
        )
        materializer = get_materializer(materializer_ref)
        auth_models = list(materializer.acceptedAuthModels)
        if len(auth_models) != 1:
            raise ValueError(
                f"credential materializer {materializer_ref} must declare one auth model"
            )
        config = {
            **config,
            "materializerRef": materializer_ref,
            "authModel": auth_models[0],
        }
    launch_policy_ref = str(agent_profile_snapshot.get("launchPolicyRef") or "").strip()
    if not launch_policy_ref:
        raise ValueError("launch policy selection is unavailable")
    policy_snapshot = await _resolve_runtime_policy_snapshot(
        policy_ref=launch_policy_ref,
        session_factory=session_factory,
        db_session=db_session,
    )
    from moonmind.omnigent.execution_profiles import compile_persisted_effective_launch
    from moonmind.omnigent.codex_execution_decisions import (
        compile_follow_up_retrieval_policy,
        enforce_required_follow_up_retrieval,
    )

    authored_follow_up = (
        initial_parameters.get("followUpRetrieval")
        if isinstance(initial_parameters.get("followUpRetrieval"), Mapping)
        else {}
    )
    follow_up = compile_follow_up_retrieval_policy()
    enforce_required_follow_up_retrieval(authored_follow_up, follow_up)
    effective_launch = compile_persisted_effective_launch(
        policy_snapshot,
        provider_profile_id=provider_profile_ref,
        follow_up_retrieval=follow_up,
    )
    from moonmind.omnigent.harness_platform.host_classes import (
        launch_policy_from_effective_launch,
    )

    planner_launch_policy = launch_policy_from_effective_launch(
        effective_launch,
        expected_ref=launch_policy_ref,
    )
    expected_execution_profile = str(
        agent_profile_snapshot.get("executionProfileRef") or ""
    ).strip()
    if (
        expected_execution_profile
        and effective_launch.get("executionProfileRef") != expected_execution_profile
    ):
        raise ValueError("launch policy execution profile conflicts with Agent Profile")
    if str(effective_launch.get("harness") or "") != harness_id:
        raise ValueError("launch policy harness conflicts with Agent Profile")
    if isinstance(exact_harness, HarnessRecord):
        harness_record = exact_harness
        implementation = harness_record.implementation
    else:
        implementation = HarnessImplementationIdentity.model_validate(
            {
                "sourceKind": "core",
                "package": "omnigent",
                "version": "1.0.0",
                "digest": config["implementationDigest"],
                "pluginEntryPoint": None,
            }
        )
        harness_record = HarnessRecord.model_validate(
            {
                "id": harness_id,
                "aliases": [],
                "label": harness_id,
                "implementation": implementation.model_dump(mode="json", by_alias=True),
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": config["integrationMode"],
                    "authModel": config["authModel"],
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        )
    omnigent_build_digest = str(
        getattr(exact_catalog, "omnigentBuildDigest", None) or ""
    ).strip() or _image_digest(
        effective_launch.get("serverImageRef"),
        field_name="effective launch serverImageRef",
    )
    host_class = OmnigentHostClassSelector().select(
        harness=harness_record,
        omnigent_version=catalog_omnigent_version,
        integration_mode=config["integrationMode"],
        materializer_refs=[config["materializerRef"]],
        requested_host_mode=str(effective_launch.get("hostMode") or ""),
        requested_host_class_ref=config["hostClassRef"],
    )
    # The persisted policy snapshot records the images current when that
    # policy version was written; Host Class selection and the installed
    # server come from current deployment evidence. A fresh attempt binds the
    # installed same-repository images (launch-time attestation verifies the
    # actual host and server), so a routine update needs no new policy
    # version; a foreign host repository still fails closed instead of
    # substituting another image family.
    from moonmind.omnigent.host_image_drift import (
        installed_server_image_ref,
        reconcile_effective_launch_to_selected_host,
    )

    reconciled = reconcile_effective_launch_to_selected_host(
        effective_launch,
        host_class.imageRef,
        selected_server_image_ref=installed_server_image_ref(),
    )
    if reconciled is None:
        planned_ref = str(effective_launch.get("hostImageRef") or "")
        raise ValueError(
            "effective launch host image conflicts with the selected Host Class "
            f"(planned={planned_ref[:120]} selected={host_class.imageRef[:120]} "
            f"hostClass={config['hostClassRef']}); the policy names a different "
            "host image family than the installed one"
        )
    effective_launch = reconciled
    if planner_launch_policy.hostMode in {"static-connected", "static_compose"}:
        from moonmind.omnigent.harness_platform.static_hosts import (
            bind_static_host_selection,
        )
        from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostRepository

        host_session_factory = session_factory
        if db_session is not None:

            @asynccontextmanager
            async def host_session_factory():
                yield db_session

        effective_launch = await bind_static_host_selection(
            repository=OmnigentOAuthHostRepository(host_session_factory),
            effective_launch=effective_launch,
            commit_binding=db_session is None,
        )
    policy_artifact_ref, policy_artifact_digest = await persist_json_artifact(
        artifact_service=artifact_service,
        principal=principal,
        artifact_class="omnigent.launch_policy_snapshot",
        payload=policy_snapshot,
    )
    effective_launch_ref, effective_launch_digest = await persist_json_artifact(
        artifact_service=artifact_service,
        principal=principal,
        artifact_class="omnigent.effective_launch_snapshot",
        payload=effective_launch,
    )
    raw_architectures = effective_launch.get("architectures") or []
    host_architecture = str(raw_architectures[0] if raw_architectures else "").strip()
    if host_architecture and "/" not in host_architecture:
        host_architecture = f"linux/{host_architecture}"
    if not host_architecture or host_architecture not in host_class.architectures:
        raise ValueError(
            "launch policy architecture conflicts with the selected Host Class "
            f"(launch={host_architecture or '<missing>'} "
            f"hostClass={config['hostClassRef']} "
            f"supported={','.join(host_class.architectures) or '<none>'})"
        )
    matching_entry = next(
        (
            entry
            for entry in host_class.declaredHarnessImplementations
            if entry.harnessId == harness_id
            and entry.implementationRef == implementation.implementation_ref()
        ),
        None,
    )
    if matching_entry is None:
        raise ValueError(
            "Host Class does not declare the selected exact harness "
            f"(harness={harness_id} "
            f"implementation={implementation.implementation_ref()} "
            f"hostClass={config['hostClassRef']})"
        )
    catalog = exact_catalog or create_catalog_snapshot(
        endpointRef=str(document.get("endpointRef") or "default"),
        omnigentVersion=host_class.omnigentVersion,
        omnigentBuildDigest=host_class.omnigentBuildDigest,
        sourceDigest=str(agent_profile_snapshot.get("digest") or ""),
        observedAt=datetime.now(UTC),
        harnesses=[
            {
                "id": harness_id,
                "aliases": [],
                "label": harness_id,
                "implementation": implementation.model_dump(mode="json", by_alias=True),
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": config["integrationMode"],
                    "authModel": config["authModel"],
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
    )
    profile_snapshot_payload = dict(agent_profile_snapshot)
    profile_snapshot_ref, _profile_snapshot_digest = await persist_json_artifact(
        artifact_service=artifact_service,
        principal=principal,
        artifact_class="omnigent.agent_profile_snapshot",
        payload=profile_snapshot_payload,
    )
    (
        agent_resolved_skills,
        skill_ref,
        skill_digest,
        skill_content_refs,
    ) = await _resolve_and_persist_skills(
        session_factory=session_factory,
        db_session=db_session,
        artifact_service=artifact_service,
        principal=principal,
        workflow_id=workflow_id,
        task_input_snapshot_digest=task_input_snapshot_digest,
        initial_parameters=initial_parameters,
        access=access,
        recorded_skillset_ref=recorded_skillset_ref,
    )
    skill_delivery_ref = _digest_ref(
        "skill-delivery",
        {
            "resolvedSkillSetRef": skill_ref,
            "delivery": "immutable-run-snapshot",
        },
    )
    resolved_skills = ResolvedSkillSet.model_validate(
        {
            "resolvedSkillSetRef": f"artifact:{skill_ref}",
            "resolvedSkillSetDigest": skill_digest,
            "skillDeliveryRef": skill_delivery_ref,
        }
    )
    required_capabilities = tuple(
        sorted(
            {
                *(
                    str(value).strip().lower()
                    for value in initial_parameters.get("requiredCapabilities") or []
                    if str(value).strip()
                ),
                *(
                    str(capability).strip().lower()
                    for skill in agent_resolved_skills.skills
                    for capability in skill.required_capabilities
                    if str(capability).strip()
                ),
            }
        )
    )
    mounted_skill_tools = tuple(
        sorted(set(required_capabilities).intersection(deployment_mounted_tool_names()))
    )
    resolved_profile = _build_v2_profile(
        snapshot=agent_profile_snapshot,
        catalog_ref=catalog.catalogRef,
        implementation_ref=implementation.implementation_ref(),
        harness_id=harness_id,
        auth_model=config["authModel"],
        additional_tools=mounted_skill_tools,
    )
    native_review = await _verify_native_review_graph(
        initial_parameters=initial_parameters,
        artifact_service=artifact_service,
        principal=principal,
        task_input_snapshot_ref=task_input_snapshot_ref,
        task_input_snapshot_digest=task_input_snapshot_digest,
        access=access,
    )
    repository_access = {}
    if repository_bindings is None:
        repository_inputs = await _admit_repository_plan_inputs(
            session_factory=session_factory,
            db_session=db_session,
            artifact_service=artifact_service,
            principal=principal,
            workflow_id=workflow_id,
            initial_parameters=initial_parameters,
            requires_github="gh" in resolved_profile.tools,
            parent_plan=parent_repository_plan,
            parent_artifact_access=parent_artifact_access,
            native_review=native_review,
            resolved_skills=agent_resolved_skills,
            typed_authority=selected_realizer_consumes_repository_authority(
                harness_id=harness_id
            ),
            access=access,
        )
        repository_bindings = repository_inputs["bindings"]
        trusted_repository_declarations = repository_inputs["declarations"]
        workspace_source_kind = workspace_source_kind or repository_inputs["sourceKind"]
        workspace_access_snapshot_ref = repository_inputs.get("anonymousSnapshotRef")
        repository_access = repository_inputs["access"]
    bridge_capabilities = {
        "repository.read": True,
        "repository.write": True,
        "session.start": True,
        **{tool_name: True for tool_name in mounted_skill_tools},
    }
    binding_payloads: dict[str, dict[str, Any]] = {
        "primary-model": {
            "providerProfileRef": provider_profile_ref,
            "materializerRef": config["materializerRef"],
        }
    }
    if repository_bindings:
        if (
            any(
                binding.get("consumer") == "native"
                for binding in repository_bindings.values()
            )
            and not native_review
        ):
            raise ValueError(
                "native repository authority requires a frozen native review graph"
            )
        # A mixed model/repository plan uses the v2 envelope so repository
        # authority carries its explicit kind, delivery contract, and role.
        # Model entries without a discriminator upgrade to model authority
        # inside the versioned constructor.
        for slot, repo_binding in dict(repository_bindings).items():
            slot_name = str(slot or "").strip()
            if not slot_name:
                raise ValueError("repository binding requires a slot name")
            if slot_name in binding_payloads:
                raise ValueError(
                    f"repository binding slot {slot_name!r} conflicts with "
                    "the model authority slot"
                )
            if not isinstance(repo_binding, Mapping):
                raise ValueError(
                    f"repository binding for slot {slot_name!r} must be a mapping"
                )
            binding_payloads[slot_name] = dict(repo_binding)
        binding_set = create_binding_set(
            bindingSetId=f"{harness_id}.primary-model",
            version=int(agent_profile_snapshot.get("version") or 1),
            bindings=binding_payloads,
            schema_version="moonmind.omnigent-credential-bindings.v2",
        )
    else:
        binding_set = create_binding_set(
            bindingSetId=f"{harness_id}.primary-model",
            version=int(agent_profile_snapshot.get("version") or 1),
            bindings=binding_payloads,
        )
    if real_config is not None:
        trust = real_config.get("_freshnessTrustRecord")
        if not isinstance(trust, HarnessTrustRecord):
            raise HarnessPlatformError(
                "fresh harness trust decision is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED,
            )
    else:
        trust = classify_harness_trust(
            harnessId=harness_id,
            implementation=implementation,
            trustState=TrustState.core_trusted,
        )
    # The plan pins this exact catalog snapshot; persist it as durable
    # observation authority so launch-time host resolution can load it.
    # Hermetic callers may pass a non-callable session factory placeholder;
    # production always provides the canonical async session maker.
    if exact_catalog is None and callable(session_factory):
        from moonmind.omnigent.harness_platform.catalog_service import (
            DbHarnessCatalogRepository,
        )
        from moonmind.omnigent.harness_platform.catalog_service import (
            HarnessCatalogSyncResult as _CatalogSyncResult,
        )

        catalog_result = _CatalogSyncResult(
            snapshot=catalog,
            trust_records=(trust,),
            diagnostics={
                "harnessCount": 1,
                "agentCount": 0,
                "hostCount": 0,
                "source": "execution-plan-compilation",
            },
        )
        if db_session is not None:
            await DbHarnessCatalogRepository.persist_in_session(
                db_session, catalog_result
            )
        else:
            await DbHarnessCatalogRepository(session_factory).persist(catalog_result)
    repository = initial_parameters.get("repository")
    workspace = initial_parameters.get("workspace")
    repository_intent_ref = _digest_ref(
        "repository-intent", {"repository": repository, "workspace": workspace}
    )
    workflow_mapping = _workflow_payload(initial_parameters)
    authority = {
        "authoredRequestRef": authored_request_ref,
        "authoredRequestDigest": authored_request_digest,
        "taskInputSnapshotRef": task_input_snapshot_ref,
        "taskInputSnapshotDigest": task_input_snapshot_digest,
        "repositoryIntentRef": repository_intent_ref,
        "continuationPolicyRef": _digest_ref(
            "continuation-policy", document.get("continuations") or {}
        ),
        "remediationPolicyRef": _digest_ref(
            "remediation-policy", workflow_mapping.get("remediation") or {}
        ),
        "checkpointPolicyRef": _digest_ref(
            "checkpoint-policy", document.get("continuations") or {}
        ),
        "publicationPolicyRef": _digest_ref(
            "publication-policy",
            {
                "profile": document.get("publish") or {},
                "mode": initial_parameters.get("publishMode"),
            },
        ),
        "timingPolicyRef": _digest_ref(
            "timing-policy",
            {
                "priority": initial_parameters.get("priority"),
                "maxAttempts": initial_parameters.get("maxAttempts"),
            },
        ),
        "failurePolicyRef": _digest_ref(
            "failure-policy", {"maxAttempts": initial_parameters.get("maxAttempts")}
        ),
    }
    model = document.get("model")
    model_mapping = dict(model) if isinstance(model, Mapping) else {}
    raw_selected_model = (
        initial_parameters["model"]
        if "model" in initial_parameters
        else model_mapping.get("model")
    )
    selected_model = str(raw_selected_model or "").strip() or None
    if str(getattr(provider_profile, "runtime_id", "")) in {"codex_cli", "claude_code"}:
        from moonmind.workflows.executions.model_resolver import apply_profile_model_alias

        selected_model = apply_profile_model_alias(provider_profile, selected_model)
    # MoonLadderStudios/MoonMind#4009: repository slot declarations derive
    # from admitted profile authority plus trusted delivery / publication
    # declarations when those owners supply them (#4011/#1090; issuance is
    # #4007). Agent-supplied binding keys never create declarations.
    # Fail-closed ({}) without trusted input.
    repository_slot_requirements = derive_repository_slot_requirements(
        profile_document=dict(document),
        trusted_repository_declarations=trusted_repository_declarations,
    )
    plan = compile_execution_plan(
        agent_profile=resolved_profile,
        harness_catalog=catalog,
        freshness_catalog=freshness_catalog,
        trust_record=trust,
        resolved_skills=resolved_skills,
        credential_binding_set=binding_set,
        repository_slot_requirements=repository_slot_requirements,
        workspace_source_kind=workspace_source_kind,
        workspace_access_snapshot_ref=workspace_access_snapshot_ref,
        worker_authority_kinds=worker_authority_kinds,
        host_class_ref=host_class.ref,
        host_class=host_class,
        launch_policy_ref=launch_policy_ref,
        launch_policy=planner_launch_policy,
        model_qualified_id=selected_model,
        model_effort=(
            str(
                (initial_parameters["effort"] if "effort" in initial_parameters else model_mapping.get("effort")) or ""
            ).strip()
            or None
        ),
        model_route_ref=str(getattr(provider_profile, "provider_id", "") or "") or None,
        model_normalized_options=dict(model_mapping.get("settings") or {}),
        workflow_requirements=list(required_capabilities),
        # Capability admission consumes deployment-owned declarations only.
        # A workflow request can require a capability, but it cannot make that
        # capability available merely by naming it.
        bridge_capabilities=bridge_capabilities,
        # Runtime-owned requirements are satisfied at trusted MoonMind adapter
        # boundaries. They are not exact-host tool claims: the runtime token is
        # proven by this compiler, while fan-out is authorized from resolved
        # Skill provenance and materialized by the launch adapter.
        platform_capabilities={
            OMNIGENT_RUNTIME_ID: True,
            EXECUTION_FANOUT_REQUIRED_CAPABILITY: True,
        },
        workspace_intent_ref=_digest_ref(
            "workspace-intent", {"repository": repository, "workspace": workspace}
        ),
        policy_snapshot_ref=f"artifact:{policy_artifact_ref}",
        policy_snapshot_digest=policy_artifact_digest,
        effective_launch_snapshot_ref=f"artifact:{effective_launch_ref}",
        effective_launch_snapshot_digest=effective_launch_digest,
        host_image_ref=str(effective_launch.get("hostImageRef") or ""),
        omnigent_host_build_digest=host_class.omnigentBuildDigest,
        host_architecture=host_architecture,
        capture_policy_ref=_digest_ref("capture-policy", document.get("capture") or {}),
        execution_authority=authority,
        agent_profile_snapshot_ref=f"artifact:{profile_snapshot_ref}",
    )
    if repository_access:
        # Compact artifact/digest references travel in the existing tool and
        # transport delivery projection. Selection bytes remain artifact-owned;
        # issuance, paths and cleanup generations remain runtime-owned (#4009).
        plan = create_execution_plan_envelope(
            plan.payload.model_copy(
                update={
                    "resolvedTools": {
                        **plan.payload.resolvedTools,
                        "repositoryAccess": repository_access,
                    },
                }
            )
        )
    # A client may name an exact rollout row, but only the compiled Profile,
    # policy and harness can establish it. Validate before persisting the plan.
    runtime = workflow_mapping.get("runtime") or {}
    requested_target_id = runtime.get("targetId")
    if requested_target_id and (
        plan.payload.runtimeProviderRollout is None
        or plan.payload.runtimeProviderRollout.targetId != requested_target_id
    ):
        raise ValueError(
            "Requested runtime target does not match the selected Profile's "
            "execution plan."
        )
    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    # Execution evidence is policy-driven. MoonLadderStudios/MoonMind#4560:
    # ordinary admission (omitted/blank/shipped ``either``) is
    # certificate-independent: a compatible, authorized workflow must not
    # become unrunnable merely because a historical certificate is missing,
    # stale, or names another policy revision. Optional certificates remain
    # a truthful observation but never veto ordinary execution. Explicit
    # ``protected``/``deployment`` selects strict certification, chosen only
    # at the trusted settings boundary -- workflow-authored input can never
    # downgrade it or forge admission.
    try:
        from moonmind.omnigent.settings import (
            omnigent_evidence_policy,
            omnigent_requires_certification,
        )

        strict_admission = omnigent_requires_certification()
        support_evidence, support_tier = resolve_execution_evidence(plan.payload)
        # For deployment evidence, we still want to publish same artifact class
        # but supportTier distinguishes readiness (deployment_qualified vs supported)
    except ValueError as exc:
        # Provide actionable error that includes policy
        from moonmind.omnigent.settings import omnigent_evidence_policy

        policy = omnigent_evidence_policy()
        raise ValueError(
            f"execution evidence unavailable under policy={policy}: {exc}"
        ) from exc

    def _uncertified_ordinary_authority() -> AdmissionAuthority:
        # Certificate-independent ordinary admission. Never fabricate an
        # empty passing certificate, never substitute {} for evidence, and
        # never label uncertified execution supported/deployment_qualified.
        # Ordinary admission is not a claim execution has already succeeded.
        return AdmissionAuthority(
            admissionMode="ordinary",
            supportEvidenceRef="",
            supportEvidenceDigest="",
            supportTier="uncertified",
            featureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
            replayCompatibilityVersion=(OMNIGENT_SESSION_COMPATIBILITY_VERSION),
            rollbackPolicyVersion=SUPERVISOR_ROLLBACK_POLICY_VERSION,
        )

    if support_evidence is None:
        assert support_tier == "uncertified" and not strict_admission
        support_evidence_ref = ""
        plan = create_execution_plan_envelope(
            plan.payload.model_copy(
                update={"admissionAuthority": _uncertified_ordinary_authority()}
            )
        )
    else:
        try:
            support_evidence_ref, support_evidence_digest = await persist_json_artifact(
                artifact_service=artifact_service,
                principal=principal,
                artifact_class="omnigent.execution_support_evidence",
                payload=support_evidence,
            )
        except Exception as exc:
            if strict_admission:
                raise
            # Optional evidence persistence is advisory-only for ordinary
            # admission: an artifact-store failure must not veto otherwise
            # valid ordinary work. Fall back to the already supported
            # uncertified authority -- the same outcome as if the optional
            # certificate had been absent. The invalid/unpersisted report
            # authorizes nothing either way.
            logger.warning(
                "optional execution support evidence persistence failed; "
                "admitting ordinary uncertified execution: %s",
                exc,
            )
            support_evidence = None
            support_evidence_ref = ""
            plan = create_execution_plan_envelope(
                plan.payload.model_copy(
                    update={"admissionAuthority": _uncertified_ordinary_authority()}
                )
            )
        else:
            plan = create_execution_plan_envelope(
                plan.payload.model_copy(
                    update={
                        "admissionAuthority": AdmissionAuthority(
                            admissionMode=(
                                "strict" if strict_admission else "ordinary"
                            ),
                            supportEvidenceRef=f"artifact:{support_evidence_ref}",
                            supportEvidenceDigest=support_evidence_digest,
                            # The evidence resolver returns the tier that admission
                            # actually used; workers must re-validate the same schema.
                            supportTier=(
                                "supported"
                                if support_tier == "supported"
                                else "deployment_qualified"
                            ),
                            featureGeneration=OMNIGENT_SESSION_FEATURE_GENERATION,
                            replayCompatibilityVersion=(
                                OMNIGENT_SESSION_COMPATIBILITY_VERSION
                            ),
                            rollbackPolicyVersion=SUPERVISOR_ROLLBACK_POLICY_VERSION,
                        )
                    }
                )
            )
    step_plans = ()
    step_parameters = None
    if _compile_step_plans:
        from api_service.services.omnigent_step_plan_service import compile_step_execution_plans
        compiled_steps = await compile_step_execution_plans(
            root_plan=plan,
            recorded_step_parameters=_recorded_step_parameters,
            session_factory=session_factory,
            artifact_service=artifact_service,
            principal=principal,
            workflow_id=workflow_id,
            agent_profile_snapshot=agent_profile_snapshot,
            provider_profile=provider_profile,
            initial_parameters=initial_parameters,
            authored_request_ref=authored_request_ref,
            authored_request_digest=authored_request_digest,
            task_input_snapshot_ref=task_input_snapshot_ref,
            task_input_snapshot_digest=task_input_snapshot_digest,
            execution_plan_store=execution_plan_store,
            db_session=db_session,
            trusted_repository_declarations=trusted_repository_declarations,
            workspace_source_kind=workspace_source_kind,
            workspace_access_snapshot_ref=workspace_access_snapshot_ref,
            worker_authority_kinds=worker_authority_kinds,
            repository_bindings=repository_bindings,
            parent_repository_plan=parent_repository_plan,
            parent_artifact_access=parent_artifact_access,
            access=access,
        )
        step_plans, step_parameters = compiled_steps.plans, compiled_steps.parameters
        if step_parameters:
            plan = create_execution_plan_envelope(plan.payload.model_copy(update={
                "logicalStepPlans": logical_step_plan_digests(step_parameters),
            }))
    plan_store = execution_plan_store or (
        SessionExecutionPlanStore(db_session)
        if db_session is not None
        else DbExecutionPlanStore(session_factory)
    )
    persisted = await plan_store.persist(plan)
    plan_payload = persisted.model_dump(mode="json", by_alias=True)
    plan_artifact_ref, _artifact_digest = await persist_json_artifact(
        artifact_service=artifact_service,
        principal=principal,
        artifact_class="omnigent.execution_plan",
        payload=plan_payload,
    )
    plan_digest = "sha256:" + persisted.planRef.rsplit(":", 1)[-1]
    binding = OmnigentExecutionPlanBinding(
        planRef=persisted.planRef,
        planDigest=plan_digest,
        planArtifactRef=plan_artifact_ref,
        taskInputSnapshotRef=task_input_snapshot_ref,
        taskInputSnapshotDigest=task_input_snapshot_digest,
    )
    from moonmind.omnigent.bridge_artifacts import (
        ensure_verified_execution_plan_dependencies,
    )

    await ensure_verified_execution_plan_dependencies(
        artifacts=artifact_service,
        plan=persisted,
        binding=binding,
        access=access,
    )
    frozen_rollout = persisted.payload.runtimeProviderRollout
    _evidence_refs: tuple[str, ...] = ()
    if support_evidence is not None:
        _evidence_refs = (support_evidence_ref,)
    admitted = PersistedOmnigentExecutionPlan(
        artifact_access=access,
        envelope=persisted,
        binding=binding,
        runtime_provider_rollout=(
            frozen_rollout.model_dump(mode="json", by_alias=True)
            if frozen_rollout is not None
            else None
        ),
        artifact_refs=(
            policy_artifact_ref,
            effective_launch_ref,
            profile_snapshot_ref,
            *_evidence_refs,
            *skill_content_refs,
            *(access["artifactRef"] for access in repository_access.values()),
            skill_ref,
            plan_artifact_ref,
            *(ref for step_plan in step_plans for ref in step_plan.artifact_refs),
        ),
        resolved_skillset_ref=skill_ref,
        resolved_skillset_digest=skill_digest,
        step_plans=step_plans,
        step_parameters=step_parameters,
    )
    return admitted


async def frozen_task_input_provenance(
    db_session: Any, binding: OmnigentExecutionPlanBinding
) -> str:
    """Return creator provenance for accounting; this is not read authority.

    The original task input survives every plan compile. Its creator records
    provenance even when a service compiles the plan or a legacy schedule
    owner no longer exists. Artifact reads require their own trusted context.
    """

    from api_service.db.models import TemporalArtifact

    task_artifact = await db_session.get(
        TemporalArtifact,
        binding.task_input_snapshot_ref.removeprefix("artifact://").removeprefix(
            "artifact:"
        ),
    )
    principal = str(getattr(task_artifact, "created_by_principal", None) or "").strip()
    if not principal or getattr(
        task_artifact, "sha256", None
    ) != binding.task_input_snapshot_digest.removeprefix("sha256:"):
        raise ValueError(
            "original task-input principal authority is unavailable or does not "
            "match the frozen snapshot"
        )
    return principal


async def frozen_execution_artifact_access(
    db_session: Any, binding: OmnigentExecutionPlanBinding, *, workflow_id: str
) -> ArtifactAccessContext:
    """Recover only a persisted plan's admitted closure, never creator authority."""
    from moonmind.omnigent.bridge_artifacts import (
        _artifact_ids,
        _execution_plan_input_refs,
    )
    from moonmind.omnigent.harness_platform.execution_plan import (
        verify_execution_plan_envelope,
    )
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore

    plan = await SessionExecutionPlanStore(db_session).load(binding.plan_ref)
    if plan is None:
        raise ValueError("persisted execution-plan read authority is unavailable")
    plan = verify_execution_plan_envelope(plan)
    if "sha256:" + plan.planRef.rsplit(":", 1)[-1] != binding.plan_digest:
        raise ValueError(
            "persisted execution-plan read authority conflicts with binding"
        )
    authority = plan.payload.authority
    if authority is not None and (
        authority.taskInputSnapshotRef != binding.task_input_snapshot_ref
        or authority.taskInputSnapshotDigest != binding.task_input_snapshot_digest
    ):
        raise ValueError("persisted plan conflicts with original task-input authority")
    from moonmind.workflows.temporal.artifacts import TemporalArtifactRepository

    repository = TemporalArtifactRepository(db_session)
    root_id = binding.plan_artifact_ref.removeprefix("artifact://").removeprefix(
        "artifact:"
    )
    root = await repository.get_artifact(root_id)
    if root.sha256 != json_artifact_digest(
        plan.model_dump(mode="json", by_alias=True)
    ).removeprefix("sha256:"):
        raise ValueError(
            "persisted plan artifact conflicts with original input authority"
        )
    dependencies = await repository.get_verified_dependencies(root_id)
    ids = (
        {root_id, *(entry.artifact_id for entry in dependencies)}
        if dependencies is not None
        else _artifact_ids(_execution_plan_input_refs(plan, binding))
    )
    return ArtifactAccessContext.for_execution(
        workflow_id, principal="service:execution-plan"
    )._with_verified_inputs(ids)


async def compile_frozen_execution_plan(
    *,
    db_session: Any,
    artifact_service: Any,
    workflow_id: str,
    principal: str,
    snapshot: Mapping[str, Any],
    provider_profile: Any,
    initial_parameters: Mapping[str, Any],
    binding: OmnigentExecutionPlanBinding,
    parent_repository_plan: OmnigentExecutionPlanEnvelope | None = None,
    access: ArtifactAccessContext,
    refresh_step_selections: bool = False,
) -> PersistedOmnigentExecutionPlan:
    """Compile an admitted workflow's frozen inputs again through this owner."""

    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    artifact_service = artifact_service or TemporalArtifactService(
        TemporalArtifactRepository(db_session),
        transaction_ownership=ArtifactTransactionOwnership.CALLER,
    )
    compilation_parameters = dict(initial_parameters)
    compilation_parameters.pop("omnigentExecutionPlan", None)
    compilation_parameters.pop("resolvedSkillsetRef", None)
    recorded_plan = await SessionExecutionPlanStore(db_session).load(binding.plan_ref)
    if recorded_plan is None:
        raise ValueError("the recorded execution plan is unavailable")
    recorded_plan = verify_execution_plan_envelope(recorded_plan)
    recorded_steps = initial_parameters.get("omnigentStepExecutionPlans")
    if logical_step_plan_digests(recorded_steps) != (recorded_plan.payload.logicalStepPlans or {}):
        raise ValueError("logical step plan authority conflicts with its recorded root plan")
    return await compile_and_persist_execution_plan(
        session_factory=None,
        execution_plan_store=SessionExecutionPlanStore(db_session),
        artifact_service=artifact_service,
        principal=principal,
        workflow_id=workflow_id,
        agent_profile_snapshot=dict(snapshot),
        provider_profile=provider_profile,
        initial_parameters=compilation_parameters,
        authored_request_ref=binding.task_input_snapshot_ref,
        authored_request_digest=binding.task_input_snapshot_digest,
        task_input_snapshot_ref=binding.task_input_snapshot_ref,
        task_input_snapshot_digest=binding.task_input_snapshot_digest,
        db_session=db_session,
        parent_repository_plan=parent_repository_plan,
        access=access,
        _compile_step_plans=refresh_step_selections or bool(recorded_steps),
        _recorded_step_parameters=None if refresh_step_selections else recorded_steps,
    )


async def _resolver_child_frozen_inputs(
    *,
    artifact_service: Any,
    principal: str,
    binding: OmnigentExecutionPlanBinding,
    recorded_plan: OmnigentExecutionPlanEnvelope,
    access: ArtifactAccessContext,
) -> tuple[dict[str, Any], str, dict[str, Any]] | None:
    """Return a resolver child's frozen Profile, Provider Profile and inputs.

    Merge automation compiles its resolver child from the parent's plan, so the
    child workflow carries no Agent Profile snapshot. The child's own task-input
    snapshot and recorded plan hold the same authority. Returns ``None`` for
    any other workflow.
    """

    from moonmind.omnigent.harness_platform.credential_bindings import model_bindings_of

    def artifact_id(ref: str) -> str:
        return ref.removeprefix("artifact://").removeprefix("artifact:")

    _artifact, body = await artifact_service.read(
        artifact_id=artifact_id(binding.task_input_snapshot_ref), access=access
    )
    if _sha256(body) != binding.task_input_snapshot_digest:
        raise ValueError("frozen task-input snapshot digest mismatch")
    task_snapshot = json.loads(body)
    source = task_snapshot.get("source") if isinstance(task_snapshot, Mapping) else None
    if not isinstance(source, Mapping) or source.get("kind") != (
        "merge_automation_resolver_child"
    ):
        return None
    try:
        child_parameters = dict(task_snapshot["target"]["initialParameters"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "resolver child task input lacks its initial parameters"
        ) from exc
    profile_ref = str(recorded_plan.payload.agentProfileSnapshotRef or "")
    if not profile_ref.startswith("artifact:"):
        raise ValueError("recorded execution plan lacks Agent Profile authority")
    _artifact, profile_body = await artifact_service.read(
        artifact_id=artifact_id(profile_ref), access=access
    )
    provider_profile_refs = {
        model_binding.providerProfileRef
        for model_binding in model_bindings_of(
            recorded_plan.payload.credentialBindings
        ).values()
    }
    if len(provider_profile_refs) != 1:
        raise ValueError(
            "recorded execution plan has ambiguous Provider Profile authority"
        )
    return (
        json.loads(profile_body),
        next(iter(provider_profile_refs)),
        child_parameters,
    )


def changed_admitted_intent(
    recorded: OmnigentExecutionPlanPayload,
    admitted: OmnigentExecutionPlanPayload,
) -> list[str]:
    """Name the requested-outcome authority a re-admission would change.

    Images, Host Class revision, harness implementation digest, Skill and
    snapshot artifact refs, and time-bounded repository access snapshot refs
    move with the installed runtime; ``changed_repository_selection`` compares
    what those snapshots select. Harness, Provider Profile account, model and
    effort, launch (cost/egress) and capture (privacy) policy, source, and
    publication intent belong to the request and must not.
    """

    def intent(payload: OmnigentExecutionPlanPayload) -> dict[str, Any]:
        model = payload.modelConfig
        rollout = payload.runtimeProviderRollout
        return {
            "harnessId": payload.harnessId,
            "endpointRef": payload.endpointRef,
            "executionRealizerRef": payload.executionRealizerRef,
            "launchPolicyRef": payload.launchPolicyRef,
            "capturePolicy": (payload.capturePolicyRef, payload.capturePolicy),
            "model": (
                model.qualifiedId,
                model.effort,
                model.routeRef,
                model.normalizedOptions,
            ),
            "credentialBindings": {
                slot: {
                    key: value
                    for key, value in binding.model_dump(
                        by_alias=True, mode="json"
                    ).items()
                    if key != "repositoryAccessSnapshotRef"
                }
                for slot, binding in payload.credentialBindings.items()
            },
            "authority": payload.authority,
            "workspace": (payload.workspaceIntentRef, payload.workspaceMutation),
            "runtimeProviderTarget": rollout.targetId if rollout else None,
        }

    before, after = intent(recorded), intent(admitted)
    return [field for field in before if before[field] != after[field]]


#: Repository selection fields that identify which repository authority a plan
#: holds. Revisions and the authorization time refresh on every compile.
_STABLE_REPOSITORY_SELECTION_KEYS = (
    "endpoint",
    "routeId",
    "repositoryDisplay",
    "connectionId",
    "role",
    "accessMode",
)


async def changed_repository_selection(
    *,
    artifact_service: Any,
    principal: str,
    recorded: OmnigentExecutionPlanPayload,
    admitted: OmnigentExecutionPlanPayload,
    access: ArtifactAccessContext,
) -> list[str]:
    """Name the repository slots whose selected authority would change.

    Each slot's access snapshot carries the selected endpoint, route and
    connection, which the binding's ``connectionRef`` and role alone do not.
    """

    async def selections(payload: OmnigentExecutionPlanPayload) -> dict[str, Any]:
        result = {}
        repository_entries = (payload.resolvedTools or {}).get("repositoryAccess") or {}
        for slot, entry in repository_entries.items():
            try:
                artifact_ref, snapshot_ref = entry["artifactRef"], entry["snapshotRef"]
            except (KeyError, TypeError) as exc:
                raise ValueError(f"repository access {slot} is malformed") from exc
            _artifact, body = await artifact_service.read(
                artifact_id=str(artifact_ref).removeprefix("artifact:"), access=access
            )
            if "repository-access-snapshot:" + _sha256(body) != snapshot_ref:
                raise ValueError(f"repository access snapshot {slot} digest mismatch")
            selection = json.loads(body).get("selection")
            if not isinstance(selection, Mapping):
                raise ValueError(f"repository access snapshot {slot} lacks a selection")
            result[slot] = {
                **{
                    key: selection.get(key) for key in _STABLE_REPOSITORY_SELECTION_KEYS
                },
                "operations": sorted(selection.get("operations") or ()),
            }
        return result

    before, after = await selections(recorded), await selections(admitted)
    return [
        f"repositoryAccess.{slot}"
        for slot in sorted(before.keys() | after.keys())
        if before.get(slot) != after.get(slot)
    ]


async def _retire_refused_plan_artifacts(
    *, artifact_service: Any, principal: str, artifact_refs: tuple[str, ...]
) -> None:
    """Delete a refused candidate's artifacts; its plan row is rolled back.

    Retirement is cleanup: a failure is logged and the recorded plan still
    launches.
    """

    for ref in artifact_refs:
        artifact_id = ref.removeprefix("artifact://").removeprefix("artifact:")
        try:
            await artifact_service.soft_delete(
                artifact_id=artifact_id, principal=principal
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask refusal
            logger.warning(
                "refused execution-plan artifact %s was not retired: %s",
                artifact_id,
                redact_sensitive_text(str(exc))[:200],
            )


async def readmit_frozen_execution_plan(
    *,
    db_session: Any,
    artifact_service: Any,
    workflow_id: str,
    initial_parameters: Mapping[str, Any],
    subject: str,
    preserve_recorded_intent: bool = False,
) -> dict[str, Any]:
    """Admit an already-planned workflow's frozen inputs on the installed runtime.

    The frozen inputs (task input, Agent Profile snapshot, Provider Profile,
    model and Skills) compile again, so the new plan binds the installed
    same-repository host and server; an explicitly requested Host Class stays
    exact and a foreign host family fails closed. A merge-automation resolver
    child recompiles from its own frozen task input and recorded plan, within
    its recorded repository authority. Plans that attempts already
    used are never rewritten. With ``preserve_recorded_intent`` the new plan
    is refused when it would change the recorded request intent.

    Returns ``disposition`` ``admitted`` with the new plan binding and Skill
    snapshot, or ``recorded`` with the reason when the frozen authority cannot
    be admitted now; the recorded plan then launches under its own launch-time
    checks.
    """

    from api_service.db.models import ManagedAgentProviderProfile
    from api_service.services.omnigent_policies import PolicyConflict, PolicyNotFound
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactError,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )

    def recorded(reason: str) -> dict[str, Any]:
        logger.warning(
            "%s %s keeps its recorded execution plan: %s", subject, workflow_id, reason
        )
        return {
            "disposition": "recorded",
            "reason": redact_sensitive_text(reason)[:500],
        }

    try:
        binding = OmnigentExecutionPlanBinding.model_validate(
            initial_parameters.get("omnigentExecutionPlan")
        )
    except Exception as exc:
        raise ValueError("Omnigent execution-plan authority is invalid") from exc
    artifact_service = artifact_service or TemporalArtifactService(
        TemporalArtifactRepository(db_session),
        transaction_ownership=ArtifactTransactionOwnership.CALLER,
    )
    snapshot = initial_parameters.get("agentProfileSnapshot")
    recorded_plan = None
    persisted_plan = None
    if preserve_recorded_intent or not isinstance(snapshot, Mapping):
        recorded_plan = await SessionExecutionPlanStore(db_session).load(
            binding.plan_ref
        )
        if recorded_plan is None:
            return recorded("the recorded execution plan is unavailable")
    try:
        access = await frozen_execution_artifact_access(
            db_session, binding, workflow_id=workflow_id
        )
        principal = await frozen_task_input_provenance(db_session, binding)
        compilation_parameters = initial_parameters
        parent_repository_plan = None
        if isinstance(snapshot, Mapping):
            provider_profile_ref = str(snapshot.get("providerProfileRef") or "")
        else:
            child_inputs = await _resolver_child_frozen_inputs(
                artifact_service=artifact_service,
                principal=principal,
                binding=binding,
                recorded_plan=recorded_plan,
                access=access,
            )
            if child_inputs is None:
                return recorded("the workflow has no Agent Profile snapshot")
            snapshot, provider_profile_ref, compilation_parameters = child_inputs
            # The child's recorded repository authority bounds its successor,
            # as the parent's bounded the child when it was first compiled.
            parent_repository_plan = recorded_plan
        provider_profile = await db_session.get(
            ManagedAgentProviderProfile, provider_profile_ref.strip()
        )
        if provider_profile is None:
            return recorded("the admitted Provider Profile is unavailable")
        persisted_plan = await compile_frozen_execution_plan(
            db_session=db_session,
            artifact_service=artifact_service,
            workflow_id=workflow_id,
            principal=principal,
            snapshot=snapshot,
            provider_profile=provider_profile,
            initial_parameters=compilation_parameters,
            binding=binding,
            parent_repository_plan=parent_repository_plan,
            access=access,
        )
        changed = []
        if recorded_plan is not None:
            changed = changed_admitted_intent(
                recorded_plan.payload, persisted_plan.envelope.payload
            ) + await changed_repository_selection(
                artifact_service=artifact_service,
                principal=principal,
                recorded=recorded_plan.payload,
                admitted=persisted_plan.envelope.payload,
                # These are the verified outputs just created by this compiler,
                # not refs chosen by the request or a creator identity.
                access=access._with_verified_inputs(
                    ref.removeprefix("artifact://").removeprefix("artifact:")
                    for ref in persisted_plan.artifact_refs
                ),
            )
        if preserve_recorded_intent and initial_parameters.get("omnigentStepExecutionPlans"):
            recorded_steps = initial_parameters["omnigentStepExecutionPlans"]
            admitted_steps = persisted_plan.step_parameters or {}
            if set(recorded_steps) != set(admitted_steps):
                changed.append("logical step plan selection")
            plans_by_ref = {plan.binding.plan_ref: plan for plan in persisted_plan.step_plans}
            for step_id in set(recorded_steps).intersection(admitted_steps):
                recorded_step = await SessionExecutionPlanStore(db_session).load(
                    recorded_steps[step_id]["omnigentExecutionPlan"]["planRef"]
                )
                admitted_step = plans_by_ref[
                    admitted_steps[step_id]["omnigentExecutionPlan"]["planRef"]
                ].envelope
                if recorded_step is None:
                    raise ValueError(f"the recorded execution plan for step {step_id} is unavailable")
                step_changed = changed_admitted_intent(recorded_step.payload, admitted_step.payload)
                step_changed += await changed_repository_selection(
                    artifact_service=artifact_service,
                    principal=principal,
                    recorded=recorded_step.payload,
                    admitted=admitted_step.payload,
                    access=access._with_verified_inputs(
                        ref.removeprefix("artifact://").removeprefix("artifact:")
                        for ref in persisted_plan.artifact_refs
                    ),
                )
                changed.extend(f"step {step_id}: {field}" for field in step_changed)
        if changed:
            raise ValueError(
                "re-admission would change the recorded request intent: "
                + ", ".join(changed)
            )
    except (
        HarnessPlatformError,
        PolicyConflict,
        PolicyNotFound,
        TemporalArtifactError,
        ValueError,
    ) as exc:
        await db_session.rollback()
        if persisted_plan is not None:
            await _retire_refused_plan_artifacts(
                artifact_service=artifact_service,
                principal=principal,
                artifact_refs=persisted_plan.artifact_refs,
            )
        return recorded(f"{type(exc).__name__}: {exc}")
    await db_session.commit()
    return {"disposition": "admitted", **persisted_plan.execution_parameters}


__all__ = [
    "PersistedOmnigentExecutionPlan",
    "changed_admitted_intent",
    "changed_repository_selection",
    "compile_and_persist_execution_plan",
    "compile_frozen_execution_plan",
    "frozen_task_input_provenance",
    "json_artifact_digest",
    "persist_json_artifact",
    "readmit_frozen_execution_plan",
    "selected_skill_names",
    "load_protected_execution_support_evidence",
]
