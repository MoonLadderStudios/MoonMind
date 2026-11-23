"""A security plan run's conclusion through the production security.assess path (#5432).

The plan, its packs, the outcome records the planned jobs' steps published and
their cited summaries are real artifacts linked to the run. The concluding
assessment reads them through the same artifact service, validation, sealing
and report publication as any assessment. No target, credential or network
reference is resolved.
"""

import json

import pytest

from moonmind.security.security_plan import parse_security_plan
from moonmind.security.security_plan_run import OUTCOME_LINK, pack_selections
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_security_plan_journey import retained_plan_inputs

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
STEPS = {
    "code-scan": "tpl:security-plan-run:01:x",
    "web-baseline": "tpl:security-plan-run:02:x",
}


async def record_outcome(service, job_id, status, refs, *, reason=None):
    """What the runtime publishes for a planned job's step, linked to the run."""
    payload = {
        "schemaVersion": "security-plan-job-outcome/v1",
        "jobId": job_id,
        "status": status,
        "reportRefs": refs,
        **({"reason": reason} if reason else {}),
    }
    artifact, _ = await service.put_content_addressed_payload_complete(
        principal="system:agent_runtime",
        payload=json.dumps(payload).encode(),
        content_type="application/json",
        scope="security-plan-jobs",
        link={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
            "link_type": OUTCOME_LINK,
        },
        metadata_json={"step_id": STEPS[job_id]},
    )
    return artifact.artifact_id


async def conclusion_request(service):
    request, plan = await retained_plan_inputs(service)
    request["securityPlan"] = {"planRef": await recurring.put(service, plan)}
    retained = await recurring.invoke(service, request, key="retain")
    identity = retained["securityPlanIdentity"]
    parsed = parse_security_plan(json.dumps(plan))
    request["frameworkPacks"] = pack_selections(parsed)
    request["planRun"] = {
        "identity": identity,
        "jobs": [
            {"jobId": job, "kind": kind, "stepIds": [STEPS[job]]}
            for job, kind in (
                ("code-scan", "repository-scan"),
                ("web-baseline", "web-assessment"),
            )
        ],
        "skipped": [],
    }
    return request


async def report_text(service, outputs):
    _, body = await service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test("workflow:assessment"),
    )
    return body.decode()


async def test_conclusion_reports_every_planned_job_against_what_ran(environment):
    service = environment.service
    request = await conclusion_request(service)
    # The code scan claims completion but cites malformed output; the web job's
    # step was cancelled before it recorded anything.
    malformed = await recurring.put(service, {"schemaVersion": "repository-scanner/v1"})
    outcome_ref = await record_outcome(service, "code-scan", "completed", [malformed])

    outputs = await recurring.invoke(service, request, key="conclude")
    assert outputs["securityPlanRun"] == {"failed": 1, "not_run": 1}
    result = await recurring.read(service, outputs["securityPlanRunRef"])
    jobs = {row["jobId"]: row for row in result["jobs"]}
    assert jobs["code-scan"]["state"] == "failed"
    assert jobs["code-scan"]["reportRefs"] == []
    assert jobs["web-baseline"]["state"] == "not_run"
    statuses = {row["requirementId"]: row["status"] for row in result["requirements"]}
    # Missing planned evidence is never a satisfied requirement or a clean result.
    assert statuses["APP-1"] == statuses["OPS-1"] == "inconclusive"
    assert statuses["APP-3"] == "external_only"
    # The comparison is sealed with the plan and the outcome it judged.
    required = await recurring.required(service, outputs["securityPlanRunRef"])
    assert {request["securityPlan"]["planRef"], outcome_ref} <= required
    text = await report_text(service, outputs)
    assert "## Plan against actual" in text
    assert "- code-scan (repository-scan): failed — No readable native summary" in text
    assert "- web-baseline (web-assessment): not_run" in text

    # A restarted step records a newer outcome; the conclusion judges the latest.
    await record_outcome(
        service,
        "code-scan",
        "blocked_readiness",
        [],
        reason="scanner image unavailable",
    )
    again = await recurring.invoke(service, request, key="conclude-again")
    assert again["securityPlanRun"] == {"blocked_readiness": 1, "not_run": 1}


async def test_conclusion_refuses_a_plan_run_for_another_plan(environment):
    service = environment.service
    request = await conclusion_request(service)
    request["planRun"]["identity"] = "0" * 64
    with pytest.raises(Exception) as raised:
        await recurring.invoke(service, request, key="stale")
    assert "planRun.identity" in str(raised.value)
