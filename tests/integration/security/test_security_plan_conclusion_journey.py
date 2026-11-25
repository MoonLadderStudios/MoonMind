"""A plan run's concluding security.assess over its jobs' retained evidence (#5432).

The plan is retained by the real planning steps (#5431). The plan run's job
step publishes its outcome record and native evidence; here they are stored
exactly as the agent runtime and container-job owner retain them. The
conclusion then runs through the real tool dispatcher, artifact service,
sealing and report publication, with the inputs the preset compiler derives.
"""

from __future__ import annotations

import json

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.security.organisational_evidence import organisational_evidence_refs
from moonmind.security.security_plan import parse_security_plan
from moonmind.security.security_plan_run import JOB_RECORD_LINK, conclusion_step
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security import test_security_plan_run_journey as planning

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
RUN = "security-plan-run-1"


async def put(service, payload, *, link_type, workflow_id=RUN):
    artifact, _ = await service.create(
        principal="operator",
        content_type="application/json",
        link={
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": "run",
            "link_type": link_type,
        },
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal="operator",
        payload=payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
    )
    return artifact.artifact_id


async def retained_plan(service):
    """Plan, identity and its document, retained by the planning run."""
    context = (await planning.planned_context(service))["planningContext"]
    draft_ref = await recurring.put(
        service, planning.harness_response(context), workflow_id=planning.WORKFLOW
    )
    outputs = await planning.invoke(
        service,
        planning.step_inputs(
            "retain", previousOutputs={"securityPlanDraftRef": draft_ref}
        ),
        key="retain",
    )
    raw = await recurring.read(
        service, outputs["securityPlanRef"], principal=f"workflow:{planning.WORKFLOW}"
    )
    return outputs["securityPlanRef"], outputs["securityPlanIdentity"], raw


async def native_evidence(service, plan):
    """What the gitleaks job of the plan retains: native reports and summary."""
    native = {
        mode: await put(service, b"[]", link_type="security.native")
        for mode in ("history", "worktree")
    }
    summary = {
        "schemaVersion": "repository-scanner/v1",
        "scanner": "gitleaks",
        "admission": {
            "scanner": "gitleaks",
            "outputDirectory": "scan",
            "exclusions": plan["subject"]["scope"].get("exclusions", []),
        },
        "execution": {
            "inputDigest": "0" * 64,
            "assets": ["shop-app"],
            "version": "8.21.2",
            "exits": {"history": 0, "worktree": 0},
            "states": {"history": "succeeded", "worktree": "succeeded"},
        },
        "results": {
            mode: {
                "execution": "succeeded",
                "coverage": "complete",
                "findings": [],
                "examinedAssets": ["shop-app"],
            }
            for mode in native
        },
        "nativeRefs": native,
        "coverage": "complete",
        "limitations": [],
    }
    return await put(service, summary, link_type="output.summary")


async def job_record(
    service, identity, state, reports, *, reason="Recorded.", review=None
):
    return await put(
        service,
        {
            "schemaVersion": "security-plan-job/v1",
            "planIdentity": identity,
            "jobId": "secrets-scan",
            "state": state,
            "reason": reason,
            "containerJobIds": ["cj-1"],
            "reportRefs": reports,
            **({"reviewRef": review} if review else {}),
        },
        link_type=JOB_RECORD_LINK,
    )


async def admitted(service, plan_ref, identity, raw, **changes):
    """Conclusion inputs as expanded, with the reads launch admission delegates."""
    plan = parse_security_plan(json.dumps(raw))
    step = conclusion_step(plan, plan_ref=plan_ref, identity=identity, excluded=[])
    refs = organisational_evidence_refs({"steps": [step]})
    assert plan_ref in refs
    await service.delegate_read_access(
        artifact_ids=refs,
        workflow_id=RUN,
        access=recurring.access_for_test("operator"),
    )
    return {**step["tool"]["inputs"], **changes}


async def report_text(service, outputs):
    _, text = await service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test(f"workflow:{RUN}"),
    )
    return text.decode()


async def test_plan_run_concludes_once_over_the_jobs_retained_evidence(environment):
    service = environment.service
    plan_ref, identity, raw = await retained_plan(service)
    summary_ref = await native_evidence(service, raw)
    # A restarted job step publishes a newer record; only the latest counts.
    await job_record(service, identity, "failed", [], reason="Interrupted.")
    review_ref = await put(
        service, {"schemaVersion": "security-review/v1"}, link_type="output.review"
    )
    record_ref = await job_record(
        service, identity, "completed", [summary_ref], review=review_ref
    )

    outputs = await recurring.invoke(
        service,
        await admitted(service, plan_ref, identity, raw),
        workflow_id=RUN,
        key="conclude",
    )
    record = await recurring.read(
        service, outputs["securityPlanRunRef"], principal=f"workflow:{RUN}"
    )
    assert record["schemaVersion"] == "security-plan-run/v1"
    assert record["planIdentity"] == identity
    (job,) = record["jobs"]
    assert job["state"] == "completed" and job["reportRefs"] == [summary_ref]
    assert outputs["planAgainstActual"]["jobs"] == {"completed": 1}
    # The job's native evidence reached the existing retained-report owner.
    assert [row["summaryRef"] for row in outputs["scannerSummaries"]] == [summary_ref]
    required = await recurring.required(service, outputs["securityPlanRunRef"])
    assert {plan_ref, record_ref} <= required
    # The job's interpretation is cited, but never holds native retention.
    assert job["reviewRef"] == review_ref and review_ref not in required
    states = {row["requirementId"]: row["state"] for row in record["requirements"]}
    assert "evidence_collected" in states.values()
    assert "met" not in states.values()
    text = await report_text(service, outputs)
    assert "## Plan against actual" in text
    assert "secrets-scan (repository-scan): completed" in text

    # An unchanged retry reuses the retained record and report.
    again = await recurring.invoke(
        service,
        await admitted(service, plan_ref, identity, raw),
        workflow_id=RUN,
        key="conclude",
    )
    assert again["securityPlanRunRef"] == outputs["securityPlanRunRef"]


@pytest.mark.parametrize(
    ("scenario", "state"),
    [("malformed_output", "failed"), ("not_run", "not_run"), ("cancelled", "not_run")],
)
async def test_missing_or_unusable_evidence_never_becomes_a_clean_result(
    environment, scenario, state
):
    service = environment.service
    plan_ref, identity, raw = await retained_plan(service)
    if scenario == "malformed_output":
        bad = await put(service, b"not json {", link_type="output.summary")
        await job_record(service, identity, "completed", [bad])
    elif scenario == "cancelled":
        await job_record(service, identity, "cancelled", [], reason="Cancelled.")
    outputs = await recurring.invoke(
        service,
        await admitted(service, plan_ref, identity, raw),
        workflow_id=RUN,
        key=scenario,
    )
    record = await recurring.read(
        service, outputs["securityPlanRunRef"], principal=f"workflow:{RUN}"
    )
    assert record["jobs"][0]["state"] == state
    # No native evidence was carried, so nothing was scanned clean.
    assert outputs["scannerSummaries"] == []
    planned = [row for row in record["requirements"] if row["jobIds"]]
    assert planned and all(row["state"] == "inconclusive" for row in planned)


async def test_the_conclusion_is_bound_to_the_reviewed_plan_identity(environment):
    service = environment.service
    plan_ref, identity, raw = await retained_plan(service)
    with pytest.raises(ApplicationError, match="differs from the reviewed plan"):
        await recurring.invoke(
            service,
            await admitted(service, plan_ref, "f" * 64, raw),
            workflow_id=RUN,
            key="other-identity",
        )
    # A plan draft that was never retained is not a plan to conclude.
    unsealed = await put(service, raw, link_type="input.security")
    with pytest.raises(ApplicationError, match="not a retained security plan"):
        await recurring.invoke(
            service,
            await admitted(service, unsealed, identity, raw),
            workflow_id=RUN,
            key="unsealed",
        )
