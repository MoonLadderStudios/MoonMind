"""Seed lineage selects exact prior authorities, not unrelated bindings."""

from types import SimpleNamespace as NS

import pytest

from moonmind.omnigent.runtime_bindings import (
    RuntimeBindingState,
    codex_seed_predecessor,
    stable_binding_id,
)

PLAN = "omnigent-execution-plan:sha256:" + "a" * 64


def request(epoch=2, lineage=None):
    step = NS(
        workflow_id="workflow",
        run_id="run",
        logical_step_id="step",
        execution_ordinal=1,
        step_execution_id="workflow:run:step:execution:1",
    )
    return NS(
        step_execution=step,
        idempotency_key="key",
        admitted_provider_capacity=NS(admission_epoch=epoch),
        parameters={} if lineage is None else {"_codexSeedAdmissionLineage": lineage},
    )


def evidence(epoch=1):
    return {
        "executionPlanRef": PLAN,
        "idempotencyKey": "key",
        "admissionEpoch": epoch,
        "runtimeBindingRef": stable_binding_id(
            execution_plan_ref=PLAN, idempotency_key="key", admission_epoch=epoch
        ),
    }


@pytest.mark.asyncio
async def test_no_predecessor_on_first_admission():
    assert (
        await codex_seed_predecessor(
            request=request(1),
            plan=NS(planRef=PLAN),
            binding=NS(bindingId="current"),
            store=None,
            artifacts=None,
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, "live", "other_step", "forged_ref"])
async def test_exact_cleaned_admission_predecessor_only(invalid):
    lineage = evidence()
    record = NS(
        bindingId=lineage["runtimeBindingRef"],
        executionPlanRef=PLAN,
        state=RuntimeBindingState.cleaned,
        phaseResults={
            "runtimeContextAuthority": {
                "workflowId": "workflow",
                "runId": "run",
                "stepExecutionId": "workflow:run:step:execution:1",
            },
            "codexFileSeeds:model": {"artifactRef": "artifact:art_seed"},
        },
        attestationRefs={},
    )
    if invalid == "live":
        record.state = RuntimeBindingState.host_ready
    if invalid == "other_step":
        record.phaseResults["runtimeContextAuthority"]["stepExecutionId"] = "other"
    if invalid == "forged_ref":
        lineage["runtimeBindingRef"] = "other"

    class Store:
        async def get(self, reference):
            assert reference == record.bindingId
            return record

    if invalid:
        with pytest.raises(ValueError):
            await codex_seed_predecessor(
                request=request(lineage=[lineage]),
                plan=NS(planRef=PLAN),
                binding=NS(bindingId="current"),
                store=Store(),
                artifacts=None,
            )
    else:
        assert (
            await codex_seed_predecessor(
                request=request(lineage=[lineage]),
                plan=NS(planRef=PLAN),
                binding=NS(bindingId="current"),
                store=Store(),
                artifacts=None,
            )
            is record
        )


@pytest.mark.asyncio
async def test_readmission_cannot_silently_recapture_without_lineage():
    with pytest.raises(ValueError, match="lost its predecessor"):
        await codex_seed_predecessor(request=request(2), plan=NS(planRef=PLAN),
            binding=NS(bindingId="current"), store=None, artifacts=None)


def test_checkpoint_dependencies_retain_exact_seed_bytes():
    import json
    from moonmind.schemas.saved_work_models import saved_work_artifact_dependencies
    receipt = {"artifactRef": "artifact:art_seed", "contentDigest": "sha256:" + "c" * 64}
    entries = saved_work_artifact_dependencies("step_execution_checkpoint", json.dumps({
        "codexFileSeedReceipts": {"primary-model": receipt},
    }).encode())
    assert entries == [{"artifact_id": "art_seed", "sha256": "c" * 64,
        "role": "checkpoint.codex_file_seed.primary-model", "required": True}]


def test_checkpoint_projection_requires_exact_runtime_owner():
    from moonmind.omnigent.runtime_bindings import codex_seed_checkpoint_evidence
    step = request().step_execution
    binding = NS(bindingId="binding", revision=3, fencingGeneration=1,
        executionPlanRef=PLAN, phaseResults={
            "runtimeContextAuthority": {"workflowId": step.workflow_id,
                "runId": step.run_id, "stepExecutionId": step.step_execution_id},
            "codexFileSeeds:primary-model": {"artifactRef": "artifact:art_seed"}})
    receipts, source = codex_seed_checkpoint_evidence(binding, plan_ref=PLAN, step=step)
    assert receipts == {"primary-model": binding.phaseResults["codexFileSeeds:primary-model"]}
    assert source["runtimeBindingRevision"] == 3
    binding.phaseResults["runtimeContextAuthority"]["runId"] = "other"
    with pytest.raises(ValueError, match="exact execution authority"):
        codex_seed_checkpoint_evidence(binding, plan_ref=PLAN, step=step)


@pytest.mark.asyncio
async def test_intervening_cleaned_no_seed_admission_does_not_drop_first_receipt():
    first, empty = evidence(1), evidence(2)
    accepted = NS(executionPlanRef=PLAN, state=RuntimeBindingState.cleaned,
        phaseResults={"runtimeContextAuthority": {"workflowId": "workflow", "runId": "run",
            "stepExecutionId": request().step_execution.step_execution_id},
            "codexFileSeeds:primary-model": {"artifactRef": "artifact:art_seed"}})
    class Store:
        async def get(self, ref):
            if ref == empty["runtimeBindingRef"]:
                return NS(executionPlanRef=PLAN, state=RuntimeBindingState.cleaned, phaseResults={})
            assert ref == first["runtimeBindingRef"]
            return accepted
    assert await codex_seed_predecessor(request=request(3, [first, empty]),
        plan=NS(planRef=PLAN), binding=NS(bindingId="current"), store=Store(), artifacts=None) is accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded_fence", [1, 2, 0, 3, True, None])
async def test_cold_seed_receipt_survives_cleanup_fence_advance(monkeypatch, recorded_fence):
    """Reconciliation fences writers, not previously accepted immutable seed bytes."""
    from moonmind.schemas.temporal_models import StepExecutionCheckpointModel
    from moonmind.omnigent.harness_platform import execution_plan

    req = request(1)
    old_step = NS(**vars(req.step_execution))
    req.step_execution.execution_ordinal = 2
    req.parameters = {"metadata": {"moonmind": {
        "sharedStepCheckpointRef": "artifact:art_checkpoint",
        "sharedCheckpointRequired": True,
    }}}
    receipts = {"primary-model": {"artifactRef": "artifact:art_seed"}}
    source = {"executionPlanRef": PLAN, "runtimeBindingRef": "old-binding",
              "runtimeBindingRevision": 3, "runtimeBindingFencingGeneration": recorded_fence}
    authority = NS(taskInputSnapshotRef="artifact:art_input", taskInputSnapshotDigest="digest")
    plan = NS(planRef=PLAN, payload=NS(authority=authority, providerConfigurationSnapshot={}))
    checkpoint = NS(boundary="after_execution", source=old_step,
                    task_input_snapshot_ref="artifact:art_input", plan_ref="artifact:art_plan",
                    plan_digest="sha256:" + "a" * 64,
                    codex_file_seed_receipts=receipts, codex_file_seed_source=source)
    owner = NS(bindingId="old-binding", revision=5, fencingGeneration=2,
               executionPlanRef=PLAN, state=RuntimeBindingState.cleaned,
               phaseResults={"saved": {"checkpointRef": "artifact:art_checkpoint"},
                   "runtimeContextAuthority": {"workflowId": old_step.workflow_id,
                       "runId": old_step.run_id, "stepExecutionId": old_step.step_execution_id},
                   "codexFileSeeds:primary-model": receipts["primary-model"]})
    class Store:
        async def get(self, reference):
            assert reference == owner.bindingId
            return owner
    class Artifacts:
        async def read_codex_seed_checkpoint(self, reference, *, request):
            assert reference == "artifact:art_checkpoint"
            return b"checkpoint", b"plan"
    monkeypatch.setattr(StepExecutionCheckpointModel, "model_validate_json", lambda _body: checkpoint)
    monkeypatch.setattr(execution_plan.OmnigentExecutionPlanEnvelope, "model_validate_json", lambda _body: plan)
    monkeypatch.setattr(execution_plan, "verify_execution_plan_envelope", lambda envelope: envelope)
    async def resolve():
        return await codex_seed_predecessor(request=req, plan=plan,
            binding=NS(bindingId="new-binding"), store=Store(), artifacts=Artifacts())
    if type(recorded_fence) is int and 1 <= recorded_fence <= 2:
        restored = await resolve()
        assert restored.phaseResults["codexFileSeeds:primary-model"] == receipts["primary-model"]
    else:
        with pytest.raises(ValueError, match="durable source"):
            await resolve()
