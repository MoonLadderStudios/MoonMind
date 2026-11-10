"""Recorded pool settings belong to the admitted step, not the live account."""

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import ProviderProfileSlotLease
from api_service.services import native_pool_execution_plan as service
from moonmind.omnigent.harness_platform.execution_plan import (
    canonical_payload_bytes,
    create_execution_plan_envelope,
    verify_execution_plan_envelope,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.temporal_models import (
    StepExecutionCheckpointModel,
    StepExecutionIdentityModel,
    build_step_execution_id,
)
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload


def resolution(**changes):
    return {
        "resolvedModel": "admitted-model",
        "resolvedEffort": None,
        "modelSource": "requested_tier",
        "effortSource": "tier",
        "effectiveModelTier": 1,
        "requestedModelTier": 1,
        "tierParameterDefaults": {"commitMessage": "Admitted commit"},
        **changes,
    }


def plan(*, tier_resolution=None):
    payload = {
        "endpointRef": "default",
        "agentProfileSnapshotRef": "artifact:art_profile",
        "harnessCatalogRef": "catalog",
        "harnessId": "codex-native",
        "harnessImplementationRef": "implementation",
        "agentSource": {},
        "credentialBindingSetRef": "bindings",
        "credentialBindings": {
            "primary-model": {
                "providerProfileRef": "p1",
                "materializerRef": "codex-oauth-home@1",
            }
        },
        "hostClassRef": "host",
        "launchPolicyRef": "policy",
        "executionRealizerRef": "generic-omnigent-host@1",
        "model": {
            "qualifiedId": "admitted-model",
            "effort": None,
            "modelConfigDigest": "sha256:" + "a" * 64,
            **(
                {"tierResolution": tier_resolution}
                if tier_resolution is not None
                else {}
            ),
        },
        "resolvedSkills": {"resolvedSkillSetRef": "artifact:art_skills"},
        "classAdmissionDecision": {},
        "runtimeValidationRequirements": [],
        "workspaceIntentRef": "workspace",
        "policySnapshotRef": "policy-snapshot",
        "supportCombinationKey": "support",
        "authority": {
            "authoredRequestRef": "artifact:art_input",
            "authoredRequestDigest": "sha256:" + "b" * 64,
            "taskInputSnapshotRef": "artifact:art_input",
            "taskInputSnapshotDigest": "sha256:" + "b" * 64,
            **{
                key: "policy"
                for key in (
                    "repositoryIntentRef",
                    "continuationPolicyRef",
                    "remediationPolicyRef",
                    "checkpointPolicyRef",
                    "publicationPolicyRef",
                    "timingPolicyRef",
                    "failurePolicyRef",
                )
            },
        },
    }
    return create_execution_plan_envelope(payload)


def checkpoint(envelope):
    return StepExecutionCheckpointModel.model_validate(
        build_step_checkpoint_payload(
            identity=StepExecutionIdentityModel(
                workflowId="parent",
                runId="parent-run",
                logicalStepId="work",
                executionOrdinal=1,
            ),
            boundary="after_execution",
            task_input_snapshot_ref="artifact:art_input",
            plan_ref="artifact:art_plan",
            plan_digest="sha256:" + envelope.planRef.rsplit(":", 1)[-1],
            workspace={
                "kind": "worktree_archive",
                "archiveRef": "artifact:art_archive",
                "manifestRef": "artifact:art_manifest",
            },
            created_at=datetime.now(timezone.utc),
        )
    )


def request(profile="p1"):
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        executionProfileRef=profile,
        correlationId="parent",
        idempotencyKey="replacement",
        parameters={
            "runtime": {"modelTier": 1},
            "modelTier": 1,
            "metadata": {
                "moonmind": {
                    "sharedStepCheckpointRef": "artifact:art_checkpoint",
                    "sharedCheckpointRequired": True,
                }
            },
        },
        stepExecution={
            "workflowId": "parent",
            "runId": "parent-run",
            "logicalStepId": "work",
            "executionOrdinal": 2,
            "stepExecutionId": "parent:parent-run:work:execution:2",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )


@pytest_asyncio.fixture
async def retained_grant(empty_sqlite_database):
    engine = create_async_engine(f"sqlite+aiosqlite:///{empty_sqlite_database}")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        envelope = plan()
        source = checkpoint(envelope)
        row = ProviderProfileSlotLease(
            runtime_id="codex_cli",
            profile_id="p1",
            workflow_id="original-child",
            owner_id="original-child",
            lease_id="original-child",
            owner_kind="workflow",
            owner_is_workflow=True,
            purpose="execution_direct",
            lease_state="released",
            step_execution_id=build_step_execution_id(source.source),
            idempotency_key=f"{build_step_execution_id(source.source)}:agent_execute",
            execution_plan_ref=envelope.planRef,
            fencing_generation=7,
            credential_generation=1,
            safe_metadata_json={
                "runId": "original-child-run",
                "preparedPoolPlan": {
                    "requestDigest": "sha256:" + "c" * 64,
                    "binding": {
                        "planRef": envelope.planRef,
                        "planDigest": source.plan_digest,
                        "planArtifactRef": source.plan_ref,
                        "taskInputSnapshotRef": source.task_input_snapshot_ref,
                        "taskInputSnapshotDigest": envelope.payload.authority.taskInputSnapshotDigest,
                    },
                    "resolvedSkillsetRef": "artifact:art_skills",
                    "modelResolution": resolution(),
                    "launchParameters": {"commitMessage": "Admitted commit"},
                },
            },
        )
        session.add(row)
        await session.commit()
        yield session, row, envelope, source
    await engine.dispose()


@pytest.mark.asyncio
async def test_retained_released_grant_supplies_only_its_original_settings(
    retained_grant,
):
    session, row, envelope, source = retained_grant
    before = deepcopy(row.safe_metadata_json)
    recovered = await service._retained_pool_launch(
        session,
        request=request(),
        checkpoint=source,
        plan=envelope,
    )
    assert recovered == (resolution(), {"commitMessage": "Admitted commit"})
    assert row.lease_state == "released" and row.credential_generation == 1
    assert row.safe_metadata_json == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "account",
        "runtime",
        "step",
        "plan",
        "owner",
        "operation",
        "missing",
        "duplicate",
        "plan_artifact",
        "input_digest",
        "skills",
        "null_launch",
        "defaults",
        "missing_null_default",
        "secret",
        "model",
        "effort",
    ],
)
async def test_retained_grant_cannot_supply_foreign_or_incomplete_settings(
    retained_grant, change
):
    session, row, envelope, source = retained_grant
    metadata = deepcopy(row.safe_metadata_json)
    saved = metadata["preparedPoolPlan"]
    if change in {"account", "runtime", "step", "plan", "owner", "operation"}:
        field = {
            "account": "profile_id",
            "runtime": "runtime_id",
            "step": "step_execution_id",
            "plan": "execution_plan_ref",
            "owner": "owner_id",
            "operation": "idempotency_key",
        }[change]
        setattr(row, field, "" if change == "operation" else "foreign")
    elif change == "missing":
        await session.delete(row)
    elif change == "duplicate":
        session.add(
            ProviderProfileSlotLease(
                runtime_id=row.runtime_id,
                profile_id=row.profile_id,
                workflow_id="duplicate",
                owner_id="duplicate",
                lease_id="duplicate",
                step_execution_id=row.step_execution_id,
                execution_plan_ref=row.execution_plan_ref,
            )
        )
    elif change == "plan_artifact":
        saved["binding"]["planArtifactRef"] = "artifact:art_foreign"
    elif change == "input_digest":
        saved["binding"]["taskInputSnapshotDigest"] = "sha256:" + "e" * 64
    elif change == "skills":
        saved["resolvedSkillsetRef"] = "artifact:art_foreign"
    elif change == "null_launch":
        saved["launchParameters"] = None
    elif change == "defaults":
        saved["launchParameters"]["commitMessage"] = "Conflicting default"
    elif change == "missing_null_default":
        saved["modelResolution"]["tierParameterDefaults"] = {"publishBaseBranch": None}
    elif change in {"model", "effort"}:
        saved["modelResolution"][
            "resolvedModel" if change == "model" else "resolvedEffort"
        ] = "foreign"
    else:
        saved["launchParameters"]["commitMessage"] = "ghp_syntheticcredentialnotforuse"
    row.safe_metadata_json = metadata
    await session.commit()
    with pytest.raises(ValueError, match="recorded pool launch|tier metadata"):
        await service._retained_pool_launch(
            session, request=request(), checkpoint=source, plan=envelope
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values", [{}, {"commitMessage": "", "publishBaseBranch": None}]
)
async def test_retained_grant_preserves_empty_and_null_launch_values(
    retained_grant, values
):
    session, row, envelope, source = retained_grant
    metadata = deepcopy(row.safe_metadata_json)
    saved = metadata["preparedPoolPlan"]
    saved["launchParameters"] = values
    saved["modelResolution"] = resolution(tierParameterDefaults={})
    row.safe_metadata_json = metadata
    await session.commit()
    _, restored = await service._retained_pool_launch(
        session,
        request=request(),
        checkpoint=source,
        plan=envelope,
    )
    assert restored == values


@pytest.mark.asyncio
async def test_retained_custom_alias_does_not_require_current_profile_lookup(
    retained_grant,
):
    session, row, envelope, source = retained_grant
    metadata = deepcopy(row.safe_metadata_json)
    metadata["preparedPoolPlan"]["modelResolution"].update(
        resolvedModel="authored-alias",
        modelSource="task_override",
        effectiveModelTier=None,
    )
    row.safe_metadata_json = metadata
    # This supported first-attempt key is recorded by older Run producers.
    row.idempotency_key = "parent:work:parent-run"
    await session.commit()
    frozen, _ = await service._retained_pool_launch(
        session,
        request=request(),
        checkpoint=source,
        plan=envelope,
    )
    assert frozen["resolvedModel"] == "authored-alias"
    assert envelope.payload.modelConfig.qualifiedId == "admitted-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded_resolution", [None, resolution()])
async def test_account_rotation_does_not_reuse_previous_accounts_tier_policy(
    monkeypatch, recorded_resolution
):
    from moonmind.workflows.temporal.runtime.launcher import ManagedRuntimeLauncher
    from tests.unit.workflows.executions.test_model_selection_4636 import profile

    envelope = plan(tier_resolution=recorded_resolution)
    source = checkpoint(envelope)
    artifacts = SimpleNamespace(
        read=AsyncMock(
            side_effect=[
                (None, source.model_dump_json(by_alias=True).encode()),
                (None, envelope.model_dump_json(by_alias=True).encode()),
            ]
        )
    )
    monkeypatch.setattr(
        service.SessionExecutionPlanStore, "load", AsyncMock(return_value=envelope)
    )
    retained = AsyncMock(
        side_effect=AssertionError("another account cannot read source lease settings")
    )
    monkeypatch.setattr(service, "_retained_pool_launch", retained)
    rotated = request("p2")
    assert (
        await service._recorded_step_plan(
            None,
            artifacts,
            None,
            rotated,
            snapshot_ref="artifact:art_input",
            snapshot_digest="sha256:" + "b" * 64,
        )
        is None
    )
    current = profile()
    current.profile_id = "p2"
    current.model_tiers = [
        {
            "model": "current-account-model",
            "parameters": {"commitMessage": "Current account"},
        }
    ]
    ManagedRuntimeLauncher._apply_resolved_tier_policy(
        request=rotated, profile=current, strategy=None
    )
    assert rotated.parameters["model"] == "current-account-model"
    assert rotated.parameters["commitMessage"] == "Current account"
    retained.assert_not_awaited()
    assert artifacts.read.await_count == 2


def test_resolution_is_digest_covered_without_rewriting_older_plans():
    old = plan()
    old_bytes = canonical_payload_bytes(old.payload)
    assert b"tierResolution" not in old_bytes
    assert (
        canonical_payload_bytes(verify_execution_plan_envelope(old).payload)
        == old_bytes
    )
    admitted = plan(tier_resolution=resolution())
    assert admitted.planRef != old.planRef
    changed = plan(tier_resolution=resolution(tierParameterDefaults={}))
    assert changed.planRef != admitted.planRef
    assert b"tierResolution" not in old.model_dump_json(by_alias=True).encode()


@pytest.mark.parametrize("effective_tier", [None, 1])
def test_recorded_selection_preserves_absence_and_explicit_values(effective_tier):
    frozen = resolution(effectiveModelTier=effective_tier, tierParameterDefaults={})
    if effective_tier is None:
        frozen.update(
            requestedModelTier=None,
            modelSource="task_override",
            effortSource="none",
            resolvedModel="authored-alias",
        )
    selected = request()
    selected.parameters.update(
        commitMessage="Explicit commit",
        runtime={"parameters": {"commitMessage": "Explicit commit"}},
    )
    recorded = service._RecordedPoolStep(plan(tier_resolution=frozen), {}, frozen, None)
    service._restore_recorded_pool_selection(selected, recorded)
    assert selected.parameters["model"] == "admitted-model"
    assert (
        selected.parameters["metadata"]["moonmind"]["modelEffortResolution"] == frozen
    )
    assert selected.parameters["commitMessage"] == "Explicit commit"
    assert "publishBaseBranch" not in selected.parameters
    assert ("modelTierResolution" in selected.parameters) == (
        effective_tier is not None
    )


@pytest.mark.parametrize(
    "bad", [None, {}, resolution(tierParameterDefaults={"envOverrides": {}})]
)
def test_recorded_selection_requires_complete_supported_resolution(bad):
    with pytest.raises(ValueError, match="recorded pool launch"):
        service._recorded_pool_resolution(bad)
