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


def test_retained_capture_still_rejects_scope_tampering():
    import pytest

    from moonmind.security.application_access_target import ApplicationAccessCapture

    stored = json.loads(FIXTURE.read_text())
    body = json.loads(stored["httpCapture"])
    body["scope"]["web"]["routes"].append({"path": "/extra", "methods": ["GET"]})
    with pytest.raises(ValueError, match="capture scope digest differs"):
        ApplicationAccessCapture.model_validate(body)


def test_realtime_scope_digest_ignores_only_empty_template_selection():
    from tests.unit.security.test_realtime_scope_transport import realtime_scope

    scope = realtime_scope()
    for by_alias, key in ((False, "template_ids"), (True, "templateIds")):
        original = scope.model_dump(mode="json", by_alias=by_alias)
        original["web"].pop(key, None)
        explicit = {**original, "web": {**original["web"], key: []}}
        for value in (original, explicit):
            restored = TargetScope.model_validate(value)
            assert content_key(
                restored.model_dump(mode="json", by_alias=by_alias)
            ) == content_key(original)
            assert json.loads(restored.model_dump_json(by_alias=by_alias)) == original
        altered = TargetScope.model_validate(original)
        altered.realtime.max_messages += 1
        assert content_key(
            altered.model_dump(mode="json", by_alias=by_alias)
        ) != content_key(original)


def test_curated_template_selection_remains_digest_bound():
    import pytest

    from tests.unit.security.test_known_issue_templates import admission

    scope = TargetScope(web=admission())
    for by_alias, key in ((False, "template_ids"), (True, "templateIds")):
        body = scope.model_dump(mode="json", by_alias=by_alias)
        assert body["web"][key] == ["nextjs-middleware-bypass"]
        assert json.loads(scope.model_dump_json(by_alias=by_alias)) == body
        assert TargetScope.model_validate(body).web.template_ids == [
            "nextjs-middleware-bypass"
        ]
        digest = content_key(body)
        body["web"].pop(key)
        assert content_key(body) != digest
        with pytest.raises(ValueError, match="explicit IDs"):
            TargetScope.model_validate(body)
