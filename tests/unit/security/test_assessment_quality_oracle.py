"""Ground-truth evaluation of executed security scenarios (issue #5189)."""

import copy
import json

import pytest

from tests.integration.security import quality
from tests.integration.security.quality import evaluate, observation_digest, summarize


@pytest.fixture
def case():
    return {
        "outcome": "vulnerable",
        "findings": ["owner@app/routes.py"],
        "covered": ["app/routes.py"],
        "satisfied": False,
    }


@pytest.fixture
def observed(case):
    return {
        **copy.deepcopy(case),
        "evidenceByFinding": {"owner@app/routes.py": ["native"]},
        "executionEvidence": ["native"],
    }


def test_executed_case_measures_errors_gaps_and_traceability(case, observed):
    observed["findings"] = ["unexpected@other.py"]
    observed["covered"] = []
    observed["evidenceByFinding"] = {"unexpected@other.py": ["missing"]}
    result = evaluate(case, observed, {"native": b"{}"})
    assert result["passed"] is False
    assert result["metrics"] == {
        "falsePositives": 1,
        "missedDefects": 1,
        "incorrectSatisfiedConclusions": 0,
        "coverageGaps": 1,
        "traceableFindings": 0,
        "observedFindings": 1,
    }


@pytest.mark.parametrize("lie", ["clean", "repaired"])
def test_validly_shaped_false_clean_and_false_fixed_fail(case, observed, lie):
    observed.update(outcome=lie, findings=[], satisfied=True)
    # A readable valid report alone does not establish correct detection.
    result = evaluate(case, observed, {"native": json.dumps(observed).encode()})
    assert result["passed"] is False
    assert result["metrics"]["missedDefects"] == 1
    assert result["metrics"]["incorrectSatisfiedConclusions"] == 1


def test_no_execution_receipt_cannot_pass_an_otherwise_matching_result(case, observed):
    assert not evaluate(case, observed, {})["passed"]
    assert not evaluate(case, observed, {"native": b""})["passed"]
    observed["executionEvidence"] = []
    assert not evaluate(case, observed, {"native": b"{}"})["passed"]


def test_comparable_repaired_case_and_clean_control_pass(case, observed):
    assert evaluate(case, observed, {"native": b"{}"})["passed"]
    for outcome in ("clean", "repaired"):
        expected = {**case, "outcome": outcome, "findings": [], "satisfied": True}
        actual = {**observed, **expected}
        assert evaluate(expected, actual, {"native": b"{}"})["passed"]


def test_expected_partial_coverage_is_measured_and_disclosed(case, observed):
    case.update(scope=["app/routes.py", "unavailable.py"], coverageGaps=1)
    result = evaluate(case, observed, {"native": b"{}"})
    assert result["passed"]
    assert result["metrics"]["coverageGaps"] == 1


def test_reproducibility_uses_observations_not_run_specific_artifact_ids(observed):
    repeat = copy.deepcopy(observed)
    repeat["executionEvidence"] = ["another-run"]
    repeat["evidenceByFinding"] = {"owner@app/routes.py": ["another-run"]}
    assert observation_digest(repeat) == observation_digest(observed)
    repeat["findings"] = []
    assert observation_digest(repeat) != observation_digest(observed)


def test_corpus_summary_fails_missing_or_changed_execution_evidence(
    tmp_path, case, observed
):
    import hashlib

    manifest = {"cases": [{"id": "sample", "capability": "source", "expected": case}]}
    raw = json.dumps(manifest).encode()
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    output = tmp_path / "results"
    assert summarize(output, path)["missingCases"] == ["sample"]
    directory = output / "sample"
    directory.mkdir(parents=True)
    body = b"executed probe"
    (directory / "native.txt").write_bytes(body)
    result = {
        "runIdentity": "executed",
        **evaluate(case, observed, {"native": body}),
        "case": manifest["cases"][0],
        "observed": observed,
        "manifestDigest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "retainedArtifacts": {
            "native": {
                "path": "native.txt",
                "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
            }
        },
        "mutationChecks": {},
    }
    (directory / "result.json").write_text(json.dumps(result))
    assert summarize(output, path)["passed"]
    (directory / "native.txt").write_bytes(b"changed")
    assert not summarize(output, path)["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["repeat", "accepted-mutation", "missing-mutation"])
async def test_failed_quality_checks_are_recorded_as_failures(
    tmp_path, monkeypatch, case, observed, failure
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    expected = {"id": "sample", "expected": case}
    if failure == "missing-mutation":
        expected["requiredMutations"] = ["false-fixed"]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [expected]}))
    monkeypatch.setattr(quality, "MANIFEST", manifest)
    monkeypatch.setattr(quality, "OUTPUT", tmp_path / "output")
    service = SimpleNamespace(read=AsyncMock(return_value=(None, b"executed probe")))
    repeat = {**observed, "findings": []} if failure == "repeat" else None
    mutations = {"false-fixed": observed} if failure == "accepted-mutation" else None
    run = quality.QualityRun()
    run.useful()
    with pytest.raises(AssertionError):
        await run.finish(
            service,
            "sample",
            observed,
            ["native"],
            provenance={},
            repeat=repeat,
            mutations=mutations,
        )
    retained = json.loads((quality.OUTPUT / "sample/result.json").read_text())
    assert retained["passed"] is False
    assert not summarize(quality.OUTPUT, manifest)["passed"]


@pytest.mark.asyncio
async def test_exported_corpus_survives_ci_redaction(
    tmp_path, monkeypatch, case, observed
):
    import hashlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tools.ci.redact_diagnostics import redact_directory

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [{"id": "sample", "expected": case}]}))
    output = tmp_path / "output"
    monkeypatch.setattr(quality, "MANIFEST", manifest)
    monkeypatch.setattr(quality, "OUTPUT", output)
    native = json.dumps(
        {"credentialRef": "secret:fixture-grant", "token": "fixture-sensitive-value"}
    ).encode()
    service = SimpleNamespace(read=AsyncMock(return_value=(None, native)))
    await quality.QualityRun().finish(
        service,
        "sample",
        observed,
        ["native"],
        provenance={"configuration": {"credentialRef": "secret:fixture-grant"}},
    )

    # CI scrubs the entire upload directory after the producing container exits.
    redact_directory(output)

    assert summarize(output, manifest)["passed"]
    result = json.loads((output / "sample/result.json").read_text())
    retained = result["retainedArtifacts"]["native"]
    body = (output / "sample" / retained["path"]).read_bytes()
    assert retained["sourceSha256"] == "sha256:" + hashlib.sha256(native).hexdigest()
    assert retained["sha256"] == "sha256:" + hashlib.sha256(body).hexdigest()
    assert retained["bytes"] == len(body)
    assert json.loads(body)["token"] != "fixture-sensitive-value"
    assert b"fixture-sensitive-value" not in body


def test_historical_findings_require_comparable_scope_and_criteria():
    context = dict(
        build="synthetic-a",
        configuration="default",
        edition="4.0",
        criteria=["GLI-19:4"],
        period=["2026-11-04", "2026-11-07"],
        systems=["casino"],
    )
    historical = {"context": context, "findings": ["missed-rule"]}
    actual = {"context": copy.deepcopy(context), "findings": []}
    assert quality.reconcile_historical(historical, actual) == {
        "status": "comparable",
        "differences": [],
        "missedRequirements": ["missed-rule"],
    }
    for field in context:
        changed = copy.deepcopy(actual)
        changed["context"].pop(field)
        result = quality.reconcile_historical(historical, changed)
        assert result["status"] == "Unverified"
        assert result["differences"] == [field]
        assert result["missedRequirements"] == []
        changed["context"][field] = (
            ["different"] if isinstance(context[field], list) else "different"
        )
        result = quality.reconcile_historical(historical, changed)
        assert result["status"] == "Unverified"
        assert result["differences"] == [field]
        assert result["missedRequirements"] == []


def test_gaming_metrics_do_not_confuse_unknowns_with_verified_fixes(case, observed):
    observed.update(unknowns=["approval", "sampling"], verifiedRemediations=[])
    case.update(unknowns=["approval", "sampling"], verifiedRemediations=[])
    result = evaluate(case, observed, {"native": b"{}"})
    assert result["metrics"]["unknownCoverage"] == 2
    assert result["metrics"]["verifiedRemediations"] == 0
    assert result["metrics"]["detectedDefects"] == 1
    observed["unknowns"] = []
    observed["verifiedRemediations"] = ["owner@app/routes.py"]
    assert not evaluate(case, observed, {"native": b"{}"})["passed"]


def test_gaming_reproducibility_includes_gaps_and_fixes(observed):
    observed.update(unknowns=["approval"], verifiedRemediations=[])
    for key in ("unknowns", "verifiedRemediations"):
        changed = copy.deepcopy(observed)
        changed[key] = ["different"]
        assert observation_digest(changed) != observation_digest(observed)
