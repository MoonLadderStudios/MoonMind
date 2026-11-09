"""Synthetic gaming cases in the existing technical-validation corpus.

The daemon is controlled; real profile admission, CLI, artifact storage,
assessment and report owners run. No money or external targets are involved.
"""

import copy
import json
import re

import pytest

from tests.integration.security import test_game_math_jobs as math
from tests.integration.security import test_rgs_assessment_jobs as rgs
from tests.integration.security import test_rng_assessment_jobs as rng
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.quality import MANIFEST, QualityRun, observation
from tests.artifact_access import access_for_test

HELDOUT = json.loads(MANIFEST.with_name("gaming-heldout.json").read_text())

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
        if variant == "held-out":
            fixture = HELDOUT["rgs"]
            _, argv = await job.launch(
                case["id"],
                {"spec": "spec/spec.json", "traces": "traces/traces.json"},
                {
                    name: json.dumps(fixture[name]).encode()
                    for name in ("spec", "traces")
                },
            )
        else:
            _, argv, _ = await rgs.launch(
                job,
                case["id"],
                fault="double-debit" if variant == "defective" else None,
            )
    elif family == "rng":
        job = rng.Job(environment, tmp_path, monkeypatch)
        spec = rng.specification(mapped=True)
        raw = rng.RAW
        if variant == "held-out":
            fixture = HELDOUT["rng"]
            raw = bytes(b ^ 165 for b in rng.RAW)
            spec["expectedOutcomes"] = {
                str(i): "1/5" for i in range(fixture["outcomes"])
            }
            spec["mapped"]["originRef"] = "fixture:heldout-eleven-to-five"
            spec["mapped"]["transformations"] = ["xor 165; modulo 11; modulo 5"]
        elif variant == "insufficient-sample":
            raw = b"\x55"
            spec["raw"]["amount"] = 1
        outcomes = [
            str((b & 7) % 3)
            for b in raw
            if variant in {"defective", "held-out"} or (b & 7) < 6
        ]
        if variant == "held-out":
            outcomes = [
                str((b % fixture["modulus"]) % fixture["outcomes"]) for b in raw
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
        spec = copy.deepcopy(math.SPEC)
        if variant == "held-out":
            spec = copy.deepcopy(HELDOUT["math"]["spec"])
            settlements = HELDOUT["math"]["settlements"]
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
    native = json.loads(job.published[ref])
    plan = math.controls()
    requirement = plan["requirements"][0]
    requirement["scope"]["assets"] = [
        native["spec"]["gameRef"] if family == "rgs" else native["gameRef"]
    ]
    source = {"rgs": "rgs-scenarios", "rng": "rng", "math": "game-math"}[family]
    requirement["title"] = (
        f"Selected synthetic {family} diagnostic rules have no recorded violation"
    )
    objective = requirement["objectives"][0]
    objective["description"] = (
        "Check only the explicitly named fixture diagnostics, not deployed-game approval"
    )
    objective.update(source=source, tool=source, ruleIds=case["controlRules"])
    result = await job.assess(ref, case["id"] + "-assessment", controlAssessment=plan)
    control = await recurring.read(service, result["controlAssessmentRef"])
    conclusion = control["requirements"][0]["conclusion"]
    assert (
        conclusion == case["conclusion"]
    ), "native conclusion differs from ground truth"
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
        satisfied=conclusion == "satisfied",
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
    repeat = await job.assess(ref, case["id"] + "-repeat", controlAssessment=plan)
    repeat_control = await recurring.read(service, repeat["controlAssessmentRef"])

    assert repeat["counts"] == result["counts"]
    repeat_comparison = await recurring.read(service, repeat["comparisonRef"])
    repeated = copy.deepcopy(actual)
    repeated["satisfied"] = (
        repeat_control["requirements"][0]["conclusion"] == "satisfied"
    )
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
    assert re.findall(rb"Conclusion: \*\*([a-z_]+)\*\*", exported) == [
        conclusion.encode()
    ], "exported conclusion differs from retained control"
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
        [
            ref,
            result["scanRef"],
            result["comparisonRef"],
            result["controlAssessmentRef"],
            report_ref,
        ],
        provenance={
            "fixture": case["source"],
            "nativeInputDigests": native.get("inputDigests"),
            "limits": case["limits"],
        },
        repeat=repeated,
        mutations={
            "wrong-conclusion": {**actual, "satisfied": not actual["satisfied"]}
        },
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
            systems = HELDOUT["packs"]["gli"]["systems"]
            payload["scope"]["assets"] = systems
            payload["framework"]["systemInputs"]["systems"] = systems
            records = [
                {**copy.deepcopy(records[0]), "systems": [system]} for system in systems
            ]
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
            systems = HELDOUT["packs"]["nj"]["systems"]
            payload["scope"]["assets"] = systems
            payload["framework"]["systemInputs"]["systems"] = systems
            full_scope = copy.deepcopy(records)
            for row in full_scope:
                row["systems"] = systems
                row.pop("divisionApprovalRef", None)
                row.pop("operatorEvidenceRef", None)
            records += full_scope
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
            fixture = HELDOUT["packs"]["uk"]
            systems = fixture["systems"]
            payload["scope"]["assets"] = systems
            for system in payload["framework"]["systemInputs"]["criticalSystems"]:
                system["systems"] = systems
            for row in records:
                if row["method"] != "observation":
                    row["systems"] = systems
            records += [
                {
                    **copy.deepcopy(row),
                    "systems": [systems[1]],
                    "period": fixture["futurePeriod"],
                }
                for row in records
                if row["method"] == "observation"
            ]
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
        satisfied=native["readiness"] == "prepared",
    )
    actual.update(unknowns=unknowns, verifiedRemediations=[])
    report_ref = first["reportBundle"]["primary_report_ref"]["artifact_id"]
    _, exported = await service.read(
        artifact_id=report_ref, access=access_for_test("workflow:assessment")
    )
    assert first["frameworkReadiness"] == native["readiness"]
    assert re.findall(rb"Readiness: (prepared|incomplete)", exported) == [
        native["readiness"].encode()
    ], "exported readiness differs from retained framework"
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
        mutations={
            "wrong-conclusion": {**actual, "satisfied": not actual["satisfied"]}
        },
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


@pytest.mark.parametrize("boundary", ["producer", "export"])
async def test_gaming_corpus_rejects_false_prepared_headline(
    environment, monkeypatch, tmp_path, boundary
):
    from moonmind.security import gli_readiness
    from tests.integration.security import quality

    original = gli_readiness.prepare_assessment

    def false_pass(*args, **kwargs):
        result = original(*args, **kwargs)
        result["readiness"] = "prepared"
        return result

    monkeypatch.setattr(quality, "OUTPUT", tmp_path / "rejected-headline")
    if boundary == "producer":
        monkeypatch.setattr(gli_readiness, "prepare_assessment", false_pass)
    else:
        render = gli_readiness.render_assessment
        monkeypatch.setattr(
            gli_readiness,
            "render_assessment",
            lambda report: render(report).replace(
                "Readiness: incomplete", "Readiness: prepared"
            ),
        )
    case = next(c for c in CASES if c["id"] == "gaming-gli-defective")
    expected_failure = (
        "incorrectSatisfiedConclusions"
        if boundary == "producer"
        else "exported readiness"
    )
    with pytest.raises(AssertionError, match=expected_failure):
        await test_gaming_pack_scope_and_exports(environment, case)


@pytest.mark.parametrize("boundary", ["producer", "export"])
async def test_gaming_corpus_rejects_false_native_conclusion(
    environment, monkeypatch, tmp_path, boundary
):
    from moonmind.security import control_assessments
    from tests.integration.security import quality

    monkeypatch.setattr(quality, "OUTPUT", tmp_path / "rejected-control")
    if boundary == "producer":
        assess = control_assessments.assess_controls

        def false_pass(*args, **kwargs):
            report = assess(*args, **kwargs)
            report.requirements[0].conclusion = "satisfied"
            return report

        monkeypatch.setattr(control_assessments, "assess_controls", false_pass)
    else:
        render = control_assessments.render_control_assessment
        monkeypatch.setattr(
            control_assessments,
            "render_control_assessment",
            lambda report: render(report).replace("**not_satisfied**", "**satisfied**"),
        )
    case = next(c for c in CASES if c["id"] == "gaming-math-defective")
    expected_failure = (
        "native conclusion" if boundary == "producer" else "exported conclusion"
    )
    with pytest.raises(AssertionError, match=expected_failure):
        await test_native_gaming_corpus(environment, tmp_path, monkeypatch, case)
