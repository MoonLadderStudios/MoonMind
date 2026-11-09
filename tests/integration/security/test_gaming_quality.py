"""Synthetic gaming cases in the existing technical-validation corpus.

The daemon is controlled; real profile admission, CLI, artifact storage,
assessment and report owners run. No money or external targets are involved.
"""

import copy
import json

import pytest

from tests.integration.security import test_game_math_jobs as math
from tests.integration.security import test_rgs_assessment_jobs as rgs
from tests.integration.security import test_rng_assessment_jobs as rng
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.quality import MANIFEST, QualityRun, observation
from tests.artifact_access import access_for_test

environment = recurring.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
CASES = [
    c
    for c in json.loads(MANIFEST.read_text())["cases"]
    if c["capability"].startswith("gaming-")
]


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["capability"] == "gaming-native"],
    ids=lambda c: c["id"],
)
async def test_native_gaming_corpus(environment, tmp_path, monkeypatch, case):
    run = QualityRun()
    service = environment.service
    family, variant = case["family"], case["variant"]
    if family == "rgs":
        job = rgs.Job(environment, tmp_path, monkeypatch)
        fault = {"defective": "double-debit", "held-out": "missing-audit"}.get(variant)
        _, argv, _ = await rgs.launch(job, case["id"], fault=fault)
    elif family == "rng":
        job = rng.Job(environment, tmp_path, monkeypatch)
        spec = rng.specification(mapped=True)
        raw = rng.RAW
        if variant == "held-out":
            raw = bytes([0]) * len(raw)
        elif variant == "insufficient-sample":
            raw = b"\x55"
            spec["raw"]["amount"] = 1
        outcomes = [
            str((b & 7) % 3)
            for b in raw
            if variant in {"defective", "held-out"} or (b & 7) < 6
        ]
        spec["mapped"]["amount"] = len(outcomes)
        _, argv = await job.launch(
            case["id"],
            {
                "spec": "spec/spec.json",
                "raw": "raw/raw.json",
                "outcomes": "outcomes/outcomes.json",
            },
            {
                "spec": json.dumps(spec).encode(),
                "raw": raw,
                "outcomes": "\n".join(outcomes).encode(),
            },
        )
    else:
        job = math.Job(environment, tmp_path, monkeypatch)
        settlements = copy.deepcopy(math.SETTLEMENTS)
        if variant in {"clean", "corrected"}:
            settlements[1].update(payouts=["0"], total="0")
        elif variant == "held-out":
            settlements = [{**settlements[0], "payouts": ["0"], "total": "0"}]
        spec = copy.deepcopy(math.SPEC)
        spec["provenance"].pop("approvalRef")
        _, argv = await job.launch(
            case["id"],
            {"spec": "spec/spec.json", "settlements": "settlements/settlements.json"},
            {
                "spec": json.dumps(spec).encode(),
                "settlements": "\n".join(map(json.dumps, settlements)).encode(),
            },
        )
    process = job.process(argv)
    _, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    result = await job.assess(ref, case["id"] + "-assessment")
    compared = await recurring.read(service, result["comparisonRef"])
    native = json.loads(job.published[ref])
    # Evaluate actual assessment findings, not the variant label or fixture expectation.
    findings = sorted({r["finding"]["ruleId"] for r in compared["findings"]})
    actual = observation(
        "vulnerable"
        if findings
        else ("clean" if result["coverageStatus"] == "complete" else "incomplete"),
        findings,
        [family] if result["coverageStatus"] == "complete" else [],
        native_ref=ref,
        satisfied=False,
    )
    if family == "rgs":
        unknowns = [
            name
            for name, code in [
                ("transport", "transport_not_exercised"),
                ("authorization", "authorization_matrix_not_exercised"),
            ]
            if code in native["limitations"]
        ]
    elif family == "rng":
        unknowns = [
            name
            for name, field in [
                ("cryptographic-security", "cryptographicSecurity"),
                ("certification", "certification"),
                ("game-fairness", "gameFairness"),
            ]
            if native[field] in {"unverified", "not_performed"}
        ]
    else:
        unknowns = []
        if "approval_reference_absent" in native["limitations"]:
            unknowns.append("approval")
        if native.get("simulation") is None:
            unknowns.append("sampling")
        if native["certification"] == "not_performed":
            unknowns.append("deployed-implementation")
    if family == "rng" and any(
        row["decision"] == "insufficient_sample"
        for stage in ("raw", "mapped")
        for row in native[stage]["results"]
    ):
        unknowns.append("sampling")
    actual.update(unknowns=unknowns, verifiedRemediations=[])
    # Replay the actual assessment owner with the same retained inputs.
    repeat = await job.assess(ref, case["id"] + "-repeat")
    assert repeat["counts"] == result["counts"]
    repeat_comparison = await recurring.read(service, repeat["comparisonRef"])
    repeated = copy.deepcopy(actual)
    repeated["findings"] = sorted(
        {r["finding"]["ruleId"] for r in repeat_comparison["findings"]}
    )
    repeated["covered"] = [family] if repeat["coverageStatus"] == "complete" else []
    repeated["outcome"] = (
        "vulnerable"
        if repeated["findings"]
        else ("clean" if repeat["coverageStatus"] == "complete" else "incomplete")
    )
    report_ref = recurring.report_ref(result)
    _, exported = await service.read(
        artifact_id=report_ref, access=access_for_test("workflow:assessment")
    )
    assert exported and result["publicationOutcome"] == "published"
    export_limits = {
        "rgs": [b"transport_not_exercised", b"authorization_matrix_not_exercised"],
        "rng": [b"Unverified", b"cryptographic", b"game fairness"],
        "math": [b"approval_reference_absent", b"not_requested", b"does not certify"],
    }
    assert all(token in exported for token in export_limits[family])
    if variant == "insufficient-sample":
        assert b"insufficient_sample" in exported

    run.useful()
    await run.finish(
        service,
        case["id"],
        actual,
        [ref, result["scanRef"], result["comparisonRef"], report_ref],
        provenance={
            "fixture": case["source"],
            "nativeInputDigests": native.get("inputDigests"),
            "limits": case["limits"],
        },
        repeat=repeated,
        mutations={"false-pass": {**actual, "findings": [], "satisfied": True}},
    )


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["capability"] == "gaming-pack"],
    ids=lambda c: c["id"],
)
async def test_gaming_pack_scope_and_exports(environment, case):
    from tests.unit.security import test_gli_readiness as gli
    from tests.unit.security import test_nj_dge as nj
    from tests.unit.security import test_ukgc_rts as uk
    from tests.integration.security.test_nj_dge_readiness_journey import materialize

    run = QualityRun()
    service = environment.service
    family, variant = case["family"], case["variant"]
    if family == "gli":
        data = gli.fixture()
        records = [data["technicalEvidence"]]
        payload = gli.request_payload()
        if variant == "defective":
            records[0]["outcome"] = "failed"
        elif variant == "unsupported-adoption":
            payload["framework"]["systemInputs"]["jurisdictionBasis"]["edition"] = (
                "synthetic-other-edition"
            )
        elif variant == "held-out":
            records[0]["edition"] = "synthetic-other-edition"
    elif family == "nj":
        data, _ = await materialize(service)
        # Already materialized approval and partner references remain real artifacts.
        records = [
            await recurring.read(service, ref)
            for ref in data["framework"]["evidenceRefs"]
        ]
        payload = nj.request_payload(data)
        if variant == "defective":
            for row in records:
                row.pop("divisionApprovalRef", None)
                row.pop("operatorEvidenceRef", None)
        elif variant == "held-out":
            # A release/configuration outside the assessed system cannot inherit evidence.
            for row in records:
                row["systems"] = ["old-release"]
    else:
        data = uk.fixture()
        records = data["evidence"]
        data["audit"]["auditor"]["independenceRef"] = await recurring.put(
            service, {"statement": "Synthetic independence"}
        )
        payload = uk.request_payload()
        payload["framework"]["auditRef"] = await recurring.put(service, data["audit"])
        if variant == "defective":
            records = [r for r in records if r["method"] != "observation"]
        elif variant == "held-out":
            for row in records:
                row["period"] = {"start": "2025-01-01", "end": "2025-12-31"}
    refs = [await recurring.put(service, row) for row in records]
    payload["framework"]["evidenceRefs"] = refs
    first = await recurring.invoke(service, payload, key=case["id"])
    original = await service.read(
        artifact_id=first["frameworkRef"], access=access_for_test("workflow:assessment")
    )
    native = json.loads(original[1])
    assert native["pack"]["edition"] == case["edition"]
    measured = {
        r["id"]: r for r in native["requirements"] if r["id"] in case["requirements"]
    }
    unknowns = sorted(k for k, row in measured.items() if row["status"] == "incomplete")
    covered = sorted(
        k for k, row in measured.items() if row["status"] == "evidence_recorded"
    )
    if any(r["id"] == "jurisdiction.adoption" for r in native["evidenceRequests"]):
        unknowns.append("jurisdiction.adoption")
    actual = observation(
        "incomplete" if unknowns else "clean",
        [],
        covered,
        native_ref=first["frameworkRef"],
        satisfied=False,
    )
    actual.update(unknowns=unknowns, verifiedRemediations=[])
    report_ref = first["reportBundle"]["primary_report_ref"]["artifact_id"]
    _, exported = await service.read(
        artifact_id=report_ref, access=access_for_test("workflow:assessment")
    )
    assert b"Unverified" in exported
    for requirement in unknowns:
        assert requirement.encode() in exported and b"incomplete" in exported
    # A later assessment must not rewrite the first report or evidence bytes.
    payload["framework"]["evidenceRefs"] = []
    later = await recurring.invoke(service, payload, key=case["id"] + "-changed")
    assert later["frameworkRef"] != first["frameworkRef"]
    assert (
        await service.read(
            artifact_id=first["frameworkRef"],
            access=access_for_test("workflow:assessment"),
        )
    )[1] == original[1]
    assert (
        await service.read(
            artifact_id=report_ref, access=access_for_test("workflow:assessment")
        )
    )[1] == exported
    run.useful()
    await run.finish(
        service,
        case["id"],
        actual,
        [*refs, first["frameworkRef"], report_ref],
        provenance={
            "pack": native["pack"],
            "scope": payload["scope"],
            "period": payload["framework"]["period"],
            "source": case["source"],
        },
        mutations={"false-pass": {**actual, "satisfied": True}},
    )


async def test_optional_redacted_historical_fixture(environment):
    from tests.integration.security.quality import reconcile_historical

    service = environment.service
    source = MANIFEST.with_name("gaming-historical.json").read_bytes()
    reference = json.loads(source)["historical"]
    archived_ref = await recurring.put(service, source)
    findings = []
    comparisons = []
    for dimension in [None, *reference["context"]]:
        current = copy.deepcopy(reference)
        current["findings"] = []
        if dimension:
            current["context"].pop(dimension)
        result = reconcile_historical(reference, current)
        comparisons.append({"current": current, "result": result})
        assert result["status"] == ("Unverified" if dimension else "comparable")
        assert result["differences"] == ([dimension] if dimension else [])
        if dimension:
            assert result["missedRequirements"] == []
        else:
            findings = result["missedRequirements"]
    result_ref = await recurring.put(service, comparisons)
    assert (
        await service.read(
            artifact_id=archived_ref, access=access_for_test("workflow:assessment")
        )
    )[1] == source
    actual = observation(
        "vulnerable", findings, ["historical-comparison"], native_ref=result_ref
    )
    actual.update(unknowns=["external-laboratory"], verifiedRemediations=[])
    await QualityRun().finish(
        service,
        "gaming-historical-reconciliation",
        actual,
        [archived_ref, result_ref],
        provenance={
            "source": "synthetic redacted archive",
            "context": reference["context"],
        },
    )
