"""Host adapter retains the portable source-bound evidence contract."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from moonmind.workflows.skills.acceptance_contract import acceptance_evidence
from moonmind.workflows.skills.approval_policy import parse_step_gate_result


def _digest(value):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )


@pytest.fixture
def source_bound_report():
    scope = {
        "sourceRef": "example/repo#1",
        "sourceDigest": "sha256:original",
        "complete": True,
        "requirementIds": ["LIVE-1"],
    }
    plan = {
        "id": "LIVE-1",
        "sourceRef": scope["sourceRef"],
        "sourceDigest": scope["sourceDigest"],
        "sourceLocator": "acceptance[0]",
        "classification": "mandatory",
        "requiredBehavior": "Observe live parity",
        "executableChecks": ["./test-live-provider"],
        "proofMethods": ["live_provider"],
        "owner": "existing qualified runner",
        "profile": "production-equivalent",
    }
    evidence = {
        "artifact://actual-run": "sha256:"
        + hashlib.sha256(b"actual run evidence").hexdigest()
    }
    subject = {
        "repository": "example/repo",
        "revision": "candidate",
        "contentDigest": "git-tree:content",
    }
    binding = {
        "schemaVersion": "acceptance/v1",
        "subject": subject,
        "scope": scope,
        "completionTarget": {
            "ref": "refs/heads/main",
            "revision": "base",
            "contentDigest": "git-tree:base",
        },
        "freshness": {"policy": "content"},
        "verificationMap": [plan],
        "evidenceSet": evidence,
        "evidenceSetDigest": _digest(evidence),
        "evidence": [
            {
                "requirementId": "LIVE-1",
                "status": "VERIFIED",
                "obligationDigest": _digest(plan),
                "evidenceRefs": ["artifact://actual-run"],
                "proofMethod": "live_provider",
                "profile": "production-equivalent",
            }
        ],
    }
    return {
        "verdict": "FULLY_IMPLEMENTED",
        "recommendedNextAction": "advance",
        "validatedRefs": {"acceptance": binding},
    }


def test_source_bound_evidence_survives_host_gate(source_bound_report):
    accepted = acceptance_evidence(source_bound_report)
    assert accepted is not None
    stored = accepted.model_dump(by_alias=True, mode="json", exclude_none=True)
    assert stored == source_bound_report["validatedRefs"]["acceptance"]
    assert (
        parse_step_gate_result(source_bound_report, require_acceptance=True).verdict
        == "FULLY_IMPLEMENTED"
    )


@pytest.mark.parametrize(
    "change",
    [
        "unresolved_ref",
        "inventory_tamper",
        "missing_inventory",
        "failed_row",
        "synthetic_proof",
        "wrong_profile",
        "wrong_tested_subject",
        "invented_obligation",
        "missing_obligation",
        "wrong_obligation_digest",
        "malformed_plan",
    ],
)
def test_source_bound_success_rejects_missing_or_changed_required_proof(
    source_bound_report, change
):
    report = copy.deepcopy(source_bound_report)
    binding = report["validatedRefs"]["acceptance"]
    row = binding["evidence"][0]
    if change == "unresolved_ref":
        row["evidenceRefs"] = ["artifact://unread"]
    elif change == "inventory_tamper":
        binding["evidenceSet"]["artifact://actual-run"] = "sha256:" + "0" * 64
    elif change == "missing_inventory":
        binding.pop("evidenceSet")
    elif change == "failed_row":
        row["status"] = "FAILED"
    elif change == "synthetic_proof":
        row["proofMethod"] = "fake_backend"
    elif change == "wrong_profile":
        row["profile"] = "ordinary-ci"
    elif change == "wrong_tested_subject":
        row["testedSubject"] = {**binding["subject"], "contentDigest": "git-tree:other"}
    elif change == "invented_obligation":
        binding["verificationMap"][0]["classification"] = "optional"
    elif change == "missing_obligation":
        binding["verificationMap"] = []
    elif change == "wrong_obligation_digest":
        row["obligationDigest"] = "sha256:" + "1" * 64
    else:
        binding["verificationMap"] = [None]
    assert acceptance_evidence(report) is None
    assert (
        parse_step_gate_result(report, require_acceptance=True).verdict
        == "NO_DETERMINATION"
    )


def test_nonsemantic_probe_changes_do_not_invalidate_existing_proof(
    source_bound_report,
):
    binding = source_bound_report["validatedRefs"]["acceptance"]
    binding["verificationMap"][0]["capability"] = {
        "status": "busy",
        "evidenceRefs": ["artifact://current-probe"],
    }
    assert acceptance_evidence(source_bound_report) is not None


def test_legacy_acceptance_remains_accepted_without_new_map(source_bound_report):
    binding = source_bound_report["validatedRefs"]["acceptance"]
    for key in ("verificationMap", "evidenceSet", "evidenceSetDigest"):
        binding.pop(key)
    binding["evidence"] = [
        {"requirementId": "LIVE-1", "evidenceRefs": ["artifact://actual-run"]}
    ]
    assert acceptance_evidence(source_bound_report) is not None


def _source_reconciliation_fixture(report):
    source = {
        "issue_ref": "example/repo#1",
        "description": "Observe live parity. Release rehearsal is separate.",
    }
    source_bytes = json.dumps(source, ensure_ascii=False).encode()
    binding = report["validatedRefs"]["acceptance"]
    binding["scope"]["sourceDigest"] = (
        "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    )
    plan = binding["verificationMap"][0]
    plan["sourceDigest"] = binding["scope"]["sourceDigest"]
    plan["sourceQuote"] = "Observe live parity."
    binding["evidence"][0]["obligationDigest"] = _digest(plan)
    binding["sourceBinding"] = {
        "artifactRef": "art_original_brief",
        "pointer": "",
    }
    previous = {
        **copy.deepcopy(binding["scope"]),
        "requirementIds": ["LIVE-1", "INVENTED-RELEASE"],
    }
    binding["scopeReconciliation"] = {
        "previousScope": previous,
        "currentScope": copy.deepcopy(binding["scope"]),
        "removedRequirements": [
            {
                "id": "INVENTED-RELEASE",
                "reason": "Separate release rehearsal is outside implementation scope",
                "sourceLocator": "/description",
            }
        ],
    }
    return source_bytes, {"verdict": "BLOCKED", "scope": copy.deepcopy(previous)}


def test_original_source_bytes_admit_corrected_map_without_old_invented_requirement(
    source_bound_report,
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes, assessment = _source_reconciliation_fixture(source_bound_report)
    source_scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
    )
    assert not errors
    assert source_scope == source_bound_report["validatedRefs"]["acceptance"]["scope"]
    assert acceptance_evidence(source_bound_report) is not None
    assert assessment["scope"]["requirementIds"] == ["LIVE-1", "INVENTED-RELEASE"]


@pytest.mark.parametrize(
    "change",
    [
        "missing_current_requirement",
        "missing_reconciliation",
        "wrong_old_scope",
        "missing_removed_reason",
        "wrong_source_artifact",
        "wrong_source_pointer",
        "unread_source_quote",
        "wrong_source_digest",
        "wrong_source_identity",
    ],
)
def test_source_reconciliation_preserves_real_current_requirements(
    source_bound_report, change
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes, assessment = _source_reconciliation_fixture(source_bound_report)
    binding = source_bound_report["validatedRefs"]["acceptance"]
    if change == "missing_current_requirement":
        binding["scope"]["requirementIds"] = ["OTHER"]
    elif change == "missing_reconciliation":
        binding.pop("scopeReconciliation")
    elif change == "wrong_old_scope":
        binding["scopeReconciliation"]["previousScope"][
            "sourceDigest"
        ] = "sha256:forged"
    elif change == "missing_removed_reason":
        binding["scopeReconciliation"]["removedRequirements"][0]["reason"] = ""
    elif change == "wrong_source_artifact":
        binding["sourceBinding"]["artifactRef"] = "art_agent_copy"
    elif change == "wrong_source_pointer":
        binding["sourceBinding"]["pointer"] = "/unknown"
    elif change == "unread_source_quote":
        binding["verificationMap"][0]["sourceQuote"] = "Optional fake proof suffices"
    elif change == "wrong_source_digest":
        binding["scope"]["sourceDigest"] = "sha256:unobserved"
    else:
        binding["scope"]["sourceRef"] = "other/repo#2"
    source_scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
    )
    assert source_scope is None and errors


def test_whole_source_artifact_digest_uses_actual_bytes(source_bound_report):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes = b'{"description": "Observe live parity."}\n'
    binding = source_bound_report["validatedRefs"]["acceptance"]
    binding["scope"]["sourceDigest"] = (
        "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    )
    binding["verificationMap"][0]["sourceDigest"] = binding["scope"]["sourceDigest"]
    binding["sourceBinding"] = {"artifactRef": "art_original_brief", "pointer": ""}
    source_scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment={},
    )
    assert source_scope == binding["scope"] and not errors
    # Reformatting is not the same input bytes, even if parsed JSON is identical.
    assert source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes.rstrip(),
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment={},
    )[1]


def test_incomplete_original_source_cannot_be_repaired_by_claiming_complete_scope(
    source_bound_report,
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes, assessment = _source_reconciliation_fixture(source_bound_report)
    original = json.loads(source_bytes)
    original["source_resolution"] = {
        "status": "incomplete",
        "unrecovered_fields": ["acceptance_criteria"],
    }
    source_scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=json.dumps(original).encode(),
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
    )
    assert source_scope is None and errors


@pytest.mark.parametrize("gap_state", ["all_missing", "omitted", "contradictory_pass"])
def test_nonpassing_source_map_requires_truthful_complete_coverage(
    source_bound_report, gap_state
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes, assessment = _source_reconciliation_fixture(source_bound_report)
    source_bound_report.update(verdict="BLOCKED", recommendedNextAction="blocked")
    source_bound_report["remainingWork"] = [
        {
            "requirement": "LIVE-1",
            "gapType": "environment",
            "remainingWork": "Collect the explicitly required live provider proof",
        }
    ]
    binding = source_bound_report["validatedRefs"]["acceptance"]
    if gap_state != "contradictory_pass":
        binding["evidence"] = []
    if gap_state == "omitted":
        source_bound_report["remainingWork"] = []
    source_scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
    )
    assert (not errors and source_scope is not None) is (gap_state == "all_missing")


def test_source_artifact_uri_and_id_identify_the_same_original_bytes(
    source_bound_report,
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes, assessment = _source_reconciliation_fixture(source_bound_report)
    source_bound_report["validatedRefs"]["acceptance"]["sourceBinding"][
        "artifactRef"
    ] = "artifact://art_original_brief"
    scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
    )
    assert scope is not None and not errors


@pytest.mark.parametrize("pointer", ["/title", "/description"])
def test_issue_brief_source_cannot_be_shrunk_by_verifier_pointer(
    source_bound_report, pointer
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    original = {
        "issue_ref": "example/repo#1",
        "title": "Observe live parity",
        "description": "Implement the integration.",
        "acceptance_criteria": "Require live-provider proof before acceptance.",
        "constraints": "Never transmit private production data.",
    }
    raw = json.dumps(original).encode()
    binding = source_bound_report["validatedRefs"]["acceptance"]
    binding["sourceBinding"] = {"artifactRef": "art_original_brief", "pointer": pointer}
    narrowed = original[pointer.removeprefix("/")].encode()
    binding["scope"]["sourceDigest"] = "sha256:" + hashlib.sha256(narrowed).hexdigest()
    binding["verificationMap"][0]["sourceDigest"] = binding["scope"]["sourceDigest"]
    binding["evidence"][0]["obligationDigest"] = _digest(binding["verificationMap"][0])
    scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=raw,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment={},
    )
    assert scope is None and errors


@pytest.mark.parametrize("verdict", ["FULLY_IMPLEMENTED", "BLOCKED"])
def test_new_source_admission_cannot_fall_back_to_mapless_legacy_binding(
    source_bound_report, verdict
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bound_report["verdict"] = verdict
    binding = source_bound_report["validatedRefs"]["acceptance"]
    binding.pop("verificationMap")
    scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=b"original",
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment={},
        require_source_bound=True,
    )
    assert scope is None and errors
    assert source_scope_from_artifact(
        source_bound_report,
        source_bytes=b"original",
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment={},
    ) == (None, [])


@pytest.mark.parametrize(
    "missing",
    [
        "schemaVersion",
        "subject",
        "completionTarget",
        "freshness",
        "evidenceSet",
        "evidenceSetDigest",
    ],
)
def test_new_nonpassing_source_admission_requires_complete_current_binding(
    source_bound_report, missing
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    source_bytes, assessment = _source_reconciliation_fixture(source_bound_report)
    binding = source_bound_report["validatedRefs"]["acceptance"]
    # Full raw bytes are the host-owned input. No field selector is admitted.
    binding["sourceBinding"]["pointer"] = ""
    binding["scope"]["sourceDigest"] = (
        "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    )
    binding["verificationMap"][0]["sourceDigest"] = binding["scope"]["sourceDigest"]
    binding["scopeReconciliation"]["currentScope"] = copy.deepcopy(binding["scope"])
    binding["evidence"] = []
    source_bound_report.update(verdict="BLOCKED", recommendedNextAction="blocked")
    source_bound_report["remainingWork"] = [
        {
            "requirement": "LIVE-1",
            "gapType": "environment",
            "remainingWork": "Obtain mandatory live proof",
        }
    ]
    binding.pop(missing)
    scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=source_bytes,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
        require_source_bound=True,
    )
    assert scope is None and errors


def test_strict_nonpass_rejects_conflicting_primary_remaining_requirement(
    source_bound_report,
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    raw, assessment = _source_reconciliation_fixture(source_bound_report)
    source_bound_report.update(verdict="BLOCKED", recommendedNextAction="blocked")
    source_bound_report["validatedRefs"]["acceptance"]["evidence"] = []
    source_bound_report["remainingWork"] = [
        {
            "requirement": "INVENTED-RELEASE",
            "requirementIds": ["LIVE-1"],
            "gapType": "environment",
            "remainingWork": "Unrelated release proof remains",
        }
    ]
    scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=raw,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
        require_source_bound=True,
    )
    assert scope is None and errors


@pytest.mark.parametrize(
    "verdict", ["BLOCKED", "NO_DETERMINATION", "ADDITIONAL_WORK_NEEDED"]
)
@pytest.mark.parametrize("work", [None, []])
def test_unexplained_nonpass_cannot_publish_current_acceptance(
    source_bound_report, verdict, work
):
    from moonmind.workflows.skills.acceptance_contract import source_scope_from_artifact

    raw, assessment = _source_reconciliation_fixture(source_bound_report)
    source_bound_report["verdict"] = verdict
    source_bound_report["recommendedNextAction"] = "blocked"
    if work is not None:
        source_bound_report["remainingWork"] = work
    scope, errors = source_scope_from_artifact(
        source_bound_report,
        source_bytes=raw,
        source_ref="example/repo#1",
        source_artifact_ref="art_original_brief",
        assessment=assessment,
        require_source_bound=True,
    )
    assert scope is None and errors
    assert any("remaining work" in error for error in errors)
