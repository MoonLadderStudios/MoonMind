"""RGS profile, artifact and retained-report boundaries (#5194).

The real admission service, CLI process, output collection and security.assess
run. Docker is a daemon fixture; these tests do not claim a live deployment.
"""

from __future__ import annotations

import hashlib
import json
import signal
import tarfile
from io import BytesIO

import pytest

from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from tests.fixtures.security.rgs_reference import reference_traces
from tests.integration.security import test_game_math_jobs as game_jobs
from tests.integration.security.test_recurring_assessments import invoke
from tests.unit.security.test_rgs_assessment import inputs

environment = game_jobs.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OUTPUT = "rgs-assessment.json"
GAME = "game:rgs-fixture"
PROFILE = {
    "profileRef": "rgs-scenarios",
    "imageSourceRef": "moonmind",
    "entrypoint": ["python", "-m", "moonmind.security.rgs_assessment"],
    "modes": [
        {
            "name": "evaluate",
            "actionClass": "rgs-scenarios",
            "argv": [
                "--spec={spec}",
                "--traces={traces}",
                "--max-operations={maxOperations}",
                "--max-seconds={maxSeconds}",
                f"--output=/outputs/{OUTPUT}",
            ],
            "arguments": [
                {"name": "spec", "type": "input", "required": True},
                {"name": "traces", "type": "input", "required": True},
                {
                    "name": "maxOperations",
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 4096,
                },
                {"name": "maxSeconds", "type": "integer", "minimum": 1, "maximum": 300},
            ],
        }
    ],
    "outputs": [{"name": "assessment", "relativePath": OUTPUT}],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 1024,
    "maxTimeoutSeconds": 600,
}


class Job(game_jobs.Job):
    profile_ref = "rgs-scenarios"
    result_schema = b'"rgs-assessment/v1"'

    def __init__(self, *args):
        super().__init__(*args)
        self.settings = resolve_container_backend_settings(
            {
                IMAGE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "sourceRef": "moonmind",
                            "image": "ghcr.io/moonladderstudios/moonmind:latest",
                        }
                    ]
                ),
                WORKLOAD_PROFILES_ENV_KEY: json.dumps([PROFILE]),
            }
        )

    async def launch(self, *args, **kwargs):
        result = await super().launch(*args, **kwargs)
        # The operator admits the original inputs to this assessment execution,
        # just as it admits the collected report. A reference inside JSON alone
        # never grants the assessment permission to read another artifact.
        for attachment in self.request.request.spec.input_attachments:
            await self.environment.service.link_artifact(
                artifact_id=attachment.artifact_ref,
                principal=game_jobs.owner_artifact_principal(game_jobs.OWNER),
                execution_ref={
                    "namespace": "default",
                    "workflow_id": "assessment",
                    "run_id": "run",
                    "link_type": "input.security",
                },
            )
        return result

    async def assess(self, ref, key, **options):
        return await invoke(
            self.environment.service,
            {
                "scope": {"assets": [GAME]},
                "tools": ["retained-report"],
                "reportRefs": [ref],
                **options,
                "credentialRefs": [],
                "networkRefs": [],
                "budget": {
                    "baseSeconds": 60,
                    "maxSeconds": 120,
                    "progressStallSeconds": 60,
                },
            },
            key=key,
        )


async def launch(job, key, *, fault=None, arguments=None):
    spec, _ = inputs()
    traces = reference_traces(spec, fault=fault)
    payloads = {
        "spec": spec.model_dump_json(by_alias=True).encode(),
        "traces": traces.model_dump_json(by_alias=True).encode(),
    }
    create, argv = await job.launch(
        key,
        {"spec": "spec/spec.json", "traces": "traces/traces.json", **(arguments or {})},
        payloads,
    )
    return create, argv, payloads


@pytest.mark.parametrize(
    "fault,expected",
    [
        (None, "consistent"),
        ("double-debit", "violations"),
        ("double-credit", "violations"),
        ("missing-audit", "violations"),
    ],
)
async def test_synthetic_target_through_profile_artifacts_and_retained_report(
    environment, tmp_path, monkeypatch, fault, expected
):
    job = Job(environment, tmp_path, monkeypatch)
    create, argv, payloads = await launch(
        job, "rgs-" + (fault or "reference"), fault=fault
    )
    assert create[create.index("--network") + 1] == "none"
    process = job.process(argv)
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    report = json.loads(job.published[ref])
    assert report["outcome"] == expected
    assert report["spec"]["sourceRef"] == "fixture:rgs-contract"
    assert report["inputDigests"] == {
        name: "sha256:" + hashlib.sha256(body).hexdigest()
        for name, body in payloads.items()
    }
    assert len(report["job"]["inputRefs"]) == 2
    assert report["retest"][:3] == ["python", "-m", "moonmind.security.rgs_assessment"]
    assert report["plannedOperations"] == report["completedOperations"] == 23
    assert all(row["evidenceRefs"] for row in report["checks"])
    outputs = await job.assess(ref, "rgs-report-" + (fault or "reference"))
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["scanner"], reviewed["target"], reviewed["outcome"]) == (
        "rgs-scenarios",
        GAME,
        expected,
    )
    assert reviewed["coverage"] == "complete"
    assert outputs["coverageStatus"] == "complete"
    assert (outputs["counts"]["new"] > 0) == bool(fault)
    assert {"transport_not_exercised", "authorization_matrix_not_exercised"} <= set(
        reviewed["limitations"]
    )
    dependencies = await game_jobs.recurring.required(
        environment.service, outputs["scanRef"]
    )
    assert {ref, *report["job"]["inputRefs"].values()} <= dependencies


async def test_operation_budget_and_retest_preserve_financial_evidence(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv, _ = await launch(job, "rgs-budget", arguments={"maxOperations": 4})
    process = job.process(argv)
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    partial = json.loads(job.published[ref])
    assert (partial["status"], partial["outcome"], partial["completedOperations"]) == (
        "partial",
        "incomplete",
        4,
    )
    outputs = await job.assess(ref, "rgs-budget-report")
    assert outputs["coverageStatus"] == "stale_or_partial"
    assert (
        outputs["scannerSummaries"][0]["execution"]["scenarios"] == "operation_budget"
    )
    assert outputs["counts"]["new"] == 0

    # The exact same captured inputs can be evaluated again with enough budget.
    # Neither invocation has credentials or a transport capable of repeating
    # a financial effect; the synthetic wallet trace remains unchanged.
    process = job.process(
        [arg for arg in argv if not arg.startswith("--max-operations=")]
    )
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    complete = json.loads((job.storage.outputs / OUTPUT).read_bytes())
    assert complete["outcome"] == "consistent"
    assert complete["inputDigests"] == partial["inputDigests"]
    assert complete["checks"][:4] == partial["checks"]


@pytest.mark.parametrize("terminal", ["failed", "canceled", "timed_out"])
async def test_adverse_job_outcome_cannot_be_hidden_by_complete_file(
    environment, tmp_path, monkeypatch, terminal
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv, _ = await launch(job, "rgs-adverse-" + terminal)
    process = job.process(argv)
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state=terminal, exit_code=None)
    report = json.loads(job.published[ref])
    assert (report["status"], report["outcome"]) == ("partial", "incomplete")
    assert report["completedOperations"] == report["plannedOperations"]
    assert "job_did_not_succeed" in report["limitations"]
    outputs = await job.assess(ref, "rgs-adverse-report-" + terminal)
    assert outputs["coverageStatus"] == "stale_or_partial"


@pytest.mark.parametrize(
    "sig,terminal", [(signal.SIGTERM, "canceled"), (signal.SIGKILL, "failed")]
)
async def test_interrupted_process_keeps_an_atomic_partial_report(
    environment, tmp_path, monkeypatch, sig, terminal
):
    job = Job(environment, tmp_path, monkeypatch)
    spec, traces = inputs()
    # Many independent traces keep the process busy while we stop it. The
    # maximum admitted workload is still small and all inputs are synthetic.
    original_spec, original_trace = spec.scenarios[0], traces.traces[0]
    spec.scenarios, traces.traces = [], []
    for i in range(32):
        scenario, trace = (
            original_spec.model_copy(deep=True),
            original_trace.model_copy(deep=True),
        )
        scenario.id = trace.scenario = "stop-" + str(i)
        spec.scenarios.append(scenario)
        traces.traces.append(trace)
    _, argv = await job.launch(
        "rgs-stop-" + str(sig),
        {"spec": "spec/spec.json", "traces": "traces/traces.json"},
        {
            "spec": spec.model_dump_json(by_alias=True).encode(),
            "traces": traces.model_dump_json(by_alias=True).encode(),
        },
    )
    process = job.process(argv)
    assert b"started" in process.stderr.readline()
    process.send_signal(sig)
    process.communicate(timeout=30)
    assert process.returncode == (0 if sig == signal.SIGTERM else -signal.SIGKILL)
    ref = await job.publish(terminal_state=terminal, exit_code=None)
    report = json.loads(job.published[ref])
    assert report["status"] == "partial"
    assert report["outcome"] == "incomplete"
    assert report["completedOperations"] < report["plannedOperations"]
    assert report["spec"]["scenarios"]
    assert report["tracesDigest"]


async def test_profile_cannot_substitute_an_uncaptured_trace_path(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        await job.launch(
            "rgs-escape",
            {"spec": "spec/spec.json", "traces": "../../not-admitted.json"},
            {"spec": b"{}"},
        )


async def test_workspace_input_capture_is_retained_before_binding_the_report(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv, payloads = await launch(job, "rgs-workspace-input")
    # Workspace and artifact attachments share this captured directory contract.
    # Exercise collection with a workspace input whose live source is unavailable.
    spec = job.request.request.spec
    spec.input_attachments = [
        (
            attachment.model_copy(
                update={"artifact_ref": None, "workspace_path": "lost-source"}
            )
            if attachment.name == "spec"
            else attachment
        )
        for attachment in spec.input_attachments
    ]
    process = job.process(argv)
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    report = json.loads(job.published[ref])
    input_ref = report["job"]["inputRefs"]["spec"]
    with tarfile.open(
        fileobj=BytesIO(job.published[input_ref]), mode="r:gz"
    ) as archive:
        assert archive.extractfile("spec.json").read() == payloads["spec"]
    outputs = await job.assess(ref, "rgs-workspace-report")
    assert outputs["scannerSummaries"][0]["outcome"] == "consistent"
    assert input_ref in await game_jobs.recurring.required(
        environment.service, outputs["scanRef"]
    )


@pytest.mark.parametrize("violations", [4096, 4097])
async def test_large_rgs_reports_retain_bounded_findings_and_explicit_coverage(
    environment, tmp_path, monkeypatch, violations
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv, _ = await launch(job, f"rgs-many-findings-{violations}")
    process = job.process(argv)
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    report_path = job.storage.outputs / OUTPUT
    report = json.loads(report_path.read_bytes())
    report["checks"].extend(
        {
            "scenario": report["spec"]["scenarios"][0]["id"],
            "operation": f"unexpected-transaction-{index}",
            "rule": "wallet-effect",
            "expected": "absent",
            "observed": "extra debit",
            "decision": "violation",
            "evidenceRefs": ["fixture:extra-wallet-effects"],
        }
        for index in range(violations)
    )
    report["outcome"] = "violations"
    report_path.write_text(json.dumps(report))
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    outputs = await job.assess(ref, f"rgs-many-findings-report-{violations}")
    (summary,) = outputs["scannerSummaries"]
    assert outputs["counts"]["new"] == min(violations, 4096)
    assert summary["outcome"] == "violations"
    assert summary["coverage"] == ("partial" if violations > 4096 else "complete")
    assert ("finding_import_limit" in summary["limitations"]) == (violations > 4096)
    assert (
        sum(
            row["decision"] == "violation"
            for row in json.loads(job.published[ref])["checks"]
        )
        == violations
    )
