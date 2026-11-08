"""Controlled reports exercise retained evidence, not documentation wording."""

import json

import pytest

from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_assessment_evidence_exports import unpack

environment = recurring.environment


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "issue",
        "clean",
        "failed",
        "partial",
        "stale",
        "authentication_failed",
        "unsupported",
    ],
)
async def test_readable_report_traces_observed_scope_and_limits(environment, scenario):
    service = environment.service
    request, inventory, _feed = await recurring.inputs(
        service, rules=scenario != "clean"
    )
    if scenario in {"failed", "partial", "authentication_failed", "unsupported"}:
        inventory["status"] = scenario
    if scenario == "stale":
        inventory["observedAt"] = "2020-01-01T00:00:00Z"
        inventory["expiresAt"] = "2020-01-02T00:00:00Z"
    request["inventoryRef"] = await recurring.put(service, inventory)
    result = await recurring.invoke(service, request)
    comparison = await recurring.read(service, result["comparisonRef"])
    _, body = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    report = body.decode()
    assert result["comparisonRef"] in report
    assert comparison["observation"]["observedAt"] in report
    assert comparison["observation"]["expiresAt"] in report
    assert "Review status: not_requested" in report
    for row in comparison["findings"]:
        assert row["findingId"] in report
        assert row["finding"]["nativeId"] in report
        assert all(ref in report for ref in row["finding"]["evidenceRefs"])
    for row in comparison["observation"]["coverage"]:
        assert row["configRef"] in report
        assert row["status"] in report
    if scenario in {
        "failed",
        "partial",
        "stale",
        "authentication_failed",
        "unsupported",
    }:
        assert "stale_or_partial" in report
    request["evidenceExport"] = {
        "artifactRefs": [recurring.report_ref(result), result["comparisonRef"]]
    }
    exported = await recurring.invoke(service, request)
    archive, manifest = await unpack(service, exported)
    assert manifest["offlineReview"]["status"] == "incomplete"
    assert manifest["offlineReview"]["missingArtifactRefs"]
    # Reader can inspect the retained report without contacting the scanner.
    reports = [
        entry
        for entry in manifest["entries"]
        if entry["sourceSchema"] == "utf8-text/v1"
    ]
    assert reports
    assert comparison["observation"]["observedAt"].encode() in archive.read(
        reports[0]["memberName"]
    )


def test_game_math_projection_never_promotes_imported_control_method():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from moonmind.security.assessments import AssessmentScope
    from moonmind.security.evidence_exports import prepare_evidence_member
    from moonmind.security.game_math import assess_game_math
    from moonmind.workflows.skills.security_assessment import _game_math_observation
    from tests.unit.security.test_game_math import COIN, one_bet, spec

    result = assess_game_math(
        spec(COIN, *one_bet(), declared=[{"strategy": "play", "rtp": "1"}])
    )
    body, _extension, schema = prepare_evidence_member(
        result.model_dump_json(by_alias=True).encode(),
        content_type="application/json",
        hint="",
    )
    assert schema == "game-math-assessment/v1"
    assert json.loads(body)["specDigest"] == result.spec_digest
    observation = _game_math_observation(
        SimpleNamespace(scope=AssessmentScope(assets=[result.game_ref])),
        result,
        "art_math",
        datetime.now(UTC),
    )
    assert observation.coverage[0].evidence_method == "unclassified"


@pytest.mark.asyncio
async def test_verified_retest_and_attributed_report_context_survive_offline(
    environment,
):
    service = environment.service
    request, inventory, _ = await recurring.inputs(service)
    first = await recurring.invoke(service, request)
    original = await recurring.read(service, first["comparisonRef"])
    finding_id = original["findings"][0]["findingId"]
    inventory["components"][0]["version"] = "2.0"
    retest_ref = await recurring.put(service, {"retest": "passed"})
    request.update(
        inventoryRef=await recurring.put(service, inventory),
        baselineRef=first["comparisonRef"],
        decisionsRef=await recurring.put(
            service, [recurring.decision(finding_id, [retest_ref])]
        ),
        reportContext={
            "author": "Assessor A",
            "reviewer": "Reviewer B",
            "reviewStatus": "reviewed",
            "methodologyVersions": {"OWASP ASVS": "5.0.0"},
            "annotations": [
                {
                    "findingId": finding_id,
                    "author": "Assessor A",
                    "annotation": "Dependency issue in the tested component",
                    "remediation": "Upgrade the component and check the pinned artifact",
                    "managementResponse": "Upgrade was applied to the test environment",
                    "priority": "high",
                    "severity": "high",
                    "businessRisk": "limited test exposure",
                    "riskRationale": "Test service is isolated from production",
                    "environmentInputs": {"exposure": "isolated test network"},
                    "cvssVector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
                }
            ],
        },
    )
    result = await recurring.invoke(service, request, key="retested")
    native = await recurring.read(service, result["comparisonRef"])
    assert native["findings"][0]["state"] == "remediated_verified"
    _, body = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert retest_ref.encode() in body and first["comparisonRef"].encode() in body
    assert b"Review status: reviewed" in body and b"Reviewer B" in body
    assert b"5.0.0" in body and b"limited test exposure" in body
    assert b"retest_passed" in body
    assert "severity" not in native["findings"][0]["finding"]
    assert result["reportContextRef"].encode() in body
    request["evidenceExport"] = {
        "artifactRefs": [
            recurring.report_ref(result),
            result["reportContextRef"],
            result["comparisonRef"],
        ]
    }
    exported = await recurring.invoke(service, request, key="retested")
    archive, manifest = await unpack(service, exported)
    assert retest_ref in manifest["offlineReview"]["missingArtifactRefs"]
    root = exported["evidenceExport"]["packageRef"]
    await service.pin(artifact_id=root, principal="operator", reason="Offline handoff")
    await recurring.age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    _, retained = await service.read(
        artifact_id=root, access=access_for_test("workflow:assessment")
    )
    assert retained == archive.fp.getvalue()


def test_report_context_requires_explicit_risk_and_review_attribution():
    from pydantic import ValidationError

    from moonmind.security.assessments import (
        AssessmentReportContext,
        FindingReportAnnotation,
    )

    with pytest.raises(ValidationError, match="reviewer"):
        AssessmentReportContext(author="author", reviewStatus="reviewed")
    with pytest.raises(ValidationError, match="rationale"):
        FindingReportAnnotation(
            findingId="id", author="author", annotation="text", severity="high"
        )


def test_offline_archive_integrity_rejects_changed_missing_or_extra_members():
    import hashlib
    import io
    import zipfile

    from moonmind.security.evidence_exports import (
        WithheldEvidence,
        build_evidence_archive,
        member_name,
        verify_evidence_archive,
    )

    body = b"Independently readable assessment"
    name = member_name(0, body, "txt")
    manifest = {
        "schemaVersion": "assessment-evidence-export/v1",
        "entries": [
            {
                "status": "included",
                "memberName": name,
                "memberSha256": hashlib.sha256(body).hexdigest(),
                "memberBytes": len(body),
            }
        ],
        "offlineReview": {
            "status": "incomplete",
            "missingArtifactRefs": ["art_missing"],
        },
    }
    package, _ = build_evidence_archive(manifest, {name: body}, limit=65536)
    verified = verify_evidence_archive(package)
    assert verified["offlineReview"]["status"] == "incomplete"
    for replacement in (
        {name: body + b"tampered"},
        {},
        {name: body, "extra.txt": b"extra"},
    ):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("manifest.json", json.dumps(manifest))
            for path, data in replacement.items():
                output.writestr(path, data)
        with pytest.raises(WithheldEvidence):
            verify_evidence_archive(archive.getvalue())


def test_native_admission_is_context_not_execution_authority():
    from moonmind.security.assessment_reports import render_native_admission
    from tests.unit.security.test_service_inventory_integration import summarized

    summary = summarized()
    report = render_native_admission(
        [("art_inventory", summary.model_dump_json(by_alias=True).encode())]
    )
    assert "art_inventory" in report
    assert "nativeNetwork" in report
    assert "serviceInventory" in report
    assert summary.plan_key in report
    assert "not independent proof of execution or authorization" in report


@pytest.mark.asyncio
async def test_accepted_risk_and_unknown_annotation_cannot_rewrite_native(environment):
    service = environment.service
    request, _, _ = await recurring.inputs(service)
    first = await recurring.invoke(service, request)
    comparison = await recurring.read(service, first["comparisonRef"])
    finding_id = comparison["findings"][0]["findingId"]
    decision = recurring.decision(finding_id, [first["scanRef"]])
    decision.update(
        disposition="accepted_risk", reason="Recorded test-environment acceptance"
    )
    decision.pop("retestOutcome")
    request["decisionsRef"] = await recurring.put(service, [decision])
    accepted = await recurring.invoke(service, request)
    _, body = await service.read(
        artifact_id=recurring.report_ref(accepted),
        access=access_for_test("workflow:assessment"),
    )
    assert b"accepted_risk" in body and b"Recorded test-environment acceptance" in body
    assert (await recurring.read(service, accepted["comparisonRef"]))["findings"][0][
        "state"
    ] != "remediated_verified"
    request["reportContext"] = {
        "author": "assessor",
        "annotations": [
            {
                "findingId": "unknown",
                "author": "assessor",
                "annotation": "No fabricated finding",
            }
        ],
    }
    invalid = await recurring.invoke(service, request)
    assert invalid["publicationOutcome"] == "failed"
    assert invalid["scanRef"] == accepted["scanRef"] == first["scanRef"]
    assert invalid["comparisonRef"] == accepted["comparisonRef"]
    request["reportContext"]["annotations"][0]["findingId"] = finding_id
    corrected = await recurring.invoke(service, request)
    assert corrected["publicationOutcome"] == "published"
    assert corrected["scanRef"] == first["scanRef"]


@pytest.mark.asyncio
async def test_structured_personal_risk_inputs_are_redacted_before_rendering(
    environment,
):
    service = environment.service
    request, _, _ = await recurring.inputs(service)
    first = await recurring.invoke(service, request)
    finding_id = (await recurring.read(service, first["comparisonRef"]))["findings"][0][
        "findingId"
    ]
    request["reportContext"] = {
        "author": "assessor",
        "annotations": [
            {
                "findingId": finding_id,
                "author": "assessor",
                "annotation": "Context",
                "environmentInputs": {
                    "phone": "555-0100",
                    "passportNumber": "fixture-private-id",
                    "exposure": "isolated",
                },
            }
        ],
    }
    result = await recurring.invoke(service, request)
    _, primary = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert b"555-0100" not in primary
    assert b"fixture-private-id" not in primary
    assert b"isolated" in primary
    native = await recurring.read(service, result["reportContextRef"])
    assert native["annotations"][0]["environmentInputs"]["phone"] == "555-0100"
    request["evidenceExport"] = {
        "artifactRefs": [recurring.report_ref(result), result["reportContextRef"]]
    }
    exported = await recurring.invoke(service, request)
    archive, manifest = await unpack(service, exported)
    for entry in manifest["entries"]:
        body = archive.read(entry["memberName"])
        assert b"555-0100" not in body
        assert b"fixture-private-id" not in body
