"""A network plan over exported configurations, from Create to its conclusion (#5441).

The planning steps and the concluding ``security.assess`` run through the real
tool dispatcher, artifact service, sealing and report publication. The analyzer
job's evidence is retained exactly as its worker publishes it: the sanitized
export, the analyzer's answers recorded from the real Batfish image over the
fixture, and the worker-generated summary, linked to the run. Nothing in the
run holds network, credential or new target authority.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from moonmind.schemas.repository_scanner_models import NetworkConfigAssessment
from moonmind.security.assessment_projects import resolve_assessment_project
from moonmind.security.network_configs import prepare_exports, summarize_network_config
from moonmind.security.organisational_evidence import organisational_evidence_refs
from moonmind.security.security_plan import parse_security_plan
from moonmind.security.security_plan_run import conclusion_step
from moonmind.workflows.temporal.container_job_backend import _redact_strings
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security import test_security_plan_run_journey as planning

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment

FIXTURES = Path(__file__).parents[2] / "fixtures" / "security" / "network_config"
RUN = "network-plan-run"
STANDARDS = [
    {"id": "network-best-practices", "edition": "2026-11"},
    {"id": "pci-dss-readiness", "edition": "4.0.1"},
]
SECRETS = ("fixtureSalt", "fixtureHash", "fixture-community-ro")


def exported_now():
    """An export taken just now, so the point-in-time period is today's."""
    return (datetime.now(UTC) - timedelta(seconds=1)).replace(microsecond=0)


def exports(at):
    rows = json.loads((FIXTURES / "assessments.json").read_text())["corrected"][
        "exports"
    ]
    return [{**row, "exportedAt": at.isoformat()} for row in rows]


def project(at, **processing):
    day = at.date().isoformat()
    environment = {
        "id": "edge",
        "name": "Edge network",
        "assets": ["edge-rtr-1"],
        "artifactRefs": [row["artifactRef"] for row in exports(at)],
        "dataClassification": "confidential",
        "period": {"start": day, "end": day},
        **({"evidenceProcessing": processing} if processing else {}),
    }
    return resolve_assessment_project(
        {
            "projects": [
                {
                    "id": "network",
                    "name": "Network",
                    "purpose": "Exported configuration review",
                    "boundaries": "Edge devices whose exports the operator supplies",
                    "environments": [environment],
                }
            ]
        },
        project_id="network",
        environment_id="edge",
        version=1,
    )


def step_inputs(phase, at, **extra):
    return {
        "phase": phase,
        "subjectType": "network",
        "standards": copy.deepcopy(STANDARDS),
        "networkExports": exports(at),
        "assessmentProject": project(at).model_dump(mode="json", by_alias=True),
        **extra,
    }


def job_input(context):
    return {
        "exports": context["network"]["exports"],
        "flows": [
            {"host": "10.10.10.20", "port": 23},
            {"host": "10.10.10.20", "port": 443},
        ],
    }


def network_draft(context):
    """A controlled planner response: tagged requirements planned, others out."""
    refs = [row["artifactRef"] for row in context["network"]["exports"]]
    rows = []
    for standard in context["standards"]:
        for requirement in standard["requirements"]:
            row = {
                "packId": standard["packId"],
                "edition": standard["edition"],
                "id": requirement["id"],
                "sources": [{"kind": "document", "ref": "Pack subject tags"}],
            }
            if not requirement["selectedForSubject"]:
                row.update(
                    applicability="not_applicable",
                    reason="Not a network requirement of this standard.",
                )
            else:
                methods = requirement["allowedMethods"]
                row.update(
                    applicability="applicable",
                    reason="The supplied edge export is in scope.",
                    methods=methods,
                )
                if "deterministic_check" in methods:
                    row["jobIds"] = ["edge-config"]
                elif not {"agent_interpretation", "external_only"} & set(methods):
                    row["gaps"] = [
                        {
                            "kind": "missing_input",
                            "detail": "Operator evidence for this requirement.",
                        }
                    ]
            rows.append(row)
    project = context["project"]
    return {
        "schemaVersion": "security-plan/v1",
        "subject": {
            "type": "network",
            "project": {
                key: project[key]
                for key in ("projectId", "environmentId", "sourceVersion", "digest")
            },
            "candidateRef": "edge exports as supplied",
            "systemBoundary": "The edge router whose configuration export was supplied.",
            "scope": {"assets": refs, "exclusions": []},
            "sources": [
                {"kind": "environment_field", "ref": "environment.artifactRefs"}
            ],
        },
        "standards": [
            {
                key: standard[key]
                for key in (
                    "packId",
                    "edition",
                    "revision",
                    "sha256",
                    "period",
                    "artifactRef",
                )
                if key in standard
            }
            for standard in context["standards"]
        ],
        "requirements": rows,
        "jobs": [
            {
                "id": "edge-config",
                "kind": "network-config",
                "input": job_input(context),
                "assets": refs,
                "sources": [
                    {"kind": "operator_input", "ref": "Create: network exports"}
                ],
            }
        ],
    }


async def plan_network(service, at, monkeypatch):
    monkeypatch.setattr(
        planning, "step_inputs", lambda phase, **extra: step_inputs(phase, at, **extra)
    )
    context_outputs = await planning.invoke(
        service, step_inputs("context", at), key="context"
    )
    context = context_outputs["planningContext"]
    draft_ref = await recurring.put(
        service, network_draft(context), workflow_id=planning.WORKFLOW
    )
    outputs = await planning.invoke(
        service,
        step_inputs("retain", at, previousOutputs={"securityPlanDraftRef": draft_ref}),
        key="retain",
    )
    raw = await recurring.read(
        service, outputs["securityPlanRef"], principal=f"workflow:{planning.WORKFLOW}"
    )
    return context, outputs, raw


async def linked(service, name, payload, content_type="application/json"):
    """Container-job evidence as the worker publishes and links it to the run."""
    operator = recurring.access_for_test("operator")
    artifact, _ = await service.create(
        principal=operator.principal,
        content_type=content_type,
        link={
            "namespace": "default",
            "workflow_id": RUN,
            "run_id": "run",
            "link_type": "output.container-job",
        },
        metadata_json={"artifact_type": "container_job.evidence", "name": name},
        redaction_level="restricted",
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id, principal=operator.principal, payload=payload
    )
    # The worker's publisher delegates restricted evidence to its workflow.
    await service.delegate_read_access(
        artifact_ids=[artifact.artifact_id], workflow_id=RUN, access=operator
    )
    return artifact.artifact_id


async def analyzer_evidence(service, raw_plan):
    """What the planned network-config job's worker retains for this run."""
    settings = NetworkConfigAssessment.model_validate(raw_plan["jobs"][0]["input"])
    prepared = prepare_exports(
        settings,
        {e.file_name: (FIXTURES / e.file_name).read_bytes() for e in settings.exports},
    )
    refs = {
        name: await linked(
            service, f"job-output-export-{index}.txt", body, "text/plain"
        )
        for index, (name, body) in enumerate(prepared.sanitized.items())
    }
    # Redacted per string, as the worker does before publishing.
    native = _redact_strings(
        json.loads((FIXTURES / "native-corrected.json").read_text())
    )
    native_ref = await linked(
        service, "job-output-native.json", json.dumps(native, sort_keys=True).encode()
    )
    summary = summarize_network_config(
        settings, prepared, native, native_ref=native_ref, sanitized_refs=refs
    )
    summary_ref = await linked(
        service,
        "job-output-summary.json",
        summary.model_dump_json(by_alias=True).encode(),
    )
    return summary_ref, refs


async def conclude(service, plan_ref, identity, raw, at, key, **processing):
    plan = parse_security_plan(json.dumps(raw))
    step = conclusion_step(plan, plan_ref=plan_ref, identity=identity, excluded=[])
    await service.delegate_read_access(
        artifact_ids=organisational_evidence_refs({"steps": [step]}),
        workflow_id=RUN,
        access=recurring.access_for_test("operator"),
    )
    inputs = {
        **step["tool"]["inputs"],
        "assessmentProject": project(at, **processing).model_dump(
            mode="json", by_alias=True
        ),
    }
    return await recurring.invoke(service, inputs, workflow_id=RUN, key=key)


async def test_network_plan_over_exports_holds_no_authority(environment, monkeypatch):
    at = exported_now()
    context, outputs, raw = await plan_network(environment.service, at, monkeypatch)
    assert context["subjectType"] == "network"
    assert context["network"]["exports"][0]["device"] == "edge-corrected"
    [job] = raw["jobs"]
    assert job["authority"]["scoped"] is False
    assert job["authority"]["networkRefs"] == []
    assert job["authority"]["credentialPurposes"] == []
    assert {t["kind"] for t in job["authority"]["targets"]} == {"artifact"}
    _, text = await environment.service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test(f"workflow:{planning.WORKFLOW}"),
    )
    text = text.decode()
    for section in (
        "### Planned checks",
        "### Review procedures",
        "### Required inputs",
    ):
        assert section in text
    assert "Fixture network team" in text


@pytest.mark.parametrize("delivery", [None, "disabled"])
async def test_network_requirements_conclude_per_requirement_over_fixture_exports(
    environment, monkeypatch, delivery
):
    service = environment.service
    at = exported_now()
    _, outputs, raw = await plan_network(service, at, monkeypatch)
    plan_ref, identity = outputs["securityPlanRef"], outputs["securityPlanIdentity"]
    summary_ref, sanitized = await analyzer_evidence(service, raw)
    processing = {"modelDelivery": delivery} if delivery else {}
    result = await conclude(
        service, plan_ref, identity, raw, at, f"conclude-{delivery}", **processing
    )
    record = await recurring.read(
        service, result["securityPlanRunRef"], principal=f"workflow:{RUN}"
    )
    [job] = record["jobs"]
    assert (job["jobId"], job["state"], job["reportRefs"]) == (
        "edge-config",
        "completed",
        [summary_ref],
    )
    states = {row["requirementId"]: row for row in record["requirements"]}
    review = states["NET-6"]
    if delivery == "disabled":
        assert review["state"] == "not_assessed"
        assert "model delivery to disabled" in review["reason"]
    else:
        assert review["state"] == "review"
    assert states["12.1.1"]["state"] == "not_applicable"
    # Deterministic checks ran and conclude per requirement either way.
    report = await recurring.read(
        service, result["controlAssessmentRef"], principal=f"workflow:{RUN}"
    )
    rows = {row["requirement"]["id"]: row for row in report["requirements"]}
    for rid in ("NET-1", "NET-2", "NET-3", "NET-4", "NET-5"):
        assert rows[rid]["conclusion"] == "satisfied", (rid, rows[rid]["limitations"])
    assert rows["NET-6"]["conclusion"] != "satisfied"
    assert rows["NET-11"]["conclusion"] != "satisfied"
    pci_network = [rid for rid in rows if rid.startswith("1.")]
    assert pci_network and all(rows[rid]["conclusion"] for rid in pci_network)
    [entry] = [row for row in result["scannerSummaries"] if row["scanner"] == "batfish"]
    assert entry["summaryRef"] == summary_ref and entry["coverage"] == "complete"
    _, text = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test(f"workflow:{RUN}"),
    )
    text = text.decode()
    assert (
        "Fixture network team" in text and "did not observe the running device" in text
    )
    assert "Unverified" in text
    assert not [secret for secret in SECRETS if secret in text]
    assert not [secret for secret in SECRETS if secret in json.dumps(result)]
