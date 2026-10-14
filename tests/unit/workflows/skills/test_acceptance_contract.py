"""Exercise the resolved portable helper with real Git candidates and test evidence."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType

import pytest

from moonmind.workflows.skills.acceptance_contract import (
    acceptance_evidence,
    assessment_scope_violations,
)
from moonmind.workflows.skills.approval_policy import parse_step_gate_result

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".agents/skills/moonspec-verify/scripts/acceptance.py"
portable = ModuleType("portable_acceptance")
exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), portable.__dict__)


def git(repo, *args):
    return (
        subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)
        .decode()
        .strip()
    )


@pytest.fixture
def candidate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "release")
    git(repo, "config", "user.email", "acceptance@example.test")
    git(repo, "config", "user.name", "Acceptance fixture")
    (repo / ".gitignore").write_text("__pycache__/\n")
    (repo / "app.py").write_text("def value():\n    return 0\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    git(repo, "switch", "-c", "feature")
    (repo / "app.py").write_text("def value():\n    return 42\n")
    git(repo, "commit", "-am", "implementation")
    source = b"Return 42. Keep value callable. Deployment is a separate operation."
    current = portable.capture(repo, "example/repo", "release")
    current["scope"] = portable.scope(
        source, "example/repo#1", ["AC-1", "AC-2"], complete=True
    )
    current["freshnessPolicy"] = "content"
    # Obtain the fixture's objective evidence by executing its actual behavior.
    checks = {"AC-1": "assert app.value() == 42", "AC-2": "assert callable(app.value)"}
    rows = []
    for requirement, check in checks.items():
        result = subprocess.run(
            [sys.executable, "-c", "import app; " + check],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        log = tmp_path / (requirement + ".json")
        log.write_text(
            json.dumps(
                {
                    "command": check,
                    "exitCode": result.returncode,
                    "subject": current["subject"],
                }
            )
        )
        rows.append({"requirementId": requirement, "evidenceRefs": [str(log)]})
    binding = {key: current[key] for key in ("subject", "scope", "completionTarget")}
    binding.update(
        schemaVersion="acceptance/v1", evidence=rows, freshness={"policy": "content"}
    )
    report = {
        "verdict": "FULLY_IMPLEMENTED",
        "recommendedNextAction": "advance",
        "validatedRefs": {"acceptance": binding},
    }
    return repo, current, report


def test_initial_inspection_cannot_replace_objective_gate(candidate):
    _, current, report = candidate
    assessment = {"verdict": "FULLY_IMPLEMENTED", "requirements": [{"status": "met"}]}
    assert (
        parse_step_gate_result(assessment, require_acceptance=True).verdict
        == "NO_DETERMINATION"
    )
    # Historical serialized gates retain their meaning outside the new contract.
    assert parse_step_gate_result(assessment).verdict == "FULLY_IMPLEMENTED"
    assert (
        parse_step_gate_result(report, require_acceptance=True).verdict
        == "FULLY_IMPLEMENTED"
    )
    assert portable.reuse(report, current) == {
        "reusable": True,
        "completionEligible": False,
        "reasons": [],
    }


@pytest.mark.parametrize(
    "change",
    [
        "candidate",
        "source",
        "requirements",
        "incomplete",
        "freshness",
        "expired",
        "missing_check",
        "target_policy",
    ],
)
def test_reuse_invalidates_changed_subject_scope_or_required_proof(candidate, change):
    repo, current, report = candidate
    if change == "candidate":
        (repo / "app.py").write_text("def value():\n    return -1\n")
        current.update(portable.capture(repo, "example/repo", "release"))
    elif change == "source":
        current["scope"] = portable.scope(
            b"Return 43", "example/repo#1", ["AC-1", "AC-2"], complete=True
        )
    elif change == "requirements":
        current["scope"] = copy.deepcopy(current["scope"])
        current["scope"]["requirementIds"].append("AC-3")
    elif change == "incomplete":
        current["scope"] = copy.deepcopy(current["scope"])
        current["scope"]["complete"] = False
    elif change == "freshness":
        current["freshnessPolicy"] = "current-deployment"
    elif change == "expired":
        report["validatedRefs"]["acceptance"]["freshness"]["validUntil"] = (
            "2000-01-01T00:00:00Z"
        )
    elif change == "missing_check":
        report["validatedRefs"]["acceptance"]["evidence"].pop()
    else:
        current["completionTarget"] = dict(
            current["completionTarget"], ref="refs/heads/other"
        )
    result = portable.reuse(report, current)
    assert not result["reusable"] and not result["completionEligible"]
    assert result["reasons"]


def test_explicit_target_detached_head_dirty_index_and_squash_merge(candidate):
    repo, current, report = candidate
    index_before = git(repo, "write-tree")
    (repo / "notes.txt").write_text("unrelated user notes")
    git(repo, "add", "notes.txt")
    staged_before = git(repo, "diff", "--cached")
    (repo / "scratch.txt").write_text("unstaged new source")
    git(repo, "switch", "--detach")
    dirty = portable.capture(repo, "example/repo", "release")
    assert dirty["subject"]["revision"] == current["subject"]["revision"]
    assert dirty["subject"]["contentDigest"] != current["subject"]["contentDigest"]
    target = portable.capture(repo, "example/repo", "release", target_mode=True)
    assert target["subject"]["revision"] == git(repo, "rev-parse", "release")
    assert target["subject"]["revision"] != git(repo, "rev-parse", "HEAD")
    assert git(repo, "diff", "--cached") == staged_before
    assert (repo / "scratch.txt").read_text() == "unstaged new source"
    assert git(repo, "branch", "--show-current") == ""
    # A squash-shaped commit: different commit identity, identical tested tree.
    squash = git(repo, "commit-tree", index_before, "-p", "release", "-m", "squash")
    git(repo, "update-ref", "refs/heads/release", squash)
    current.update(portable.capture(repo, "example/repo", "release", target_mode=True))
    assert portable.reuse(report, current)["completionEligible"]
    assert (
        current["subject"]["revision"]
        != report["validatedRefs"]["acceptance"]["subject"]["revision"]
    )


def test_partial_remediation_retains_prior_requirement_regression(candidate):
    repo, current, report = candidate
    # Previously met AC-2 remains mandatory after a change intended for AC-1.
    (repo / "app.py").write_text("value = 42\n")
    current.update(portable.capture(repo, "example/repo", "release"))
    check = subprocess.run(
        [sys.executable, "-c", "import app; assert callable(app.value)"],
        cwd=repo,
        capture_output=True,
    )
    assert check.returncode != 0
    assert not portable.reuse(report, current)["reusable"]
    report["validatedRefs"]["acceptance"]["evidence"] = report["validatedRefs"][
        "acceptance"
    ]["evidence"][:1]
    assert acceptance_evidence(report) is None


def test_optional_diagnostic_failure_does_not_invalidate_actual_acceptance(candidate):
    _, current, report = candidate
    diagnostic = subprocess.run(
        [sys.executable, "-c", 'raise RuntimeError("optional enrichment unavailable")'],
        capture_output=True,
    )
    assert diagnostic.returncode != 0
    report["limitations"] = ["Optional enrichment unavailable; required checks passed."]
    assert portable.reuse(report, current)["reusable"]
    assert (
        parse_step_gate_result(report, require_acceptance=True).verdict
        == "FULLY_IMPLEMENTED"
    )


def test_capture_cli_is_portable_without_moonspec_packet(candidate, tmp_path):
    repo, _, _ = candidate
    source = tmp_path / "brief.txt"
    source.write_text("Return 42")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "capture",
            "--repo",
            str(repo),
            "--repository",
            "example/repo",
            "--target",
            "release",
            "--source",
            str(source),
            "--source-ref",
            "example/repo#1",
            "--requirement",
            "AC-1",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    captured = json.loads(result.stdout)
    assert captured["completionTarget"]["ref"] == "refs/heads/release"
    assert captured["scope"]["complete"] is True


def test_expired_evidence_cannot_advance_a_current_workflow(candidate):
    _, _, report = candidate
    report["validatedRefs"]["acceptance"]["freshness"]["validUntil"] = (
        "2000-01-01T00:00:00Z"
    )
    gate = parse_step_gate_result(
        report, require_acceptance=True, validation_time=datetime.now(timezone.utc)
    )
    assert gate.verdict == "NO_DETERMINATION"
    assert "expired" in gate.downgrade_reason


@pytest.mark.parametrize("binding", [None, [], {"evidence": [None]}, {"subject": None}])
def test_malformed_evidence_is_actionable_missing_proof(candidate, binding):
    _, current, report = candidate
    report["validatedRefs"]["acceptance"] = binding
    assert not portable.reuse(report, current)["reusable"]
    assert acceptance_evidence(report) is None


def test_nonpassing_gate_cannot_reuse_an_old_candidate_or_scope(candidate):
    _, current, _ = candidate
    blocked = {
        "verdict": "BLOCKED",
        "recommendedNextAction": "blocked",
        "recoverableInCurrentRuntime": False,
        "subject": dict(current["subject"]),
        "scope": copy.deepcopy(current["scope"]),
        "remainingWork": [
            {
                "requirement": "AC-1",
                "gapType": "environment",
                "remainingWork": "Run the required check on its qualified CI route.",
            }
        ],
    }
    assert portable.validate_report(blocked, current)["valid"]

    stale = copy.deepcopy(blocked)
    stale["subject"]["contentDigest"] = "git-tree:old-candidate"
    assert "candidate" in " ".join(portable.validate_report(stale, current)["errors"])

    changed_scope = copy.deepcopy(blocked)
    changed_scope["scope"]["sourceDigest"] = "sha256:old-issue"
    assert "scope" in " ".join(portable.validate_report(changed_scope, current)["errors"])


def test_nonpassing_gate_cannot_import_optional_or_other_issue_work(candidate):
    _, current, _ = candidate
    blocked = {
        "verdict": "BLOCKED",
        "recommendedNextAction": "blocked",
        "recoverableInCurrentRuntime": False,
        "subject": dict(current["subject"]),
        "scope": copy.deepcopy(current["scope"]),
        "remainingWork": [
            {
                "requirement": "FULL-RELEASE-GPU",
                "gapType": "environment",
                "remainingWork": "Run an unrelated release certification.",
            }
        ],
    }
    assert "mandatory requirement" in " ".join(
        portable.validate_report(blocked, current)["errors"]
    )


def test_partial_report_retains_only_same_candidate_verified_rows(candidate):
    _, current, _ = candidate
    checks = [
        {"id": "AC-1", "executableChecks": ["python -m pytest tests/test_value.py"]},
        {"id": "AC-2", "executableChecks": ["python -m pytest tests/test_callable.py"]},
    ]
    current["verificationMap"] = copy.deepcopy(checks)
    partial = {
        "verdict": "BLOCKED",
        "subject": dict(current["subject"]),
        "scope": copy.deepcopy(current["scope"]),
        "verificationMap": copy.deepcopy(checks),
        "requirementCoverage": [
            {"id": "AC-1", "verificationStatus": "VERIFIED", "testEvidenceRefs": ["artifact://passing-test"]},
            {"id": "AC-2", "verificationStatus": "PARTIAL", "testEvidenceRefs": ["artifact://unproved"]},
            {"id": "FULL-RELEASE-GPU", "verificationStatus": "VERIFIED", "testEvidenceRefs": ["artifact://other-scope"]},
        ],
    }
    retained = portable.reuse(partial, current)
    assert not retained["reusable"] and not retained["completionEligible"]
    assert retained["candidateRows"] == [
        {"requirementId": "AC-1", "evidenceRefs": ["artifact://passing-test"]}
    ]

    changed = copy.deepcopy(current)
    changed["subject"]["contentDigest"] = "git-tree:changed"
    assert portable.reuse(partial, changed)["candidateRows"] == []

    changed_check = copy.deepcopy(current)
    changed_check["verificationMap"][0]["executableChecks"] = ["different check"]
    assert portable.reuse(partial, changed_check)["candidateRows"] == []


def test_assessment_checks_can_bind_only_the_original_scope(candidate):
    _, current, _ = candidate
    assessment = {
        "scope": copy.deepcopy(current["scope"]),
        "verificationMap": [
            {"id": "AC-1", "mandatory": True, "executableChecks": ["python -m pytest tests/test_value.py"]},
            {"id": "AC-2", "mandatory": True, "executableChecks": ["python -m pytest tests/test_callable.py"]},
        ],
    }
    bound = portable.bind_verification_map(current, assessment)
    assert [row["id"] for row in bound["verificationMap"]] == ["AC-1", "AC-2"]

    invented = copy.deepcopy(assessment)
    invented["verificationMap"].append(
        {"id": "FULL-RELEASE-GPU", "mandatory": True, "executableChecks": ["render all maps"]}
    )
    with pytest.raises(ValueError, match="original mandatory scope"):
        portable.bind_verification_map(current, invented)


def test_host_rejects_unrelated_blocker_and_shrunken_success_scope(candidate):
    _, current, success = candidate
    assessment = {"scope": copy.deepcopy(current["scope"])}
    blocked = {
        "verdict": "BLOCKED",
        "scope": copy.deepcopy(current["scope"]),
        "remainingWork": [
            {"requirement": "FULL-RELEASE-GPU", "gapType": "environment", "remainingWork": "Run release proof"}
        ],
    }
    assert "mandatory requirement" in " ".join(
        assessment_scope_violations(blocked, assessment)
    )
    blocked["remainingWork"][0]["requirement"] = "AC-1"
    assert assessment_scope_violations(blocked, assessment) == []

    success["validatedRefs"]["acceptance"]["scope"]["requirementIds"] = ["AC-1"]
    success["validatedRefs"]["acceptance"]["evidence"] = success["validatedRefs"]["acceptance"]["evidence"][:1]
    assert "original source scope" in " ".join(
        assessment_scope_violations(success, assessment)
    )
