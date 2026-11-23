"""Declared pack intelligence through the admitted Skill and artifact owners."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_control_assessment_journey import inputs
from tests.unit.security.test_framework_packs import pack_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def select_pack(service, request, pack=None):
    pack = pack or pack_payload()
    raw = json.dumps(pack).encode()
    ref = await recurring.put(service, raw)
    day = datetime.now(UTC).date().isoformat()
    request.pop("controlAssessment", None)
    request["frameworkPack"] = {
        "artifactRef": ref,
        "packId": pack["id"],
        "edition": pack["edition"],
        "revision": pack["revision"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "period": {"start": day, "end": day},
        "requirementChoices": {
            "control-1": {
                "applicability": "applicable",
                "applicabilityReason": "The synthetic requirement applies to the selected app.",
            }
        },
    }
    return request


async def test_declared_pack_produces_retained_requirement_report(environment):
    service = environment.service
    request = await select_pack(service, await inputs(service))
    result = await recurring.invoke(service, request)
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert report["requirements"][0]["conclusion"] == "satisfied"
    assert report["frameworkPack"]["revision"] == "1"
    assert (
        result["frameworkPack"]["artifactRef"]
        == request["frameworkPack"]["artifactRef"]
    )
    assert "requirements" not in result["frameworkPack"]
    assert "mappings" not in result["frameworkPack"]
    assert report["frameworkPack"]["sha256"] == request["frameworkPack"]["sha256"]
    assert request["frameworkPack"]["artifactRef"] in await recurring.required(
        service, result["controlAssessmentRef"]
    )
    _, summary = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"Framework pack: synthetic / 2026 / 1" in summary


async def test_pack_change_is_intelligence_not_a_new_scan_or_remediation(
    environment, monkeypatch
):
    service = environment.service
    request = await select_pack(service, await inputs(service))
    first = await recurring.invoke(service, request)
    pack = pack_payload()
    pack["revision"] = "2"
    pack["requirements"][0]["summary"] = "Updated original assessment summary"
    request = await select_pack(service, request, pack)
    request["frameworkPack"]["previousAssessmentRef"] = first["controlAssessmentRef"]

    def no_rescan(*args):
        raise AssertionError("Framework intelligence change repeated native work")

    monkeypatch.setattr(recurring.security_assessment, "observe_inventory", no_rescan)
    second = await recurring.invoke(service, request)
    assert second["scanRef"] == first["scanRef"]
    assert second["controlAssessmentRef"] != first["controlAssessmentRef"]
    report = await recurring.read(service, second["controlAssessmentRef"])
    assert report["frameworkPack"]["revision"] == "2"
    assert (
        report["frameworkPack"]["previousAssessmentRef"]
        == first["controlAssessmentRef"]
    )
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    assert any("revision" in row for row in report["intelligenceChanges"])
    assert any("requirementsDigest" in row for row in report["intelligenceChanges"])
    assert all("not remediation" in row for row in report["intelligenceChanges"])
    historical = await recurring.read(service, first["controlAssessmentRef"])
    assert historical["frameworkPack"]["revision"] == "1"
    assert historical["intelligenceChanges"] == []


async def test_restricted_pack_text_is_omitted_from_portable_reports(environment):
    service = environment.service
    pack = pack_payload()
    pack["distribution"] = "restricted"
    row = pack["requirements"][0]
    for key in ("title", "summary", "evidencePrompt", "objectiveBasis"):
        row[key] = "licensed-secret-marker"
    row["objectives"][0]["description"] = "licensed-secret-marker"
    request = await select_pack(service, await inputs(service), pack)
    result = await recurring.invoke(service, request)
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert "licensed-secret-marker" not in json.dumps(report)
    assert report["frameworkPack"]["textOmitted"]
    assert any("omitted" in row for row in report["diagnostics"])
    _, summary = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"licensed-secret-marker" not in summary


@pytest.mark.parametrize(
    "change",
    [
        "digest",
        "edition",
        "draft",
        "superseded",
        "expired",
        "future",
        "prohibited",
        "missing",
        "foreign",
    ],
)
async def test_invalid_or_inaccessible_pack_never_collects_native_work(
    environment, monkeypatch, change
):
    from moonmind.security.assessments import AssessmentRequest
    from moonmind.workflows.skills.security_assessment import AssessmentExecution
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
    )

    service = environment.service
    pack = pack_payload()
    if change in {"draft", "superseded"}:
        pack["publicationStatus"] = change
    elif change == "expired":
        pack["effectiveUntil"] = "2026-02-01"
    elif change == "future":
        pack["effectiveFrom"] = "2099-01-01"
    elif change == "prohibited":
        pack["distribution"] = "prohibited"
    request = await select_pack(service, await inputs(service), pack)
    if change == "digest":
        request["frameworkPack"]["sha256"] = "0" * 64
    elif change == "edition":
        request["frameworkPack"]["edition"] = "2025"
    elif change == "missing":
        request["frameworkPack"]["artifactRef"] = "art_missing_pack"
    elif change == "foreign":
        request["frameworkPack"]["artifactRef"] = await recurring.put(
            service, pack, workflow_id="foreign"
        )

    def no_scan(*args):
        raise AssertionError("Unadmitted pack started a native scan")

    monkeypatch.setattr(recurring.security_assessment, "observe_inventory", no_scan)
    execution = AssessmentExecution(
        service,
        principal="operator",
        context={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
        },
        access=recurring.access_for_test("workflow:assessment"),
    )
    with pytest.raises(
        (ToolFailure, TemporalArtifactAuthorizationError, TemporalArtifactNotFoundError)
    ):
        await execution.execute(AssessmentRequest.model_validate(request))


async def test_lost_history_is_reported_without_rewriting_current_evidence(environment):
    service = environment.service
    request = await select_pack(service, await inputs(service))
    request["frameworkPack"][
        "previousAssessmentRef"
    ] = "art_missing_previous_pack_report"
    result = await recurring.invoke(service, request)
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert report["requirements"][0]["conclusion"] == "satisfied"
    assert report["intelligenceChanges"] == []
    assert report["frameworkPack"]["previousAssessmentStatus"] == "unavailable"
    assert any("comparison is Unverified" in row for row in report["diagnostics"])


@pytest.mark.parametrize("fault", ["missing_bytes", "expired"])
async def test_old_evidence_loss_does_not_block_current_pack_assessment(
    environment, fault
):
    from datetime import timedelta

    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    service = environment.service
    old_request = await select_pack(service, await inputs(service))
    first = await recurring.invoke(service, old_request)
    old_input, _ = await service.read(
        artifact_id=old_request["inventoryRef"],
        access=recurring.access_for_test("workflow:assessment"),
    )
    if fault == "missing_bytes":
        LocalTemporalArtifactStore(environment.storage_root).delete(
            old_input.storage_key
        )
    else:
        from api_service.db.models import TemporalArtifact

        row = await environment.session.get(
            TemporalArtifact, old_request["inventoryRef"]
        )
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        await environment.session.commit()
    request = await select_pack(service, await inputs(service))
    request["frameworkPack"]["previousAssessmentRef"] = first["controlAssessmentRef"]
    result = await recurring.invoke(service, request)
    assert result["controlAssessmentStatus"] == "completed", result
    assert result["publicationOutcome"] == "published"
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert report["requirements"][0]["conclusion"] == "satisfied"
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    proof = await recurring.required(service, result["controlAssessmentRef"])
    assert first["controlAssessmentRef"] in proof
    assert old_request["inventoryRef"] not in proof


@pytest.mark.parametrize("count", [50, 350])
async def test_standard_size_through_retained_report(environment, count, monkeypatch):
    from time import perf_counter

    from moonmind.security import control_assessments
    from moonmind.security.assessment_reports import MAX_REPORT_BYTES
    from moonmind.security.assessments import MAX_RETAINED_REPORT_BYTES

    service = environment.service
    pack = pack_payload()
    template = pack["requirements"][0]
    pack["requirements"] = [
        dict(template, id=f"domain-{i // 50}.R{i}", category=f"domain-{i // 50}")
        for i in range(count)
    ]
    request = await select_pack(service, await inputs(service), pack)
    choices = request["frameworkPack"]["requirementChoices"]
    applicable = choices.pop("control-1")
    choices.update({row["id"]: applicable for row in pack["requirements"]})
    evaluate = control_assessments.assess_controls
    timings = []

    def timed(*args, **kwargs):
        start = perf_counter()
        report = evaluate(*args, **kwargs)
        timings.append(perf_counter() - start)
        return report

    monkeypatch.setattr(control_assessments, "assess_controls", timed)
    start = perf_counter()
    result = await recurring.invoke(service, request)
    elapsed = perf_counter() - start
    assert result["controlAssessmentCounts"]["satisfied"] == count, result
    access = recurring.access_for_test("workflow:assessment")
    _, structured = await service.read(
        artifact_id=result["controlAssessmentRef"], access=access
    )
    _, primary = await service.read(
        artifact_id=recurring.report_ref(result), access=access
    )
    assert (
        len(structured) <= MAX_RETAINED_REPORT_BYTES
        and len(primary) <= MAX_REPORT_BYTES
    )
    assert len(json.loads(structured)["requirements"]) == count
    assert all(row["id"].encode() in primary for row in pack["requirements"])
    assert len(timings) == 1
    from moonmind.workflows.skills.tool_plan_contracts import ToolResult
    from moonmind.workflows.temporal.data_converter import (
        MOONMIND_TEMPORAL_DATA_CONVERTER,
    )

    # Measure the result envelope carried by Temporal, including converter metadata.
    (output_payload,) = await MOONMIND_TEMPORAL_DATA_CONVERTER.encode(
        [ToolResult(status="COMPLETED", outputs=result).to_payload()]
    )
    if count == 350:
        assert b"requirements omitted" in primary
        assert (
            f"/api/artifacts/{result['controlAssessmentRef']}/download".encode()
            in primary
        )
    assert (
        sum(
            line.startswith(b"- domain-") and b"**satisfied**" in line
            for line in primary.splitlines()
        )
        == count
    )
    print(
        "framework size measurement",
        json.dumps(
            {
                "requirements": count,
                "domains": len({row["category"] for row in pack["requirements"]}),
                "packBytes": len(json.dumps(pack).encode()),
                "requestBytes": len(
                    json.dumps(request, separators=(",", ":")).encode()
                ),
                "activityOutputBytes": len(
                    json.dumps(result, separators=(",", ":")).encode()
                ),
                "temporalResultBytes": output_payload.ByteSize(),
                "structuredBytes": len(structured),
                "primaryBytes": len(primary),
                "evaluationSeconds": timings[0],
                "activitySeconds": elapsed,
            },
            sort_keys=True,
        ),
    )


@pytest.mark.parametrize("partial", [False, True])
async def test_three_packs_share_evidence_and_keep_local_conclusions(
    environment, monkeypatch, partial
):
    from collections import Counter
    from datetime import timedelta

    from moonmind.security import control_assessments
    from moonmind.workflows.skills.security_assessment import AssessmentExecution

    service = environment.service
    request = await inputs(service)
    original = pack_payload()
    await select_pack(service, request, original)
    previous = await recurring.invoke(service, request)
    native = (await recurring.read(service, previous["scanRef"]))["observation"]
    if partial:
        native["coverage"][0]["status"] = "partial"
    source_ref = await recurring.put(service, native)
    request.update(
        tools=["retained-report"],
        inventoryRef=None,
        feedRef=None,
        reportRefs=[source_ref],
    )

    original["revision"] = "2"
    original["mappingTargets"] = [
        {
            "id": "other",
            "frameworkId": "local",
            "edition": "2026",
            "requirementIds": ["control-1"],
        }
    ]
    claim = {
        "id": "M1",
        "fromRequirementIds": ["control-1"],
        "targetId": "other",
        "toRequirementIds": ["control-1"],
        "relation": "equivalent",
        "rationale": "Synthetic crosswalk",
        "source": original["source"],
        "basis": "upstream",
    }
    original["mappings"] = [
        claim,
        dict(claim, id="M2", relation="partial", rationale="Second interpretation"),
    ]
    local = dict(pack_payload(), id="local", distribution="restricted")
    local["requirements"][0]["title"] = "restricted local marker"
    third = dict(pack_payload(), id="policy")
    selections = []
    for pack in (original, local, third):
        await select_pack(service, request, pack)
        selection = request.pop("frameworkPack")
        selections.append(selection)
    selections[1]["period"]["start"] = (
        datetime.now(UTC).date() - timedelta(days=1)
    ).isoformat()
    selections[2]["requirementChoices"]["control-1"][
        "applicabilityReason"
    ] = "Local policy applies."
    request["frameworkPacks"] = selections
    request["previousAssessmentRef"] = previous["controlAssessmentRef"]
    request["oscalExport"] = {"models": ["catalog"]}
    request["evidenceExport"] = {"artifactRefs": [source_ref]}
    reads = Counter()
    read = AssessmentExecution.read

    async def count_read(self, ref):
        reads[ref] += 1
        return await read(self, ref)

    monkeypatch.setattr(AssessmentExecution, "read", count_read)
    cells = []
    observe = control_assessments._observe_cell

    def count_cell(observation, windows, now, source, tool, asset, rule):
        cells.append((source, tool, asset, rule))
        return observe(observation, windows, now, source, tool, asset, rule)

    monkeypatch.setattr(control_assessments, "_observe_cell", count_cell)
    result = await recurring.invoke(service, request)
    assert reads[source_ref] == 1
    assert reads[previous["controlAssessmentRef"]] == 1
    assert cells == [("fixture-advisories", "inventory-feed", "app", "CVE-EXAMPLE")]
    report = await recurring.read(service, result["controlAssessmentRef"])
    summaries = report["frameworkPacks"]
    assert [s["previousAssessmentStatus"] for s in summaries] == [
        "available",
        "initial",
        "initial",
    ]
    assert (
        summaries[0]["intelligenceChanges"] and not summaries[1]["intelligenceChanges"]
    )
    assert [s["textOmitted"] for s in summaries] == [False, True, False]
    assert [m["relation"] for m in summaries[0]["mappings"]] == [
        "equivalent",
        "partial",
    ]
    assert "restricted local marker" not in json.dumps(report)
    assert "Secret title" in json.dumps(summaries[0])
    assert (
        "Local policy applies."
        == report["requirements"][2]["requirement"]["applicabilityReason"]
    )
    expected = "inconclusive" if partial else "satisfied"
    for row, pack, conclusion in zip(
        report["requirements"],
        summaries,
        [expected, "inconclusive", expected],
        strict=True,
    ):
        assert row["conclusion"] == conclusion
        assert (
            pack["conclusionCounts"][conclusion]
            == pack["coverageCounts"][row["coverage"]]
            == 1
        )
        assert row["objectives"][0]["configurationRefs"]
    _, readable = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"restricted local marker" not in readable
    for pack in summaries:
        assert f"Framework pack: {pack['id']} / 2026".encode() in readable
    for key in ("oscalExport", "evidenceExport"):
        assert result[key]["status"] == "unsupported"
        assert "multiple" in json.dumps(result[key]["diagnostics"]).lower()
    assert {s["artifactRef"] for s in selections} <= await recurring.required(
        service, result["controlAssessmentRef"]
    )


async def test_second_invalid_pack_stops_before_collection(environment, monkeypatch):
    from moonmind.security.assessments import AssessmentRequest
    from moonmind.workflows.skills.security_assessment import AssessmentExecution
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    service = environment.service
    request = await select_pack(service, await inputs(service))
    first = request.pop("frameworkPack")
    await select_pack(
        service,
        request,
        dict(pack_payload(), id="expired", effectiveUntil="2026-01-02"),
    )
    request["frameworkPacks"] = [first, request.pop("frameworkPack")]
    execution = AssessmentExecution(
        service,
        principal="operator",
        context={"namespace": "default", "workflow_id": "assessment", "run_id": "run"},
        access=recurring.access_for_test("workflow:assessment"),
    )
    read = execution.read

    async def only_packs(ref):
        assert ref in {s["artifactRef"] for s in request["frameworkPacks"]}
        return await read(ref)

    monkeypatch.setattr(execution, "read", only_packs)
    with pytest.raises(ToolFailure, match="expired"):
        await execution.execute(AssessmentRequest.model_validate(request))


@pytest.mark.parametrize("bound", ["cells", "evidence", "choices", "selections"])
async def test_pack_bounds_name_field_and_limit_before_collection(environment, bound):
    from temporalio.exceptions import ApplicationError

    from tests.unit.security.test_control_assessments import organisational_request

    service = environment.service
    request = await inputs(service)
    pack = pack_payload()
    row = pack["requirements"][0]
    if bound == "cells":
        request["scope"] = {
            **request["scope"],
            "assets": [f"app-{i}" for i in range(256)],
        }
        row["objectives"][0]["ruleIds"] = [f"r-{i}" for i in range(32)]
        row["objectives"] = [dict(row["objectives"][0], id=f"o-{i}") for i in range(3)]
    await select_pack(service, request, pack)
    first = request.pop("frameworkPack")
    if bound == "evidence":
        # All immutable revisions, even superseded revisions, count toward the
        # reference bound. None need to be read to reject an oversized plan.
        evidence = []
        for i in range(9):
            req = organisational_request(id=f"e-{i}")
            req["revisions"] = [
                dict(req["revisions"][0], artifactRef=f"art_{i}_{j}") for j in range(8)
            ]
            evidence.append(req)
        first["requirementChoices"]["control-1"]["organisationalEvidence"] = evidence
    if bound == "choices":
        first["requirementChoices"] = {f"r{i}": {} for i in range(513)}
    request["frameworkPacks"] = [first] * (17 if bound == "selections" else 1)
    expected = {
        "cells": r"frameworkPacks\[0\].*objectives.*16384",
        "evidence": r"frameworkPacks\[0\].*evidence.*64",
        "choices": r"frameworkPacks\[0\].requirementChoices.*512",
        "selections": r"frameworkPacks.*16",
    }
    with pytest.raises(ApplicationError, match=expected[bound]):
        await recurring.invoke(service, request)


async def test_compare_previous_multi_pack_report_with_only_one_common_pack(
    environment,
):
    service = environment.service
    request = await inputs(service)
    selections = []
    for pack_id in ("first", "common"):
        await select_pack(service, request, dict(pack_payload(), id=pack_id))
        selections.append(request.pop("frameworkPack"))
    request["frameworkPacks"] = selections
    first = await recurring.invoke(service, request)
    request["frameworkPacks"] = [
        dict(selections[1], previousAssessmentRef=first["controlAssessmentRef"])
    ]
    second = await recurring.invoke(service, request)
    report = await recurring.read(service, second["controlAssessmentRef"])
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    assert report["frameworkPack"]["intelligenceChanges"] == []
    assert report["frameworkPack"]["previousOnlyFrameworks"] == [
        {"id": "first", "edition": "2026", "status": "unverified"}
    ]
    assert len(report["requirements"]) == 1


async def test_retained_single_selection_through_saved_rerun(environment, monkeypatch):
    from types import SimpleNamespace

    from api_service.api.routers import executions

    service = environment.service
    request = await select_pack(service, await inputs(service))
    first = await recurring.invoke(service, request)
    workflow = {
        "instructions": "Assess retained evidence",
        "tool": {"name": "security.assess", "inputs": request},
    }
    snapshot = executions._build_original_workflow_input_snapshot_payload(
        source_kind="create",
        payload={},
        task_payload=workflow,
    )
    ref = await recurring.put(service, snapshot)
    monkeypatch.setattr(
        executions, "get_temporal_artifact_service", lambda *a, **k: service
    )
    _, saved = await executions._snapshot_source_payload_from_parameters_and_artifact(
        session=environment.session,
        user=recurring.OperatorAdmission(via="test"),
        record=SimpleNamespace(input_ref=ref),
        parameters={},
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert saved["tool"]["inputs"] == request
    second = await recurring.invoke(service, saved["tool"]["inputs"], run_id="rerun")
    assert (
        second["controlAssessmentCounts"]
        == first["controlAssessmentCounts"]
        == {
            "satisfied": 1,
            "not_satisfied": 0,
            "inconclusive": 0,
            "not_assessed": 0,
        }
    )


async def test_combined_requirement_bound_applies_before_collection(environment):
    from temporalio.exceptions import ApplicationError

    service = environment.service
    request = await inputs(service)
    selections = []
    for pack_id, count in (("one", 512), ("two", 512), ("three", 1)):
        pack = dict(pack_payload(), id=pack_id)
        pack["requirements"] = [
            dict(pack["requirements"][0], id=f"r{i}") for i in range(count)
        ]
        await select_pack(service, request, pack)
        selection = request.pop("frameworkPack")
        selection.pop("requirementChoices")
        selections.append(selection)
    request["frameworkPacks"] = selections
    with pytest.raises(ApplicationError, match=r"frameworkPacks.requirements.*1024"):
        await recurring.invoke(service, request)


async def test_one_readiness_registration_lists_prepares_and_renders(environment, monkeypatch):
    from functools import partial
    from pydantic import Field

    from moonmind.security.builtin_frameworks import prepare_scoped_framework
    from moonmind.security.framework_registry import READINESS_PACKS, ReadinessPack, readiness_catalog
    from moonmind.security.frameworks import FrameworkContract, FrameworkEvidence, load_framework_pack

    class Inputs(FrameworkContract):
        systems: list[str]
        sample_size: int = Field(ge=1)

    pack = load_framework_pack("gli-19-readiness")
    pack.id = "fixture-readiness"

    def evaluate(selection, *, evidence, audit):
        return {
            "schemaVersion": "fixture/v1", "pack": pack.model_dump(mode="json", by_alias=True),
            "readiness": "incomplete", "evidenceRequests": [],
            "sampleSize": selection.system_inputs["sampleSize"],
        }

    entry = ReadinessPack(
        definition=lambda: pack, input_model=Inputs,
        prepare=partial(prepare_scoped_framework, prepare=evaluate, evidence_type=FrameworkEvidence),
        render=lambda report: f"Reviewed fixture sample of {report['sampleSize']}",
        sector=None, jurisdiction=None, external_evidence=(), conclusion="Fixture review",
    )
    monkeypatch.setitem(READINESS_PACKS, pack.id, entry)
    catalog = next(item for item in readiness_catalog() if item["id"] == pack.id)
    assert catalog["requirementCount"] == len(load_framework_pack(pack.id).requirements)
    assert catalog["inputSchema"]["properties"]["sampleSize"]["minimum"] == 1
    result = await recurring.invoke(environment.service, {
        "scope": {"assets": ["app"]}, "tools": [], "credentialRefs": [], "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "framework": {"packId": pack.id, "systemInputs": {"systems": ["app"], "sampleSize": 3},
                      "period": {"start": "2026-01-01", "end": "2026-01-31"}},
    })
    assert result["frameworkEdition"] == pack.edition
    assert result["publicationOutcome"] == "published"
    _, primary = await environment.service.read(
        artifact_id=result["reportBundle"]["primary_report_ref"]["artifact_id"],
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"Reviewed fixture sample of 3" in primary
