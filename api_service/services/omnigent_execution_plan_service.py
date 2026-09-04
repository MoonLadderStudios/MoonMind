"""Compile and persist Omnigent execution authority at the product boundary.

This module is intentionally an API/service boundary.  It may read deployment
registries and persist artifacts/rows; Temporal workflows only receive the
compact binding returned here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    HarnessRecord,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.execution_plan import (
    AdmissionAuthority,
    OmnigentExecutionPlanEnvelope,
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HostClass,
    OmnigentHostClassSelector,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    materializer_ref_for_provider,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.mounted_tools import (
    deployment_mounted_tool_names,
)
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.schemas.agent_skill_models import (
    AgentSkillFormat,
    RuntimeMaterializationMode,
    SkillSelector,
    SkillSelectorEntry,
)
from moonmind.schemas.agent_skill_models import (
    ResolvedSkillSet as AgentResolvedSkillSet,
)
from moonmind.security.execution_fanout_capabilities import (
    EXECUTION_FANOUT_REQUIRED_CAPABILITY,
)
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    SkillResolutionContext,
)
from moonmind.workflows.temporal.remediation_loop import (
    ToolDescriptor,
    tool_descriptor_selected_skill,
)
from moonmind.omnigent.harness_platform.harness_registry import (
    canonical_harness_id,
    find_harness_registration,
    harness_registration,
)
from pr_resolver_core import IMPLEMENTATION_CONTRACT

# Hermetic fixture digests for the approved harnesses. Every other product
# authority ref comes from the one harness registration registry
# (MoonLadderStudios/MoonMind#3711); only the placeholder implementation digest
# is fixture data, because a real digest requires a synchronized catalog.
_HARNESS_FIXTURE_IMPLEMENTATION_DIGESTS: dict[str, str] = {
    "codex-native": "sha256:" + "e" * 64,
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
) -> dict[str, Any] | None:
    """Try to load authoritative harness config from synchronized catalog.

    Returns the product configuration plus the exact catalog and harness
    authorities pinned by the Agent Profile. Hermetic callers without catalog
    storage receive ``None`` and use the bounded fixture configuration.
    """
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
        # Try catalogRef first, then latest
        catalog_result = None
        if catalog_ref:
            catalog_result = await repo.load(catalog_ref)
        if catalog_result is None:
            catalog_result = await repo.latest(endpoint_ref)
        if catalog_result is None:
            return None
        harness_record = next(
            (h for h in catalog_result.snapshot.harnesses if h.id == harness_id),
            None,
        )
        if harness_record is None:
            return None
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
            "_harnessRecord": harness_record,
        }
    except Exception:
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
    envelope: OmnigentExecutionPlanEnvelope
    binding: OmnigentExecutionPlanBinding
    artifact_refs: tuple[str, ...]
    resolved_skillset_ref: str
    resolved_skillset_digest: str


def selected_skill_names(initial_parameters: Mapping[str, Any]) -> list[str]:
    """Collect the workflow's MoonMind Skill intent for one run snapshot."""

    workflow = initial_parameters.get("workflow")
    workflow_mapping = dict(workflow) if isinstance(workflow, Mapping) else {}
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
    workflow = initial_parameters.get("workflow")
    workflow_mapping = dict(workflow) if isinstance(workflow, Mapping) else {}
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
) -> tuple[AgentResolvedSkillSet, str, str, tuple[str, ...]]:
    """Resolve once at admission and persist exact content plus its manifest."""

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
        ),
    )
    from moonmind.workflows.agent_skills.agent_skills_activities import (
        AgentSkillsActivities,
    )

    content_refs: list[str] = []
    persisted_entries = []
    for skill in resolved.skills:
        if skill.content_ref:
            if not str(skill.content_digest or "").startswith("sha256:"):
                raise ValueError(
                    f"selected Skill '{skill.skill_name}' lacks content digest evidence"
                )
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
    if source.get("upstreamId"):
        agent_source: dict[str, Any] = {
            "kind": "upstream",
            "upstreamId": str(source["upstreamId"]),
            "upstreamVersion": str(source.get("upstreamVersion") or "0.0.0"),
            "upstreamSnapshotDigest": snapshot_digest,
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
        agent_source = {
            "kind": "bundle",
            "bundleArtifactRef": bundle_ref,
            "bundleDigest": bundle_digest,
            "importReceiptRef": import_receipt,
            "importedAgentId": str(snapshot.get("agentId") or ""),
            "importedAgentVersion": str(snapshot.get("version") or ""),
            "importedContentDigest": snapshot_digest,
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
    )
    config = real_config or _fixture_harness_config(harness_id)
    if config is None:
        raise ValueError(f"unsupported trusted Omnigent harness: {harness_id!r}")
    exact_catalog = real_config.get("_catalogSnapshot") if real_config else None
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
    if harness_id == "opencode-native":
        materializer_ref = materializer_ref_for_provider(
            provider_runtime_id,
            provider_id,
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
    from moonmind.omnigent.profile_bound_execution import (
        _compile_persisted_effective_launch,
        compile_follow_up_retrieval_policy,
        enforce_required_follow_up_retrieval,
    )

    authored_follow_up = (
        initial_parameters.get("followUpRetrieval")
        if isinstance(initial_parameters.get("followUpRetrieval"), Mapping)
        else {}
    )
    follow_up = compile_follow_up_retrieval_policy(
        policy_snapshot,
        initial_parameters,
        repository=str(initial_parameters.get("repository") or "").strip(),
        tenant_id=str(
            authored_follow_up.get("tenantId")
            or initial_parameters.get("tenantId")
            or "default"
        ).strip(),
    )
    enforce_required_follow_up_retrieval(authored_follow_up, follow_up)
    effective_launch = _compile_persisted_effective_launch(
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
    if harness_id == "codex-native":
        architectures = [
            value if "/" in value else f"linux/{value}"
            for value in (
                str(item).strip()
                for item in (effective_launch.get("architectures") or [])
            )
            if value
        ]
        host_class = HostClass.model_validate(
            {
                "hostClassId": config["hostClassRef"].rpartition("@")[0],
                "version": int(config["hostClassRef"].rpartition("@")[2]),
                "imageRef": effective_launch.get("hostImageRef"),
                "omnigentVersion": catalog_omnigent_version,
                "omnigentBuildDigest": omnigent_build_digest,
                "architectures": architectures,
                "declaredHarnessImplementations": [
                    {
                        "harnessId": harness_id,
                        "implementationRef": implementation.implementation_ref(),
                        "runtimeDependencies": [],
                    }
                ],
                "integrationModes": [config["integrationMode"]],
                "materializerRefs": [config["materializerRef"]],
                "features": {
                    "git": True,
                    "tmux": True,
                    "bubblewrap": True,
                    "workspaceBind": True,
                    "readOnlyRoot": bool(effective_launch.get("readOnlyRoot")),
                    "restrictedEgress": bool(effective_launch.get("enforcedEgress")),
                    "mountedSkills": True,
                    "mountedTools": True,
                },
                "runtime": {
                    "uid": int(effective_launch.get("runtimeUid") or 1000),
                    "gid": int(effective_launch.get("runtimeGid") or 1000),
                    "home": "/home/app",
                },
            }
        )
    else:
        host_class = OmnigentHostClassSelector().select(
            harness=harness_record,
            omnigent_version=catalog_omnigent_version,
            omnigent_build_digest=omnigent_build_digest,
            integration_mode=config["integrationMode"],
            materializer_refs=[config["materializerRef"]],
            requested_host_mode=str(effective_launch.get("hostMode") or ""),
            requested_host_class_ref=config["hostClassRef"],
        )
    if host_class.imageRef != str(effective_launch.get("hostImageRef") or ""):
        raise ValueError(
            "effective launch host image conflicts with the selected Host Class"
        )
    raw_architectures = effective_launch.get("architectures") or []
    host_architecture = str(raw_architectures[0] if raw_architectures else "").strip()
    if host_architecture and "/" not in host_architecture:
        host_architecture = f"linux/{host_architecture}"
    if not host_architecture or host_architecture not in host_class.architectures:
        raise ValueError(
            "launch policy architecture conflicts with the selected Host Class"
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
        raise ValueError("Host Class does not declare the selected exact harness")
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
        artifact_service=artifact_service,
        principal=principal,
        workflow_id=workflow_id,
        task_input_snapshot_digest=task_input_snapshot_digest,
        initial_parameters=initial_parameters,
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
    bridge_capabilities = {
        "repository.read": True,
        "repository.write": True,
        "session.start": True,
        **{tool_name: True for tool_name in mounted_skill_tools},
    }
    binding_set = create_binding_set(
        bindingSetId=f"{harness_id}.primary-model",
        version=int(agent_profile_snapshot.get("version") or 1),
        bindings={
            "primary-model": {
                "providerProfileRef": provider_profile_ref,
                "materializerRef": config["materializerRef"],
            }
        },
    )
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
            HarnessCatalogSyncResult as _CatalogSyncResult,
        )

        await DbHarnessCatalogRepository(session_factory).persist(
            _CatalogSyncResult(
                snapshot=catalog,
                trust_records=(trust,),
                diagnostics={
                    "harnessCount": 1,
                    "agentCount": 0,
                    "hostCount": 0,
                    "source": "execution-plan-compilation",
                },
            )
        )
    repository = initial_parameters.get("repository")
    workspace = initial_parameters.get("workspace")
    repository_intent_ref = _digest_ref(
        "repository-intent", {"repository": repository, "workspace": workspace}
    )
    workflow_payload = initial_parameters.get("workflow")
    workflow_mapping = (
        dict(workflow_payload) if isinstance(workflow_payload, Mapping) else {}
    )
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
    plan = compile_execution_plan(
        agent_profile=_build_v2_profile(
            snapshot=agent_profile_snapshot,
            catalog_ref=catalog.catalogRef,
            implementation_ref=implementation.implementation_ref(),
            harness_id=harness_id,
            auth_model=config["authModel"],
            additional_tools=mounted_skill_tools,
        ),
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=resolved_skills,
        credential_binding_set=binding_set,
        host_class_ref=host_class.ref,
        host_class=host_class,
        launch_policy_ref=launch_policy_ref,
        launch_policy=planner_launch_policy,
        model_qualified_id=(
            str(
                initial_parameters.get("model") or model_mapping.get("model") or ""
            ).strip()
            or None
        ),
        model_effort=(
            str(
                initial_parameters.get("effort") or model_mapping.get("effort") or ""
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
    from moonmind.omnigent.session_supervisor_rollback import (
        SUPERVISOR_ROLLBACK_POLICY_VERSION,
    )
    from moonmind.schemas.omnigent_session_models import (
        OMNIGENT_SESSION_COMPATIBILITY_VERSION,
        OMNIGENT_SESSION_FEATURE_GENERATION,
    )

    # Execution evidence is policy-driven. The default ``either`` policy
    # prefers protected evidence and otherwise uses local deployment
    # qualification; ``protected`` remains the strict support-certification
    # gate. The resolver chooses the appropriate evidence or fails closed.
    try:
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
    support_evidence_ref, support_evidence_digest = await persist_json_artifact(
        artifact_service=artifact_service,
        principal=principal,
        artifact_class="omnigent.execution_support_evidence",
        payload=support_evidence,
    )
    plan = create_execution_plan_envelope(
        plan.payload.model_copy(
            update={
                "admissionAuthority": AdmissionAuthority(
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
                    replayCompatibilityVersion=(OMNIGENT_SESSION_COMPATIBILITY_VERSION),
                    rollbackPolicyVersion=SUPERVISOR_ROLLBACK_POLICY_VERSION,
                )
            }
        )
    )
    plan_store = execution_plan_store or DbExecutionPlanStore(session_factory)
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
    return PersistedOmnigentExecutionPlan(
        envelope=persisted,
        binding=binding,
        artifact_refs=(
            policy_artifact_ref,
            effective_launch_ref,
            profile_snapshot_ref,
            support_evidence_ref,
            *skill_content_refs,
            skill_ref,
            plan_artifact_ref,
        ),
        resolved_skillset_ref=skill_ref,
        resolved_skillset_digest=skill_digest,
    )


__all__ = [
    "PersistedOmnigentExecutionPlan",
    "compile_and_persist_execution_plan",
    "persist_json_artifact",
    "selected_skill_names",
    "load_protected_execution_support_evidence",
]
