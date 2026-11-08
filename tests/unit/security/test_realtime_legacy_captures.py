"""Historical HTTP/RGS evidence keeps its original canonical identities."""

import json
from pathlib import Path

from moonmind.security.application_access import ApplicationAccessReport
from moonmind.security.assessments import content_key
from moonmind.security.rgs_target import RgsTargetPlan
from moonmind.security.target_assessments import TargetScope
from moonmind.workflows.skills.security_assessment import (
    _verify_application_access_report,
)

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures/security/realtime_legacy_inputs.json"
)


def test_retained_http_report_still_verifies_without_realtime_fields():
    stored = json.loads(FIXTURE.read_text())
    report = ApplicationAccessReport.model_validate_json(stored["httpReport"])
    spec, capture = _verify_application_access_report(
        report, stored["httpSpec"].encode(), stored["httpCapture"].encode()
    )
    assert capture.scope.web.origin == "https://app.example"
    assert report.cases[0].outcome == "pass"
    assert "realtime" not in capture.scope.model_dump()
    assert "realtime_case_ref" not in spec.cases[0].request.model_dump()


def test_legacy_rgs_plan_and_scope_keep_original_digests():
    stored = json.loads(FIXTURE.read_text())
    scope = TargetScope.model_validate_json(stored["rgsScope"])
    plan = RgsTargetPlan.model_validate_json(stored["rgsPlan"])
    assert (
        content_key(scope.model_dump(mode="json", by_alias=True))
        == stored["rgsScopeDigest"]
    )
    assert (
        content_key(plan.model_dump(mode="json", by_alias=True))
        == stored["rgsPlanDigest"]
    )


def test_rgs_cannot_adopt_named_scope_without_durable_transport_budget():
    import pytest

    from moonmind.security.rgs_target import _admit_plan
    from moonmind.security.target_assessments import ScopedTargetClient
    from tests.unit.security.test_realtime_scope_transport import realtime_scope

    client = ScopedTargetClient(realtime_scope(), proxy="admission-only")
    with pytest.raises(ValueError, match="named real-time"):
        _admit_plan(None, None, client)
