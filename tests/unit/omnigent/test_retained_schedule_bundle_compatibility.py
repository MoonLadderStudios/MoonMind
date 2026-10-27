"""Retained plan-less schedules admit their selected immutable bundle source.

Real profile selection owner persists schedule-scoped usage with a definition ID
that is distinct from the occurrence workflow. The actual Run adapter projects
that retained snapshot into a plan-less request. Actual DB/blob/catalog/gateway
and planner source verification run, then the test stops before provider/host
side effects. This does not exercise Temporal scheduling or schedule conversion.
The source verifier and artifact ACL run unchanged before the stop marker.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
    ProviderCredentialSource,
    ProviderProfileAuthState,
    RuntimeMaterializationMode,
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
)
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.catalog_service import (
    DbHarnessCatalogRepository,
    HarnessCatalogSyncResult,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.planning_service import (
    OmnigentExecutionPlanningService,
    _digest,
    _ref,
)
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanUsageStore
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test


class SourceVerified(Exception):
    """Stop marker after exact real source/artifact verification succeeds."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authority",
    ("selected", "missing_step", "wrong_digest", "restricted", "quarantined"),
)
async def test_retained_schedule_bundle_uses_selected_source_authority(
    tmp_path, monkeypatch, authority
):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/recorded-selection.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # Upload ownership is ordinary HTTP operator ownership, not gateway ownership.
        body = b"immutable uploaded Omnigent bundle\n"
        async with sessions() as session:
            artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
            artifact, _ = await artifacts.create(
                principal="operator",
                content_type="application/octet-stream",
                size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
                redaction_level=TemporalArtifactRedactionLevel.NONE,
                metadata_json={"artifact_class": "omnigent.agent_bundle"},
            )
            artifact = await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal="operator",
                payload=body,
                content_type="application/octet-stream",
            )
            bundle_ref = "artifact:" + artifact.artifact_id
            assert artifact.created_by_principal == "operator"
            assert await artifacts._repository.list_links(artifact.artifact_id) == []
            if authority == "restricted":
                artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            elif authority == "quarantined":
                artifact.metadata_json = {**artifact.metadata_json, "quarantine": True}
            await session.commit()

        catalog = create_catalog_snapshot(
            endpointRef="default",
            omnigentVersion="1.0.0",
            omnigentBuildDigest="sha256:" + "b" * 64,
            sourceDigest="sha256:" + "c" * 64,
            observedAt=datetime.now(UTC),
            harnesses=[
                {
                    "id": "opencode-native",
                    "label": "OpenCode",
                    "aliases": [],
                    "implementation": {
                        "sourceKind": "core",
                        "package": "omnigent",
                        "version": "1.0.0",
                        "digest": "sha256:" + "a" * 64,
                    },
                    "capabilities": {
                        "integrationMode": "native-server",
                        "authModel": "own-auth",
                    },
                }
            ],
        )
        harness = catalog.harnesses[0]
        trust = classify_harness_trust(
            harnessId=harness.id,
            implementation=harness.implementation,
            trustState=TrustState.core_trusted,
        )
        catalogs = DbHarnessCatalogRepository(sessions)
        await catalogs.persist(HarnessCatalogSyncResult(catalog, (trust,), {}))
        imported = {"id": "recorded-import", "version": "7", "ready": True}
        receipt = {
            "bundleArtifactRef": bundle_ref,
            "bundleDigest": "sha256:" + hashlib.sha256(body).hexdigest(),
            "endpointRef": "default",
            "importedAgentId": imported["id"],
            "importedAgentVersion": imported["version"],
            "importedContentDigest": _digest(imported),
        }
        profile = OmnigentAgentProfileV2.model_validate(
            {
                "endpointRef": "default",
                "source": {
                    "kind": "bundle",
                    **{k: v for k, v in receipt.items() if k != "endpointRef"},
                    "importReceiptRef": _ref("omnigent-agent-import", receipt),
                },
                "allowedLaunchPolicyRefs": ["omnigent-on-demand@1"],
                "harness": {
                    "id": harness.id,
                    "catalogRef": catalog.catalogRef,
                    "implementationRef": harness.implementation.implementation_ref(),
                },
            }
        )
        document = profile.model_dump(mode="json", by_alias=True)
        profile_digest = _digest(document)
        workflow_id = "mm:recorded-bundle-selection"
        async with sessions() as session:
            session.add(
                OmnigentAgentProfile(
                    profile_id="recorded-bundle",
                    display_name="Recorded bundle",
                    state="active",
                    active_version=1,
                )
            )
            session.add(
                OmnigentAgentProfileVersion(
                    profile_id="recorded-bundle",
                    version=1,
                    digest=profile_digest,
                    document=document,
                    validation_result={"ready": True},
                    upstream_snapshot=imported,
                    rollout_metadata={
                        "bundleImport": {
                            "status": "succeeded",
                            "upstreamAgent": imported,
                        }
                    },
                )
            )
            from moonmind.provider_profiles.isolation_policy import (
                derive_isolation_policy,
            )

            isolation = derive_isolation_policy(
                runtime_id="opencode",
                provider_id="opencode",
                authentication_method="none",
                credential_source="none",
                runtime_materialization_mode="composite",
            )
            assert isolation is not None
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="recorded-builtin-provider",
                    runtime_id="opencode",
                    provider_id="opencode",
                    enabled=True,
                    auth_state=ProviderProfileAuthState.CONNECTED,
                    credential_source=ProviderCredentialSource.NONE,
                    runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                    max_parallel_runs=1,
                    cooldown_after_429_seconds=900,
                    secret_refs={},
                    clear_env_keys=list(isolation.keys),
                )
            )
            session.add(
                OmnigentUpstreamAgentProjection(
                    projection_id="recorded-import-projection",
                    endpoint_ref="default",
                    bridge_mode="proxy",
                    upstream_id=imported["id"],
                    upstream_version=imported["version"],
                    metadata_snapshot=imported,
                    available=True,
                    compatible=True,
                    last_attempt_at=datetime.now(UTC),
                    last_successful_sync_at=datetime.now(UTC),
                )
            )
            await session.flush()
            from api_service.services.omnigent_agent_profile_selection import (
                compile_agent_profile_snapshot_parameters,
                resolve_agent_profile_snapshot,
            )

            snapshot = await resolve_agent_profile_snapshot(
                session,
                selection={
                    "profileId": "recorded-bundle",
                    "version": 1,
                    "digest": profile_digest,
                    "providerProfileRef": "recorded-builtin-provider",
                },
                consumer_type="schedule",
                consumer_id="retained-schedule-definition",
                user=None,
            )
            # A retained occurrence has its canonical snapshot but selection
            # usage remains owned by the schedule definition, not this workflow.
            session.add(
                TemporalExecutionCanonicalRecord(
                    workflow_id=workflow_id,
                    run_id="recorded-run",
                    namespace="default",
                    workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                    entry="api",
                    parameters=compile_agent_profile_snapshot_parameters(
                        {}, snapshot=snapshot
                    ),
                )
            )
            await session.commit()
        # Fresh session proves the real selection owner persisted the exact row.
        from sqlalchemy import select

        async with sessions() as session:
            usage = await session.scalar(
                select(OmnigentAgentProfileUsage).where(
                    OmnigentAgentProfileUsage.consumer_type == "schedule",
                    OmnigentAgentProfileUsage.consumer_id
                    == "retained-schedule-definition",
                )
            )
            assert usage is not None
            assert usage.profile_id == "recorded-bundle"
            assert usage.digest == profile_digest
            assert usage.effective_snapshot == snapshot
            assert snapshot["document"]["source"]["bundleArtifactRef"] == bundle_ref
        authored_parameters = compile_agent_profile_snapshot_parameters(
            {}, snapshot=snapshot
        )
        # The real run workflow projects snapshot authority into the runtime block;
        # the whole authoring snapshot is deliberately not an AgentExecutionRequest parameter.
        from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

        owner_workflow = MoonMindRunWorkflow()
        monkeypatch.setattr(
            owner_workflow, "_workflow_patch_enabled", lambda _patch: True
        )
        runtime_omnigent = (
            owner_workflow._compile_agent_profile_snapshot_omnigent_selection(
                authored_parameters["omnigent"],
                snapshot=snapshot,
                execution_profile_ref=snapshot["providerProfileRef"],
                path="workflow.omnigent",
            )
        )
        admitted_parameters = {"omnigent": runtime_omnigent}
        gateway = TemporalOmnigentArtifactGateway(sessions)
        planner = OmnigentExecutionPlanningService(
            session_factory=sessions,
            catalog_repository=catalogs,
            plan_usage_store=DbExecutionPlanUsageStore(sessions),
            skill_resolver=None,
            artifact_gateway=gateway,
        )
        real_source_verifier = planner._verify_agent_source

        async def stop_after_real_source_verification(*args, **kwargs):
            await real_source_verifier(*args, **kwargs)
            raise SourceVerified(
                "real durable uploaded-bundle digest/import/projection verification passed"
            )

        monkeypatch.setattr(
            planner, "_verify_agent_source", stop_after_real_source_verification
        )
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId=workflow_id,
            executionProfileRef=snapshot["providerProfileRef"],
            idempotencyKey=workflow_id + ":step:1",
            stepExecution={
                "workflowId": workflow_id,
                "runId": "recorded-run",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
                "stepExecutionId": workflow_id + ":recorded-run:implement:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
            parameters=admitted_parameters,
        )
        if authority == "missing_step":
            request = request.model_copy(update={"step_execution": None})
        elif authority == "wrong_digest":
            async with sessions() as session:
                version = await session.scalar(select(OmnigentAgentProfileVersion))
                version.digest = "sha256:" + "f" * 64
                await session.commit()
        if authority == "selected":
            with pytest.raises(SourceVerified):
                await planner.plan(request)
            async with sessions() as session:
                artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
                retained, restored = await artifacts.read(
                    artifact_id=artifact.artifact_id,
                    access=access_for_test(
                        "service:omnigent-generic-host",
                        admitted_principal=f"workflow:{workflow_id}",
                    ),
                )
                assert restored == body
                assert retained.created_by_principal == "operator"
        else:
            with pytest.raises(HarnessPlatformError) as failure:
                await planner.plan(request)
            assert failure.value.code == (
                HarnessPlatformFailure.OMNIGENT_AGENT_PROFILE_INVALID
                if authority == "wrong_digest"
                else HarnessPlatformFailure.OMNIGENT_AGENT_SOURCE_UNAVAILABLE
            )
            if authority in {"missing_step", "wrong_digest"}:
                async with sessions() as session:
                    assert await TemporalArtifactRepository(session).list_links(
                        artifact.artifact_id
                    ) == []
    finally:
        await engine.dispose()
