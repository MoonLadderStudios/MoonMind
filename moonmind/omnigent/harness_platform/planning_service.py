"""Production pre-session planning for generic Omnigent executions."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from moonmind.omnigent.bridge_artifacts import OmnigentArtifactGateway
from moonmind.omnigent.harness_platform.agent_profile import (
    OmnigentAgentProfileV2,
    validate_agent_profile,
)
from moonmind.omnigent.harness_platform.catalog import (
    HarnessRecord,
    HarnessTrustRecord,
    assert_catalog_refresh_attests,
)
from moonmind.omnigent.harness_platform.catalog_service import (
    OmnigentHarnessCatalogRepository,
)
from moonmind.omnigent.harness_platform.credential_bindings import (
    CredentialBindingSet,
    create_binding_set,
    parse_binding_set_ref,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    OmnigentExecutionPlanEnvelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.host_classes import (
    HostClass,
    OmnigentHostClassSelector,
    get_launch_policy,
    launch_policy_from_effective_launch,
)
from moonmind.omnigent.harness_platform.materializers import (
    get_materializer,
    materializer_ref_for_provider,
)
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.stores import (
    ExecutionPlanUsageIdentity,
    OmnigentExecutionPlanUsageStore,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.executions.model_resolver import resolve_model_effort


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _ref(prefix: str, value: Any) -> str:
    return f"{prefix}:sha256:{_digest(value).split(':', 1)[1]}"


class PlanningSkillResolver(Protocol):
    async def resolve(
        self,
        *,
        request: AgentExecutionRequest,
        profile: OmnigentAgentProfileV2,
    ) -> ResolvedSkillSet:
        raise NotImplementedError


class ArtifactPlanningSkillResolver:
    """Verify an existing Skill manifest or persist an exact empty snapshot."""

    def __init__(self, artifact_gateway: OmnigentArtifactGateway) -> None:
        self._artifacts = artifact_gateway

    async def resolve(
        self,
        *,
        request: AgentExecutionRequest,
        profile: OmnigentAgentProfileV2,
    ) -> ResolvedSkillSet:
        existing_ref = str(request.resolved_skillset_ref or "").strip()
        if existing_ref:
            try:
                body = await self._artifacts.read_bytes(existing_ref)
                manifest = json.loads(body)
            except Exception as exc:
                raise HarnessPlatformError(
                    "the selected resolved Skill snapshot cannot be loaded",
                    code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
                ) from exc
            canonical = json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
            delivery = _ref(
                "skill-delivery",
                {"manifestRef": existing_ref, "digest": digest, "runtime": "omnigent"},
            )
            return ResolvedSkillSet.model_validate(
                {
                    "resolvedSkillSetRef": existing_ref,
                    "resolvedSkillSetDigest": digest,
                    "skillDeliveryRef": delivery,
                }
            )
        if profile.skills:
            raise HarnessPlatformError(
                "Agent Profile Skill intent was not resolved before planning",
                code=HarnessPlatformFailure.OMNIGENT_SKILL_SNAPSHOT_UNAVAILABLE,
            )
        empty_identity = _digest(
            {
                "profileSnapshotRef": profile.snapshot_ref(),
                "request": request.idempotency_key,
                "skills": [],
            }
        )
        empty = {
            "snapshot_id": f"omnigent-empty-{empty_identity.removeprefix('sha256:')[:24]}",
            "deployment_id": "omnigent-planning",
            "resolved_at": datetime.now(UTC).isoformat(),
            "skills": [],
            "manifest_ref": None,
            "source_trace": {
                "source": "agent-profile",
                "profileSnapshotRef": profile.snapshot_ref(),
            },
            "resolution_inputs": {"skillIntent": []},
            "policy_summary": {"empty": True},
        }
        artifact_ref = await self._artifacts.write_json(
            request=request,
            name="resolved-skills-empty.json",
            payload=empty,
            link_type="input.skill_snapshot",
        )
        digest = _digest(empty)
        return ResolvedSkillSet.model_validate(
            {
                "resolvedSkillSetRef": artifact_ref,
                "resolvedSkillSetDigest": digest,
                "skillDeliveryRef": _ref(
                    "skill-delivery",
                    {
                        "manifestRef": artifact_ref,
                        "digest": digest,
                        "runtime": "omnigent",
                    },
                ),
            }
        )


@dataclass(frozen=True)
class AgentProfileSelection:
    profile_id: str
    version: int
    digest: str

    @classmethod
    def from_request(cls, request: AgentExecutionRequest) -> "AgentProfileSelection":
        omnigent = request.parameters.get("omnigent")
        if not isinstance(omnigent, Mapping):
            raise HarnessPlatformError(
                "generic Omnigent execution requires parameters.omnigent.agentProfileRef",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
            )
        if "agentProfile" in omnigent:
            raise HarnessPlatformError(
                "inline Agent Profile documents are not accepted for workflow execution",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
            )
        selection = omnigent.get("agentProfileRef")
        if not isinstance(selection, Mapping):
            raise HarnessPlatformError(
                "generic Omnigent execution requires an immutable Agent Profile ref",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
            )
        try:
            return cls(
                profile_id=str(selection["profileId"]).strip(),
                version=int(selection["version"]),
                digest=str(selection["digest"]).strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HarnessPlatformError(
                "Agent Profile ref requires profileId, version, and digest",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
            ) from exc


class OmnigentExecutionPlanningService:
    """Resolve every immutable input and bind one plan before side effects."""

    def __init__(
        self,
        *,
        session_factory: Any,
        catalog_repository: OmnigentHarnessCatalogRepository,
        plan_usage_store: OmnigentExecutionPlanUsageStore,
        skill_resolver: PlanningSkillResolver,
        artifact_gateway: OmnigentArtifactGateway,
        host_class_selector: OmnigentHostClassSelector | None = None,
        deployment_default_model: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._catalogs = catalog_repository
        self._usages = plan_usage_store
        self._skills = skill_resolver
        self._artifacts = artifact_gateway
        self._host_classes = host_class_selector or OmnigentHostClassSelector()
        self._deployment_default_model = (
            deployment_default_model
            or os.getenv("MOONMIND_OMNIGENT_DEFAULT_MODEL", "").strip()
        )

    async def plan(
        self, request: AgentExecutionRequest
    ) -> OmnigentExecutionPlanEnvelope:
        selection = AgentProfileSelection.from_request(request)
        workflow_id = (
            request.step_execution.workflow_id
            if request.step_execution is not None
            else request.correlation_id
        )
        step_execution_id = (
            request.step_execution.step_execution_id
            if request.step_execution is not None
            else request.correlation_id
        )
        request_payload = request.model_dump(
            by_alias=True, mode="json", exclude_none=True
        )

        async def compile_once() -> OmnigentExecutionPlanEnvelope:
            return await self._compile(request, selection)

        return await self._usages.load_or_bind(
            identity=ExecutionPlanUsageIdentity(
                workflow_id=workflow_id,
                step_execution_id=step_execution_id,
                idempotency_key=request.idempotency_key,
            ),
            request_payload=request_payload,
            compile_fn=compile_once,
        )

    async def _compile(
        self,
        request: AgentExecutionRequest,
        selection: AgentProfileSelection,
    ) -> OmnigentExecutionPlanEnvelope:
        from api_service.db.models import (
            ManagedAgentProviderProfile,
            OmnigentAgentProfile,
            OmnigentAgentProfileVersion,
            OmnigentCredentialBindingSetRecord,
            OmnigentUpstreamAgentProjection,
        )

        async with self._session_factory() as session:
            profile_row = await session.get(OmnigentAgentProfile, selection.profile_id)
            version_row = (
                await session.execute(
                    select(OmnigentAgentProfileVersion).where(
                        OmnigentAgentProfileVersion.profile_id == selection.profile_id,
                        OmnigentAgentProfileVersion.version == selection.version,
                    )
                )
            ).scalar_one_or_none()
            if profile_row is None or version_row is None:
                raise HarnessPlatformError(
                    "selected Agent Profile version does not exist",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
                )
            document_digest = _digest(version_row.document)
            if (
                version_row.digest != selection.digest
                or document_digest != selection.digest
            ):
                raise HarnessPlatformError(
                    "selected Agent Profile digest does not match its immutable version",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
                )
            if (
                profile_row.state != "active"
                or profile_row.active_version != selection.version
            ):
                raise HarnessPlatformError(
                    "selected Agent Profile version is not active",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
                )
            self._require_durable_legacy_authority(version_row.document)
            profile = validate_agent_profile(dict(version_row.document))

            catalog_result = await self._catalogs.load(profile.harness.catalogRef)
            if catalog_result is None:
                raise HarnessPlatformError(
                    "selected Agent Profile catalog snapshot is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
                )
            harness = next(
                (
                    item
                    for item in catalog_result.snapshot.harnesses
                    if item.id == profile.harness.id
                ),
                None,
            )
            if harness is None:
                raise HarnessPlatformError(
                    f"harness {profile.harness.id} is absent from the selected catalog",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNKNOWN,
                )
            latest_catalog = await self._catalogs.latest(profile.endpointRef)
            if latest_catalog is None:
                raise HarnessPlatformError(
                    "fresh harness catalog observation is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
                )
            assert_catalog_refresh_attests(
                authority=catalog_result.snapshot,
                observation=latest_catalog.snapshot,
                harness_id=profile.harness.id,
                implementation_ref=profile.harness.implementationRef,
            )
            trust = self._trust_record(latest_catalog.trust_records, harness)

            await self._verify_agent_source(
                session, profile, OmnigentUpstreamAgentProjection
            )
            provider_profile = await session.get(
                ManagedAgentProviderProfile,
                str(request.execution_profile_ref or ""),
            )
            if provider_profile is None:
                raise HarnessPlatformError(
                    "selected Provider Profile does not exist",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )
            self._verify_provider_profile(profile, provider_profile)
            binding_set = await self._resolve_binding_set(
                session,
                request=request,
                profile=profile,
                provider_profile=provider_profile,
                record_type=OmnigentCredentialBindingSetRecord,
            )
            # An inferred binding-set version is itself durable product
            # authority and must outlive this planning transaction even when
            # plan persistence happens through a separate repository session.
            await session.commit()

            model, effort, route_ref = self._resolve_model(
                request, profile, provider_profile
            )
            skills = await self._skills.resolve(request=request, profile=profile)
            policy_ref = self._launch_policy_ref(request, profile)
            from api_service.services.omnigent_policies import OmnigentPolicyService
            from moonmind.omnigent.profile_bound_execution import (
                _compile_persisted_effective_launch,
            )

            policy_service = OmnigentPolicyService(session)
            policy_snapshot = await policy_service.resolve_runtime_snapshot(policy_ref)
            effective_launch = _compile_persisted_effective_launch(
                policy_snapshot,
                provider_profile_id=str(provider_profile.profile_id),
            )
            policy = launch_policy_from_effective_launch(
                effective_launch,
                expected_ref=policy_ref,
            )
            host_architecture = str(
                request.parameters.get("architecture") or "linux/amd64"
            )
            integration_mode = harness.capabilities.integrationMode or "native-server"
            host_class = self._host_classes.select(
                harness=harness,
                omnigent_version=catalog_result.snapshot.omnigentVersion,
                omnigent_build_digest=catalog_result.snapshot.omnigentBuildDigest,
                integration_mode=integration_mode,
                materializer_refs=[
                    item.materializerRef for item in binding_set.bindings.values()
                ],
                architecture=host_architecture,
                requested_host_mode=policy.hostMode,
                requested_host_class_ref=self._requested_host_class_ref(request),
            )
            # Discovery is advisory. The actual host attests the selected model
            # before session creation; a cached observation cannot grant or
            # revoke that authority, including after an image update.
            if str(effective_launch.get("hostImageRef") or "") != (
                host_class.imageRef
            ):
                raise HarnessPlatformError(
                    "effective launch host image conflicts with the selected "
                    "Host Class",
                    code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
                )
            (
                policy_artifact_ref,
                policy_artifact_digest,
                effective_launch_artifact_ref,
                effective_launch_artifact_digest,
            ) = await self._persist_exact_launch_authority(
                request=request,
                policy_snapshot=policy_snapshot,
                effective_launch=effective_launch,
            )
            workspace_payload = {
                "profile": profile.workspace,
                "request": request.workspace_spec,
            }
            capture_payload = dict(profile.capture)
            return compile_execution_plan(
                agent_profile=profile,
                harness_catalog=catalog_result.snapshot,
                freshness_catalog=latest_catalog.snapshot,
                trust_record=trust,
                resolved_skills=skills,
                credential_binding_set=binding_set,
                host_class_ref=host_class.ref,
                host_class=host_class,
                launch_policy_ref=policy.ref,
                launch_policy=policy,
                model_qualified_id=model,
                model_effort=effort,
                model_route_ref=route_ref,
                model_normalized_options={},
                workflow_requirements=[],
                bridge_capabilities={
                    "repository.read": True,
                    "repository.write": True,
                    "session.start": True,
                },
                workspace_intent_ref=_ref("workspace-intent", workspace_payload),
                capture_policy_ref=_ref("capture-policy", capture_payload),
                policy_snapshot_ref=policy_artifact_ref,
                policy_snapshot_digest=policy_artifact_digest,
                effective_launch_snapshot_ref=effective_launch_artifact_ref,
                effective_launch_snapshot_digest=effective_launch_artifact_digest,
                host_image_ref=host_class.imageRef,
                omnigent_host_build_digest=host_class.omnigentBuildDigest,
                host_architecture=host_architecture,
            )

    async def _persist_exact_launch_authority(
        self,
        *,
        request: AgentExecutionRequest,
        policy_snapshot: Mapping[str, Any],
        effective_launch: Mapping[str, Any],
    ) -> tuple[str, str, str, str]:
        """Persist the exact launch inputs with digests over stored bytes."""

        policy_body = _canonical_json_bytes(policy_snapshot)
        effective_launch_body = _canonical_json_bytes(effective_launch)
        policy_ref = await self._artifacts.write_bytes(
            request=request,
            name="launch-policy-snapshot.json",
            payload=policy_body,
            link_type="input.launch_policy_snapshot",
            content_type="application/json",
        )
        effective_launch_ref = await self._artifacts.write_bytes(
            request=request,
            name="effective-launch-snapshot.json",
            payload=effective_launch_body,
            link_type="input.effective_launch_snapshot",
            content_type="application/json",
        )
        return (
            policy_ref,
            "sha256:" + hashlib.sha256(policy_body).hexdigest(),
            effective_launch_ref,
            "sha256:" + hashlib.sha256(effective_launch_body).hexdigest(),
        )

    @staticmethod
    def _trust_record(
        records: tuple[HarnessTrustRecord, ...],
        harness: HarnessRecord,
    ) -> HarnessTrustRecord:
        implementation_ref = harness.implementation.implementation_ref()
        trust = next(
            (item for item in records if item.implementationRef == implementation_ref),
            None,
        )
        if trust is None:
            raise HarnessPlatformError(
                "exact harness implementation has no trust decision",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_UNTRUSTED,
            )
        return trust

    async def _verify_agent_source(
        self, session: Any, profile: OmnigentAgentProfileV2, projection_type: Any
    ) -> None:
        if profile.source.kind != "upstream":
            try:
                bundle = await self._artifacts.read_bytes(
                    profile.source.bundleArtifactRef
                )
            except Exception as exc:
                raise HarnessPlatformError(
                    "bundle-backed Agent source artifact is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
                ) from exc
            observed_bundle_digest = "sha256:" + hashlib.sha256(bundle).hexdigest()
            if observed_bundle_digest != profile.source.bundleDigest:
                raise HarnessPlatformError(
                    "bundle-backed Agent source digest does not match its artifact",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
                )
            receipt_payload = {
                "bundleArtifactRef": profile.source.bundleArtifactRef,
                "bundleDigest": profile.source.bundleDigest,
                "endpointRef": profile.endpointRef,
                "importedAgentId": profile.source.importedAgentId,
                "importedAgentVersion": profile.source.importedAgentVersion,
                "importedContentDigest": profile.source.importedContentDigest,
            }
            if profile.source.importReceiptRef != _ref(
                "omnigent-agent-import", receipt_payload
            ):
                raise HarnessPlatformError(
                    "bundle-backed Agent source import receipt is invalid",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
                )
            projection = (
                await session.execute(
                    select(projection_type).where(
                        projection_type.endpoint_ref == profile.endpointRef,
                        projection_type.upstream_id == profile.source.importedAgentId,
                        projection_type.upstream_version
                        == profile.source.importedAgentVersion,
                    )
                )
            ).scalar_one_or_none()
            if (
                projection is None
                or not projection.available
                or not projection.compatible
                or projection.error
                or _digest(projection.metadata_snapshot)
                != profile.source.importedContentDigest
            ):
                raise HarnessPlatformError(
                    "bundle-backed Agent import projection is unavailable or changed",
                    code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
                )
            return
        projection = (
            await session.execute(
                select(projection_type).where(
                    projection_type.endpoint_ref == profile.endpointRef,
                    projection_type.upstream_id == profile.source.upstreamId,
                    projection_type.upstream_version == profile.source.upstreamVersion,
                )
            )
        ).scalar_one_or_none()
        if (
            projection is None
            or not projection.available
            or not projection.compatible
            or projection.error
        ):
            raise HarnessPlatformError(
                "selected upstream Agent source is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
            )
        if (
            _digest(projection.metadata_snapshot)
            != profile.source.upstreamSnapshotDigest
        ):
            raise HarnessPlatformError(
                "upstream Agent source digest changed after profile commitment",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE,
            )

    @staticmethod
    def _verify_provider_profile(
        profile: OmnigentAgentProfileV2, provider: Any
    ) -> None:
        state = getattr(provider.auth_state, "value", provider.auth_state)
        if not provider.enabled or state != "connected":
            raise HarnessPlatformError(
                "selected Provider Profile is not launch ready",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
            )
        for slot in profile.credentialSlots:
            if (
                slot.acceptedProviderIds
                and provider.provider_id not in slot.acceptedProviderIds
            ):
                raise HarnessPlatformError(
                    f"Provider Profile {provider.profile_id} is incompatible with slot {slot.id}",
                    code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
                )

    async def _resolve_binding_set(
        self,
        session: Any,
        *,
        request: AgentExecutionRequest,
        profile: OmnigentAgentProfileV2,
        provider_profile: Any,
        record_type: Any,
    ) -> CredentialBindingSet:
        omnigent = request.parameters.get("omnigent")
        explicit_ref = (
            str(omnigent.get("credentialBindingSetRef") or "").strip()
            if isinstance(omnigent, Mapping)
            else ""
        )
        if explicit_ref:
            binding_id, version, digest = parse_binding_set_ref(explicit_ref)
            row = await session.get(record_type, (binding_id, version))
            if row is None or row.digest != digest or row.ref != explicit_ref:
                raise HarnessPlatformError(
                    "selected credential binding set is unavailable",
                    code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
                )
            return CredentialBindingSet.model_validate(row.canonical_json)

        materializer_ref = materializer_ref_for_provider(
            provider_profile.runtime_id, provider_profile.provider_id
        )
        get_materializer(materializer_ref)
        required_slots = [
            slot.id for slot in profile.credentialSlots if not slot.optional
        ]
        if len(required_slots) != 1:
            raise HarnessPlatformError(
                "implicit credential binding requires exactly one required slot",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
        binding_identity = hashlib.sha256(
            f"{profile.snapshot_ref()}\0{provider_profile.profile_id}".encode("utf-8")
        ).hexdigest()[:24]
        binding_id = f"implicit-{binding_identity}"
        binding = create_binding_set(
            bindingSetId=binding_id,
            version=1,
            bindings={
                required_slots[0]: {
                    "providerProfileRef": provider_profile.profile_id,
                    "materializerRef": materializer_ref,
                }
            },
        )
        row = await session.get(record_type, (binding.bindingSetId, binding.version))
        if row is None:
            session.add(
                record_type(
                    binding_set_id=binding.bindingSetId,
                    version=binding.version,
                    digest=binding.digest,
                    canonical_json=binding.model_dump(by_alias=True, mode="json"),
                    ref=binding.ref,
                )
            )
            try:
                await session.flush()
            except IntegrityError as exc:
                raise HarnessPlatformError(
                    "credential binding set persistence conflict",
                    code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
                ) from exc
        elif row.digest != binding.digest:
            raise HarnessPlatformError(
                "implicit credential binding set version conflicts with durable authority",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
        return binding

    def _resolve_model(
        self,
        request: AgentExecutionRequest,
        profile: OmnigentAgentProfileV2,
        provider: Any,
    ) -> tuple[str, str | None, str]:
        omnigent = request.parameters.get("omnigent")
        explicit_model = str(request.parameters.get("model") or "").strip()
        if not explicit_model and isinstance(omnigent, Mapping):
            explicit_model = str(omnigent.get("model") or "").strip()
        explicit_effort = (
            omnigent.get("effort") if isinstance(omnigent, Mapping) else None
        )
        try:
            resolved = resolve_model_effort(
                runtime_id=provider.runtime_id,
                profile=provider,
                requested_model=explicit_model or None,
                requested_effort=(
                    str(explicit_effort).strip()
                    if explicit_effort is not None
                    else request.parameters.get("effort")
                ),
            )
        except ValueError as exc:
            raise HarnessPlatformError(
                "Provider Profile model authority is not launch ready",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            ) from exc
        model = str(resolved.model or self._deployment_default_model).strip()
        if not model:
            raise HarnessPlatformError(
                "no model authority is available for generic Omnigent execution",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            )
        qualified = model if "/" in model else f"{provider.provider_id}/{model}"
        if not qualified.startswith(f"{provider.provider_id}/"):
            raise HarnessPlatformError(
                "selected model does not belong to the Provider Profile route",
                code=HarnessPlatformFailure.OMNIGENT_MODEL_UNAVAILABLE,
            )
        effort = str(resolved.effort or "").strip() or None
        return qualified, effort, provider.provider_id

    @staticmethod
    def _require_durable_legacy_authority(document: Mapping[str, Any]) -> None:
        if document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2":
            return
        harness = document.get("harness")
        harness_map = harness if isinstance(harness, Mapping) else {}
        catalog_ref = document.get("harnessCatalogRef") or harness_map.get("catalogRef")
        implementation_ref = document.get(
            "harnessImplementationRef"
        ) or harness_map.get("implementationRef")
        if not catalog_ref or not implementation_ref:
            raise HarnessPlatformError(
                "legacy Agent Profile is missing durable catalog and implementation authority",
                code=HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID,
            )

    @staticmethod
    def _launch_policy_ref(
        request: AgentExecutionRequest,
        profile: OmnigentAgentProfileV2,
    ) -> str:
        omnigent = request.parameters.get("omnigent")
        explicit = (
            str(omnigent.get("launchPolicyRef") or "").strip()
            if isinstance(omnigent, Mapping)
            else ""
        )
        selected = explicit or (
            profile.allowedLaunchPolicyRefs[0]
            if profile.allowedLaunchPolicyRefs
            else ""
        )
        if not selected:
            raise HarnessPlatformError(
                "Agent Profile has no launch policy authority",
                code=HarnessPlatformFailure.OMNIGENT_LAUNCH_POLICY_INCOMPATIBLE,
            )
        return selected

    @staticmethod
    def _requested_host_class_ref(request: AgentExecutionRequest) -> str | None:
        omnigent = request.parameters.get("omnigent")
        if not isinstance(omnigent, Mapping):
            return None
        value = str(omnigent.get("hostClassRef") or "").strip()
        return value or None


class OmnigentPlannedHostResolver:
    """Rehydrate exact Host Class data from the plan's catalog authority."""

    def __init__(
        self,
        *,
        catalog_repository: OmnigentHarnessCatalogRepository,
        host_class_selector: OmnigentHostClassSelector | None = None,
        artifact_gateway: OmnigentArtifactGateway | None = None,
        architecture: str | None = None,
    ) -> None:
        self._catalogs = catalog_repository
        self._selector = host_class_selector or OmnigentHostClassSelector()
        self._artifacts = artifact_gateway
        self._architecture = architecture or os.getenv(
            "MOONMIND_OMNIGENT_HOST_ARCHITECTURE", "linux/amd64"
        )

    async def __call__(self, plan: OmnigentExecutionPlanEnvelope):
        catalog = await self._catalogs.load(plan.payload.harnessCatalogRef)
        if catalog is None:
            raise HarnessPlatformError(
                "execution plan catalog snapshot is unavailable",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
            )
        harness = next(
            (
                item
                for item in catalog.snapshot.harnesses
                if item.id == plan.payload.harnessId
                and item.implementation.implementation_ref()
                == plan.payload.harnessImplementationRef
            ),
            None,
        )
        if harness is None:
            raise HarnessPlatformError(
                "execution plan harness implementation is absent from its catalog",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        launch: Mapping[str, Any] | None = None
        if plan.payload.hostImageRef is not None:
            launch = await self._load_exact_launch(plan)
            policy = launch_policy_from_effective_launch(
                launch,
                expected_ref=plan.payload.launchPolicyRef,
            )
        else:
            policy = get_launch_policy(plan.payload.launchPolicyRef)
        host_class = self._selector.select(
            harness=harness,
            omnigent_version=catalog.snapshot.omnigentVersion,
            omnigent_build_digest=catalog.snapshot.omnigentBuildDigest,
            integration_mode=harness.capabilities.integrationMode or "native-server",
            materializer_refs=[
                item.materializerRef
                for item in plan.payload.credentialBindings.values()
            ],
            architecture=plan.payload.hostArchitecture or self._architecture,
            requested_host_mode=policy.hostMode,
            requested_host_class_ref=plan.payload.hostClassRef,
        )
        if plan.payload.hostImageRef is not None:
            host_class = self._resolve_exact_host(
                plan=plan,
                host_class=host_class,
                launch=launch,
            )
        return host_class, policy

    async def _load_exact_launch(
        self,
        plan: OmnigentExecutionPlanEnvelope,
    ) -> Mapping[str, Any]:
        """Load and validate the plan's immutable launch artifact."""
        if self._artifacts is None:
            raise HarnessPlatformError(
                "exact execution plan launch artifact cannot be resolved",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        raw = await self._artifacts.read_bytes(
            str(plan.payload.effectiveLaunchSnapshotRef)
        )
        if "sha256:" + hashlib.sha256(raw).hexdigest() != (
            plan.payload.effectiveLaunchSnapshotDigest
        ):
            raise HarnessPlatformError(
                "effective launch artifact digest conflicts with the plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        try:
            launch = json.loads(raw)
            if not isinstance(launch, Mapping):
                raise TypeError("effective launch artifact is not an object")
            from moonmind.omnigent.execution_profiles import (
                validate_effective_launch_snapshot,
            )

            validate_effective_launch_snapshot(launch)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HarnessPlatformError(
                "effective launch artifact is invalid",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            ) from exc
        expected = {
            "launchPolicyRef": plan.payload.launchPolicyRef,
            "hostImageRef": plan.payload.hostImageRef,
            "harness": plan.payload.harnessId,
        }
        if any(
            str(launch.get(key) or "") != str(value)
            for key, value in expected.items()
        ):
            raise HarnessPlatformError(
                "effective launch artifact conflicts with the execution plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        return launch

    @staticmethod
    def _resolve_exact_host(
        *,
        plan: OmnigentExecutionPlanEnvelope,
        host_class: HostClass,
        launch: Mapping[str, Any] | None,
    ) -> HostClass:
        """Rehydrate exact Host Class inputs from immutable launch authority."""

        if launch is None:
            raise HarnessPlatformError(
                "exact execution plan launch artifact cannot be resolved",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        architecture = str(plan.payload.hostArchitecture or "").strip()
        normalized_architectures = tuple(
            value if "/" in value else f"linux/{value}"
            for value in (
                str(item).strip() for item in (launch.get("architectures") or [])
            )
            if value
        )
        if architecture not in normalized_architectures:
            raise HarnessPlatformError(
                "effective launch architecture conflicts with the execution plan",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        exact_host = HostClass.model_validate(
            {
                **host_class.model_dump(mode="json", by_alias=True),
                "imageRef": plan.payload.hostImageRef,
                "omnigentBuildDigest": plan.payload.omnigentHostBuildDigest,
                "architectures": [architecture],
                "runtime": {
                    **host_class.runtime,
                    "uid": int(launch.get("runtimeUid") or 1000),
                    "gid": int(launch.get("runtimeGid") or 1000),
                },
                "features": {
                    **host_class.features,
                    "readOnlyRoot": launch.get("readOnlyRoot") is True,
                    "restrictedEgress": bool(launch.get("enforcedEgress")),
                },
            }
        )
        return exact_host


__all__ = [
    "AgentProfileSelection",
    "ArtifactPlanningSkillResolver",
    "OmnigentExecutionPlanningService",
    "OmnigentPlannedHostResolver",
    "PlanningSkillResolver",
]
