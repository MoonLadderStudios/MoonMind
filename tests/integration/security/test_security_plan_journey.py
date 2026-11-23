"""security-plan/v1 through the existing security.assess artifact and report path.

Pack, plan and project artifacts, validation, retention, sealing and the primary
report are production paths. No target, credential or network reference is
resolved: the assessment runs offline with no admitted credentials or egress.
"""

import hashlib
import json

import pytest

from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_security_plan import (
    hand_authored,
    load,
    pack_payload,
    snapshot,
)
from tests.unit.security.test_web_api_methodology import request_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def retained_plan_inputs(service):
    project = snapshot()
    plan = hand_authored(project)
    for standard, pack_id in zip(plan["standards"], ("synthetic-app", "synthetic-ops")):
        payload = pack_payload(
            pack_id,
            distribution=(
                "restricted" if pack_id == "synthetic-ops" else "redistributable"
            ),
        )
        load(payload)
        raw = json.dumps(payload).encode()
        standard["artifactRef"] = await recurring.put(service, raw)
        standard["sha256"] = hashlib.sha256(raw).hexdigest()
    request = request_payload()
    del request["methodology"]
    request["scope"] = plan["subject"]["scope"]
    request["assessmentProject"] = project.model_dump(mode="json", by_alias=True)
    return request, plan


async def test_plan_is_validated_rendered_retained_sealed_and_reused(environment):
    service = environment.service
    request, plan = await retained_plan_inputs(service)
    plan_ref = await recurring.put(service, plan)
    request["securityPlan"] = {"planRef": plan_ref}
    assert request["credentialRefs"] == [] and request["networkRefs"] == []

    outputs = await recurring.invoke(service, request, key="plan")
    retained = outputs["securityPlanRef"]
    assert outputs["securityPlanGaps"] == 1
    stored = await recurring.read(service, retained)
    assert stored["schemaVersion"] == "security-plan/v1"
    assert stored["requirements"][3]["gaps"][0]["kind"] == "no_evidence_path"
    required = await recurring.required(service, retained)
    assert {
        plan["standards"][0]["artifactRef"],
        plan["standards"][1]["artifactRef"],
        outputs["assessmentProjectRef"],
        plan_ref,
    } <= required
    _, summary = await service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test("workflow:assessment"),
    )
    text = summary.decode()
    assert "## Security plan" in text and "### Gap list" in text
    assert "APP-4: no_evidence_path" in text and "A plan grants nothing" in text
    assert "Synthetic OPS" not in text  # Restricted pack text stays in its artifact.

    # An unchanged retry reuses the retained plan; a changed job input is new.
    again = await recurring.invoke(service, request, key="plan")
    assert again["securityPlanRef"] == retained
    assert again["securityPlanIdentity"] == outputs["securityPlanIdentity"]
    changed = json.loads(json.dumps(plan))
    changed["jobs"][0]["input"]["outputDirectory"] = "scan-2"
    request["securityPlan"] = {"planRef": await recurring.put(service, changed)}
    other = await recurring.invoke(service, request, key="plan")
    assert other["securityPlanIdentity"] != outputs["securityPlanIdentity"]
    assert other["securityPlanRef"] != retained


async def test_invalid_plan_is_refused_with_field_addressed_diagnostics(environment):
    service = environment.service
    request, plan = await retained_plan_inputs(service)
    secret = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8"
    plan["requirements"].pop(4)
    plan["jobs"][1]["input"]["origin"] = "https://elsewhere.example.test"
    plan["openPoints"][0]["text"] = f"token={secret}"
    request["securityPlan"] = {"planRef": await recurring.put(service, plan)}
    with pytest.raises(Exception) as raised:
        await recurring.invoke(service, request, key="invalid-plan")
    message = str(raised.value)
    assert "INVALID_INPUT" in message
    assert "requirements: omits synthetic-ops 2026 requirement OPS-1" in message
    assert "jobs[1].authority: differs from the derived authority" in message
    assert "openPoints[0].text: contains secret material" in message
    assert secret not in message and "elsewhere" not in message
