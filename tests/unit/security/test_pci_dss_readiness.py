"""PCI DSS v4.0.1 readiness as installed declared-pack data (MoonLadderStudios/MoonMind#5438)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.security.assessments import (
    AssessmentScope,
    Comparison,
    Observation,
    compare_observations,
)
from moonmind.security.control_assessments import (
    assess_controls,
    render_control_assessment,
)
from moonmind.security.framework_packs import (
    compare_pack_metadata,
    compile_framework_plan,
    parse_declared_pack,
    readiness_metadata,
    retained_pack_metadata,
)
from moonmind.security.framework_registry import (
    installed_pack,
    pack_behavior,
    readiness_catalog,
)
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.pack_readiness import evaluate_readiness

PACK_ID = "pci-dss-readiness"
NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)
DAY = NOW.date().isoformat()
PERIOD = {"start": DAY, "end": DAY}
PRINCIPALS = {str(n) for n in range(1, 13)} | {"A1", "A2"}
MERCHANT = {
    "entityType": "merchant",
    "systems": ["app"],
    "cardholderDataEnvironment": ["app"],
    "panStored": False,
    "sadBeforeAuthorization": False,
    "supportsIssuing": False,
    "paymentChannels": ["ecommerce"],
    "posPoiEarlyTls": False,
    "validationRoute": "saq_a",
    "serviceProviders": ["Example Payments: hosted payment page (6.4.3, 11.6.1)"],
    "scopeBasis": "Synthetic scoping record",
}
SERVICE_PROVIDER = dict(
    MERCHANT,
    entityType="service_provider",
    panStored=True,
    paymentChannels=["card_present", "ecommerce"],
    validationRoute="roc",
)


def pack():
    return installed_pack(PACK_ID)[1]


def readiness(profile, choices=None, data=None):
    current = parse_declared_pack(json.dumps(data).encode()) if data else pack()
    return current, evaluate_readiness(
        current,
        pack_behavior(current),
        profile=profile,
        period=EvidencePeriod.model_validate(PERIOD),
        choices=choices or {},
        records=[],
        audit=None,
        admitted_assets=["app"],
    )


def plan(profile=SERVICE_PROVIDER, choices=None, data=None):
    current, result = readiness(profile, choices, data)
    compiled, diagnostics = compile_framework_plan(
        current,
        scope=AssessmentScope(assets=["app"]),
        period=EvidencePeriod.model_validate(PERIOD),
        choices=choices or {},
        now=NOW,
        readiness=result,
    )
    return current, result, compiled, diagnostics


def applicable(result):
    return {
        key
        for key, row in result.rows.items()
        if row["applicabilityStatus"] == "applicable"
    }


# -- AC1: unified contract, explicit dates, rights Unverified ---------------


def test_installed_pack_pins_edition_dates_and_reports_rights_unverified():
    current = pack()
    assert (current.edition, current.revision) == ("4.0.1", "1")
    assert current.publication_status == "published"
    assert current.published_on.isoformat() == "2024-06-11"
    assert current.effective_from.isoformat() == "2025-03-31"
    assert current.distribution == "redistributable"
    assert "none is reproduced" in current.license
    ids = {row.id for row in current.requirements}
    assert PRINCIPALS <= ids
    assert {"6.3.3", "11.3.2", "12.3.1", "A1.1.4"} <= ids
    # Superseded from 31 March 2025; every other requirement is in force.
    assert not {"6.4.1", "10.7.1"} & ids
    assert all(
        row.sources[0].section.startswith("PCI DSS v4.0.1")
        for row in current.requirements
    )
    _, result = readiness(MERCHANT)
    claims = result.context["externalClaims"]
    assert claims["pci-ssc-rights"]["status"] == "Unverified"
    assert claims["source-review"]["status"] == "Unverified"
    entry = next(item for item in readiness_catalog() if item["id"] == PACK_ID)
    assert entry["subjects"]["application"] == {"available": True}
    assert not entry["subjects"]["network"]["available"]
    assert entry["requirementCount"] == len(current.requirements)
    assert {"assessor-validation", "asv-scan", "independent-testing"} <= set(
        entry["externalEvidence"]
    )


# -- AC2: profile inputs change applicability, partners stay visible --------


def test_merchant_without_stored_data_and_service_provider_differ_with_reasons():
    _, merchant = readiness(MERCHANT)
    _, provider = readiness(SERVICE_PROVIDER)
    rows = merchant.rows
    assert rows["12.4.1"]["status"] == "not_applicable"
    assert "service providers only" in rows["12.4.1"]["reason"]
    assert rows["3.5.1"]["status"] == "not_applicable"
    assert "PAN is stored" in rows["3.5.1"]["reason"]
    assert rows["3.6.1"]["status"] == "not_applicable"
    assert rows["9.5.1"]["status"] == "not_applicable"
    assert rows["6.4.3"]["status"] == "incomplete"
    assert "Synthetic scoping record" in rows["6.4.3"]["reason"]
    only_provider = applicable(provider) - applicable(merchant)
    assert {
        "12.4.1",
        "12.5.2.1",
        "11.4.6",
        "3.5.1",
        "3.6.1.1",
        "9.5.1",
    } <= only_provider
    assert not applicable(merchant) - applicable(provider)
    assert provider.rows["A1.1.1"]["status"] == "not_applicable"
    assert provider.rows["3.3.2"]["status"] == "not_applicable"


def test_missing_profile_inputs_leave_applicability_unverified_with_requests():
    _, result = readiness({"systems": ["app"]})
    assert result.rows["12.4.1"]["status"] == "Unverified"
    assert "entity type" in result.rows["12.4.1"]["reason"]
    ids = {row["id"] for row in result.context["evidenceRequests"]}
    assert {"profile.entity_type", "profile.pan_stored", "applicability.12.4.1"} <= ids
    assert result.context["applicability"]["status"] == "incomplete"


def test_empty_required_payment_channels_stay_unverified_like_omitted_ones():
    omitted = {k: v for k, v in MERCHANT.items() if k != "paymentChannels"}
    _, absent = readiness(omitted)
    _, empty = readiness(dict(MERCHANT, paymentChannels=[]))
    for key in ("6.4.3", "9.5.1", "11.6.1"):
        assert absent.rows[key]["status"] == "Unverified"
        assert empty.rows[key]["status"] == "Unverified"
        assert empty.rows[key]["reason"] == absent.rows[key]["reason"]
    ids = {row["id"] for row in empty.context["evidenceRequests"]}
    assert {"profile.payment_channels", "applicability.6.4.3"} <= ids


def test_provider_owned_requirement_stays_visible_with_the_partner_recorded():
    reason = "Example Payments hosts the payment page and its scripts."
    choices = {
        "6.4.3": {"responsibleEntity": "partner", "responsibilityReason": reason}
    }
    _, result, compiled, _ = plan(MERCHANT, choices)
    assert result.rows["6.4.3"]["status"] == "partner_owned"
    assert "6.4.3" in result.context["unresolvedRequirements"]
    row = next(r for r in compiled.requirements if r.id == "6.4.3")
    assert (row.owner, row.responsibility_reason) == ("partner", reason)
    _, result = readiness(MERCHANT, {"6.4.3": {"responsibleEntity": "partner"}})
    assert any(
        r["id"] == "responsibility.6.4.3" for r in result.context["evidenceRequests"]
    )


# -- AC3: every requirement has an evidence method; external-only is explicit


def test_plan_classifies_every_requirement_and_external_only_has_no_scan():
    current, _, compiled, diagnostics = plan()
    assert len(compiled.requirements) == len(current.requirements)
    rows = {row.id: row for row in compiled.requirements}
    assert all(row.evidence_plan and row.evidence_plan.methods for row in rows.values())
    external = {key for key, row in rows.items() if row.evidence_plan.external_party}
    assert PRINCIPALS | {"11.3.2", "11.4.2", "11.4.3", "11.4.5", "11.4.6"} <= external
    assert "Approved Scanning Vendor" in rows["11.3.2"].evidence_plan.external_party
    assert "QSA or ISA" in rows["6"].evidence_plan.external_party
    assert "independence" in rows["11.4.2"].evidence_plan.external_party
    for key in external:
        assert rows[key].objectives == []
        assert rows[key].evidence_plan.checks == 0
        assert "deterministic_check" not in rows[key].evidence_plan.methods
    checked = {key for key, row in rows.items() if row.objectives}
    assert checked == {
        "1.2.5",
        "1.3.1",
        "4.2.1",
        "6.2.4",
        "6.3.3",
        "8.3.6",
        "8.4.1",
        "8.4.2",
        "8.6.2",
        "10.2.1",
        "10.3.4",
        "12.5.1",
    }
    assert not checked & PRINCIPALS
    assert not any(row.objectives_complete for row in rows.values())
    assert rows["6.3.3"].evidence_plan.checks == 2
    assert "partial objective checks" in diagnostics[-1]


def test_organisational_evidence_requests_carry_purpose_and_period():
    _, _, compiled, _ = plan()
    rows = {row.id: row for row in compiled.requirements}
    tra = rows["12.3.1"].organisational_evidence[0]
    assert (tra.id, tra.category, tra.purpose) == (
        "12.3.1.tra",
        "risk_register",
        "design",
    )
    assert rows["12.3.2"].organisational_evidence[0].category == "risk_register"
    assert [r.purpose for r in rows["7.2.4"].organisational_evidence] == ["occurrence"]
    assert rows["11.6.1"].organisational_evidence[0].id == "11.6.1.tra"
    assert rows["12.10.1"].organisational_evidence[0].category == "incident"
    assert rows["1.1.1"].organisational_evidence[0].category == "policy"
    # Requests follow applicability: no request for a requirement that does not apply.
    _, _, merchant, _ = plan(MERCHANT)
    assert (
        next(
            r for r in merchant.requirements if r.id == "12.4.2"
        ).organisational_evidence
        == []
    )


def test_supplied_revision_keeps_the_pack_request_category_and_purpose():
    revision = {
        "artifactRef": "art_tra",
        "kind": "document",
        "source": "Risk office",
        "collectedAt": NOW.isoformat(),
        "expiresAt": (NOW + timedelta(days=30)).isoformat(),
        "period": PERIOD,
        "population": ["app"],
        "sensitivity": "internal",
    }
    supplied = {
        "id": "12.3.1.tra",
        "category": "risk_register",
        "purpose": "design",
        "description": "Our targeted risk analyses",
        "owner": "Risk office",
        "revisions": [revision],
    }
    _, _, compiled, _ = plan(choices={"12.3.1": {"organisationalEvidence": [supplied]}})
    row = next(r for r in compiled.requirements if r.id == "12.3.1")
    assert [(r.id, r.owner) for r in row.organisational_evidence] == [
        ("12.3.1.tra", "Risk office")
    ]
    weakened = dict(supplied, purpose="adoption")
    with pytest.raises(ValueError, match="keeps the pack's category and purpose"):
        plan(choices={"12.3.1": {"organisationalEvidence": [weakened]}})


# -- AC4: technical evidence supports only its named sub-requirements ------


def observation(*, coverage, findings=(), expires=None):
    observed = NOW - timedelta(days=2) if expires and expires < NOW else NOW
    return Observation.model_validate(
        {
            "scope": {"assets": ["app"]},
            "observedAt": observed.isoformat(),
            "expiresAt": (expires or NOW + timedelta(days=1)).isoformat(),
            "coverage": coverage,
            "findings": list(findings),
            "provenance": {"fixture": "controlled"},
        }
    )


def cover(source, tool, *, rules=(), all_rules=True, status="complete"):
    return {
        "source": source,
        "tool": tool,
        "assets": ["app"],
        "ruleIds": list(rules),
        "allRules": all_rules,
        "configRef": f"{tool}:fixture",
        "status": status,
        "evidenceMethod": "deterministic_check",
    }


def finding(source, tool, rule):
    return {
        "source": source,
        "tool": tool,
        "ruleId": rule,
        "asset": "app",
        "nativeId": f"{tool}:{rule}",
        "evidenceRefs": ["art_native"],
    }


def assess(observed):
    _, _, compiled, _ = plan()
    compared = Comparison.model_validate(compare_observations(observed, None, now=NOW))
    window = {
        "observedAt": observed.observed_at.isoformat(),
        "expiresAt": observed.expires_at.isoformat(),
    }
    report = assess_controls(
        compiled,
        compared,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        coverage_windows=[window] * len(observed.coverage),
        now=NOW,
        evidence_availability={},
    )
    return {row.requirement.id: row for row in report.requirements}, report


CLEAN = [
    cover("image", "trivy"),
    cover("repository", "trivy"),
    cover("tls", "openssl", all_rules=False, status="partial"),
    cover(
        "service-inventory",
        "tcp-connect",
        rules=["informational-open-tcp-service"],
        all_rules=False,
    ),
    cover("cloud", "prowler", rules=["iam_root_mfa_enabled"], all_rules=False),
]


def test_clean_scan_supports_named_sub_requirements_and_never_satisfies():
    rows, _ = assess(observation(coverage=CLEAN))
    assert not any(row.conclusion == "satisfied" for row in rows.values())
    image = next(o for o in rows["6.3.3"].objectives if o.id == "image-vulnerabilities")
    assert (image.outcome, image.checked_cells, image.required_cells) == (
        "passed",
        1,
        1,
    )
    assert rows["6.3.3"].conclusion == "inconclusive"
    assert "deterministic_check" in rows["6.3.3"].methods
    assert any(
        "do not cover the complete requirement" in v for v in rows["6.3.3"].limitations
    )
    assert rows["8.4.1"].objectives[0].outcome == "passed"
    assert rows["8.4.1"].conclusion == "inconclusive"
    # The principal requirement and unmapped siblings carry no scan result.
    for key in ("6", "6.3.1", "11.3.1", "11.3.2"):
        assert rows[key].objectives == [] and rows[key].conclusion == "inconclusive"
        assert "deterministic_check" not in rows[key].methods
    assert any("External-only" in v for v in rows["6"].limitations)
    # Sampled TLS coverage and observation-only exposure never pass.
    assert rows["4.2.1"].objectives[0].outcome == "inconclusive"
    assert rows["1.2.5"].objectives[0].outcome == "inconclusive"


def test_findings_affect_only_their_named_sub_requirement():
    rows, _ = assess(
        observation(
            coverage=CLEAN,
            findings=[
                finding("image", "trivy", "CVE-2026-0001"),
                finding("tls", "openssl", "certificate_expired"),
                finding(
                    "service-inventory", "tcp-connect", "informational-open-tcp-service"
                ),
            ],
        )
    )
    assert rows["6.3.3"].conclusion == "not_satisfied"
    assert rows["6.3.3"].objectives[0].finding_ids
    assert rows["4.2.1"].conclusion == "not_satisfied"
    # An open service is exposure to review, never a failed requirement.
    assert rows["1.2.5"].conclusion == "inconclusive"
    assert rows["1.2.5"].objectives[0].finding_ids
    assert any(
        "neither establishes nor refutes" in v for v in rows["1.2.5"].limitations
    )
    for key in ("6", "4", "1", "11.3.1", "6.3.1"):
        assert rows[key].conclusion == "inconclusive"
        assert not any(o.finding_ids for o in rows[key].objectives)


def native_trivy_summaries(image_ref, repository_ref, *, complete=True, finding=False):
    """Admitted image and repository Trivy summaries as the scanner jobs retain them."""
    from moonmind.schemas.repository_scanner_models import (
        ImageAssessment,
        RepositoryScan,
    )
    from moonmind.security.image_assessments import (
        ImageAssessmentSummary,
        ImageExecution,
        ImageSubject,
    )
    from moonmind.security.repository_scanners import (
        NativeResult,
        ScannerExecution,
        ScannerSummary,
    )

    coverage = "complete" if complete else "partial"
    findings = (
        [finding_model("image", "trivy", "CVE-2026-0001", evidence=image_ref)]
        if finding
        else []
    )
    image = ImageAssessmentSummary(
        admission=ImageAssessment(
            operation="assess",
            target="archive",
            inputArtifactRef="art_imagearchive",
            databaseCacheRef="scanner-db",
            databaseDigest="d" * 64,
        ),
        execution=ImageExecution(
            operation="assess",
            target="archive",
            version="0.58.0",
            exitCode=0,
            state="succeeded",
        ),
        subject=ImageSubject(kind="archive", asset="app", inputDigest="a" * 64),
        result=NativeResult(
            execution="succeeded",
            coverage=coverage,
            examinedAssets=["app"],
            findings=findings,
        ),
        nativeRef=image_ref,
        coverage=coverage,
        limitations=[],
    )
    repository = ScannerSummary(
        scanner="trivy",
        admission=RepositoryScan(
            scanner="trivy", outputDirectory="artifacts/scan", trivyScanners=["vuln"]
        ),
        execution=ScannerExecution(
            inputDigest="a" * 64,
            assets=["app"],
            version="0.58.0",
            exits={"scan": 0},
            states={"scan": "succeeded"},
        ),
        results={
            "scan": NativeResult(
                execution="succeeded", coverage=coverage, examinedAssets=["app"]
            )
        },
        nativeRefs={"scan": repository_ref},
        coverage=coverage,
        limitations=[],
    )
    return image, repository


def finding_model(source, tool, rule, *, evidence):
    from moonmind.security.assessments import Finding

    return Finding.model_validate(
        dict(finding(source, tool, rule), evidenceRefs=[evidence])
    )


@pytest.mark.parametrize(
    "complete, found, expected",
    [(True, False, "passed"), (True, True, "failed"), (False, False, "inconclusive")],
)
def test_native_trivy_summaries_reach_the_vulnerability_objectives(
    complete, found, expected
):
    from types import SimpleNamespace

    from moonmind.workflows.skills.security_assessment import (
        _image_observation,
        _scanner_observation,
    )

    image, repository = native_trivy_summaries(
        "art_image", "art_repository", complete=complete, finding=found
    )
    request = SimpleNamespace(scope=AssessmentScope(assets=["app"]))
    observed = [
        _image_observation(request, image, "art_image_summary", NOW),
        _scanner_observation(request, repository, "art_repository_summary", NOW),
    ]
    rows, _ = assess(
        observation(
            coverage=[
                row.model_dump(mode="json", by_alias=True)
                for obs in observed
                for row in obs.coverage
            ],
            findings=[
                row.model_dump(mode="json", by_alias=True)
                for obs in observed
                for row in obs.findings
            ],
        )
    )
    outcomes = {o.id: o.outcome for o in rows["6.3.3"].objectives}
    assert outcomes["image-vulnerabilities"] == expected
    assert outcomes["dependency-vulnerabilities"] == (
        "passed" if complete else "inconclusive"
    )
    assert rows["6.3.3"].conclusion == ("not_satisfied" if found else "inconclusive")


@pytest.mark.parametrize("state", ["stale", "partial", "rules-unknown"])
def test_incomplete_or_stale_evidence_stays_inconclusive(state):
    coverage = [
        cover(
            "image",
            "trivy",
            status="partial" if state == "partial" else "complete",
            all_rules=state != "rules-unknown",
        )
    ]
    expires = NOW - timedelta(hours=1) if state == "stale" else None
    rows, _ = assess(observation(coverage=coverage, expires=expires))
    image = rows["6.3.3"].objectives[0]
    assert image.outcome == "inconclusive"
    assert rows["6.3.3"].conclusion == "inconclusive"
    if state == "stale":
        assert rows["6.3.3"].evidence_sufficiency == "stale"
    if state == "rules-unknown":
        assert any("every rule" in v for v in image.limitations)


# -- AC5: Prowler PCI mappings of another edition satisfy nothing ----------


def test_prowler_pci_mappings_of_another_edition_are_review_required():
    from moonmind.security.cloud_assessments import review_cloud_mappings
    from tests.unit.security.test_cloud_assessment import one_check_summary

    summary = one_check_summary(
        edits={
            "compliance": {
                "standards": [PACK_ID],
                "requirements": ["8.4.1"],
                "framework_version": "4.0",
            },
            "unmapped": {"compliance": {"PCI-4.0": ["8.4.1", "10.2.1"]}},
        }
    )
    _, _, compiled, _ = plan()
    reviews = review_cloud_mappings(summary, compiled)
    assert len(reviews) == 2
    assert all(r["state"] == "review_required" for r in reviews)
    assert all(r["selectedRequirements"] == [] for r in reviews)
    assert "framework_edition_mismatch" in reviews[0]["reasons"]
    assert "no_matching_selected_framework" in reviews[1]["reasons"]
    rows, _ = assess(observation(coverage=CLEAN))
    assert rows["8.4.1"].conclusion != "satisfied"


# -- AC6: superseded or future editions refused; revisions diff ------------


@pytest.mark.parametrize(
    "change, reason",
    [
        (
            {"publicationStatus": "superseded"},
            "pci-dss-readiness 4.0.1 revision 1 is superseded, not operative",
        ),
        (
            {"publishedOn": "2027-01-01", "effectiveFrom": "2027-01-01"},
            "effective from 2027-01-01",
        ),
        ({"effectiveUntil": "2026-01-01"}, "no longer effective since 2026-01-01"),
    ],
)
def test_superseded_or_not_yet_effective_edition_is_refused_readably(change, reason):
    data = json.loads(installed_pack(PACK_ID)[0])
    data.update(change)
    with pytest.raises(ValueError, match=reason):
        plan(data=data)


def test_new_edition_revision_is_an_intelligence_change_against_retained_metadata():
    raw, current = installed_pack(PACK_ID)
    previous = retained_pack_metadata(
        current, artifact_ref="art_v1", sha256="a" * 64, now=NOW
    )
    data = json.loads(raw)
    data.update(revision="2", edition="4.0.1-errata")
    data["requirements"][0]["summary"] += " Revised."
    revised = parse_declared_pack(json.dumps(data).encode())
    metadata = retained_pack_metadata(
        revised, artifact_ref="art_v2", sha256="b" * 64, now=NOW
    )
    changes = compare_pack_metadata(metadata, previous)
    for key in ("edition", "revision", "requirementsDigest", "sha256"):
        assert any(f": {key};" in change for change in changes)
    assert all("not remediation evidence" in change for change in changes)


# -- AC7: the report states readiness only, validation Unverified ----------


def test_report_states_readiness_only_and_external_validation_unverified():
    current, result, _, diagnostics = plan()
    _, report = assess(observation(coverage=CLEAN))
    metadata = retained_pack_metadata(
        current, artifact_ref="art_pci", sha256="c" * 64, now=NOW
    )
    metadata.update(
        readiness=readiness_metadata(current, result),
        diagnostics=diagnostics,
        intelligenceChanges=[],
    )
    report.framework_packs = [metadata]
    text = render_control_assessment(report)
    assert "Readiness limitation: Readiness evidence only." in text
    assert "validation by a QSA, ISA or acquirer remains Unverified" in text
    assert "External claim assessor-validation: Unverified" in text
    assert "External claim asv-scan: Unverified" in text
    assert "- 11.3.2: **inconclusive**" in text
    assert "external-only, closed by PCI SSC Approved Scanning Vendor (ASV)" in text
    assert "- 6.3.3: **inconclusive** (partial)" in text
    assert "MoonMind checks: 2" in text
    assert "compliant" not in text.replace("Report on Compliance", "")


# -- Collected review evidence is judged only by the packs it names --------


@pytest.mark.asyncio
async def test_review_evidence_collected_for_other_packs_is_not_invalid_here():
    from moonmind.security.frameworks import FrameworkPackSelection
    from moonmind.workflows.skills.security_assessment import AssessmentExecution
    from tests.artifact_access import access_for_test

    # Change-control review evidence names ISO/SOC 2 requirements and lacks
    # the typed fields that PCI DSS evidence records carry.
    collected = {
        "kind": "technical_assessment",
        "requirementIds": ["8.32", "CC8.1"],
        "systems": ["app"],
        "period": PERIOD,
        "method": "evidence",
        "description": "Synthetic change-control sources",
        "provenance": {
            "sourceRef": "art_change_root",
            "issuer": "Synthetic reader",
            "version": "1",
            "recordedOn": DAY,
        },
        "artifactRef": "art_change_root",
    }

    class Artifacts:
        async def read(self, *, artifact_id, access):
            return None, json.dumps(collected).encode()

    execution = AssessmentExecution(
        Artifacts(),
        principal="workflow:assessment",
        context={"namespace": "default", "workflow_id": "assessment", "run_id": "r"},
        access=access_for_test("workflow:assessment"),
    )
    current = pack()
    selection = FrameworkPackSelection.model_validate(
        {
            "packId": PACK_ID,
            "edition": current.edition,
            "revision": current.revision,
            "period": PERIOD,
            "profile": SERVICE_PROVIDER,
        }
    )
    result, _ = await execution.pack_readiness(
        current,
        selection,
        AssessmentScope(assets=["app"]),
        {},
        {"8.32", "CC8.1"},
        context={
            "collected": ("art_change_review",),
            "now": NOW,
            "previous": None,
            "previousRef": None,
        },
    )
    assert not any(
        request.get("artifactRef") == "art_change_review"
        for request in result.context["evidenceRequests"]
    )
