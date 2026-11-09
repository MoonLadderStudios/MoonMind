"""Game math assessments as workload-profile container jobs (#5199).

The deployment's ``game-math`` workload profile runs the production
``moonmind.security.game_math`` CLI from a MoonMind image. Admission and
profile rendering, input capture through the real artifact authority, launch
arguments, declared-output collection, artifact storage and ``security.assess``
are production code. The Docker daemon is a command fixture; the rendered
command runs as a local process with the job's input and output storage in
place of its mounts, and cancellation is the SIGTERM ``docker stop`` sends.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from temporalio.testing import ActivityEnvironment
from unittest.mock import AsyncMock

import pytest

from api_service.services.container_jobs import (
    ContainerJobService,
    owner_artifact_principal,
)
from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend
from moonmind.workflows.temporal.worker_runtime import _container_job_artifact_reader
from tests.integration.security import test_recurring_assessments as recurring
from moonmind.workflows.temporal.worker_runtime import (
    _container_job_evidence_publisher,
    _container_job_projection_writer,
)
from tests.integration.security.test_recurring_assessments import invoke
from tests.integration.security.test_web_assessment_jobs import (
    create_container_in_activity,
)

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OWNER = OwnerIdentity(principalType="user", principalId="operator")
IMAGE = "sha256:" + "d" * 64
REPOSITORY = Path(__file__).resolve().parents[3]
GAME = "game:crash-fixture"
OUTPUT = "game-math-assessment.json"

PROFILE = {
    "profileRef": "game-math",
    "imageSourceRef": "moonmind",
    "entrypoint": ["python", "-m", "moonmind.security.game_math"],
    "modes": [
        {
            "name": "evaluate",
            "actionClass": "game-math",
            "argv": [
                "--spec={spec}",
                "--settlements={settlements}",
                "--records={records}",
                "--samples={samples}",
                "--seed={seed}",
                "--max-seconds={maxSeconds}",
                f"--output=/outputs/{OUTPUT}",
            ],
            "arguments": [
                {"name": "spec", "type": "input", "required": True},
                {"name": "settlements", "type": "input"},
                {"name": "records", "type": "input"},
                {"name": "samples", "type": "integer", "minimum": 1},
                {"name": "seed", "type": "integer", "minimum": 0},
                {"name": "maxSeconds", "type": "integer", "minimum": 1},
            ],
        }
    ],
    "outputs": [{"name": "assessment", "relativePath": OUTPUT}],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 1024,
    "maxTimeoutSeconds": 3600,
}

# P(M >= t) = 0.99 / t with inclusive cashout: RTP 99/100 at every target.
SPEC = {
    "schemaVersion": "game-math-spec/v1",
    "gameRef": GAME,
    "provenance": {
        "sourceRef": "par:crash-fixture",
        "contentDigest": "sha256:" + "e" * 64,
        "approvalRef": "filing-5199",
    },
    "game": {
        "kind": "crash",
        "precision": "1/100",
        "boundary": "inclusive",
        "minTarget": "1.01",
        "survival": {"kind": "inverse", "houseEdge": "1/100"},
    },
    "wagers": [
        {"id": "low", "stake": "1", "target": "1.5"},
        {"id": "high", "stake": "1", "target": "2"},
    ],
    "strategies": [
        {"id": "cash-1.5", "bets": ["low"], "assumption": "auto cashout at 1.50x"},
        {"id": "cash-2", "bets": ["high"], "assumption": "auto cashout at 2.00x"},
    ],
    "settlement": {"quantum": "0.01", "rounding": "floor"},
    "declared": [
        {"strategy": "cash-1.5", "rtp": "0.99", "immediateBust": "2/101"},
        {"strategy": "cash-2", "rtp": "0.99", "immediateBust": "2/101"},
    ],
    "criteria": {"tolerance": "1/20", "confidence": "99/100"},
}
# A boundary tie settled correctly, and a bust the implementation paid.
SETTLEMENTS = [
    {"strategy": "cash-2", "multiplier": "2", "payouts": ["2"], "total": "2"},
    {"strategy": "cash-1.5", "multiplier": "1", "payouts": ["1.5"], "total": "1.5"},
]


def settings():
    return resolve_container_backend_settings(
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


async def owned(environment, payload: bytes) -> str:
    principal = owner_artifact_principal(OWNER)
    artifact, _ = await environment.service.create(
        principal=principal, content_type="application/json"
    )
    await environment.service.write_complete(
        artifact_id=artifact.artifact_id, principal=principal, payload=payload
    )
    return artifact.artifact_id


class Job:
    """One admitted profile job, launched up to ``docker create``."""

    profile_ref = "game-math"
    result_schema = b'"game-math-assessment/v1"'

    def __init__(self, environment, tmp_path, monkeypatch):
        self.environment, self.tmp_path = environment, tmp_path
        self.monkeypatch = monkeypatch
        self.settings = settings()
        self.temporal = AsyncMock()
        self.commands: list[tuple] = []
        self.published: dict[str, bytes] = {}

    async def launch(self, key: str, arguments: dict, inputs: dict[str, bytes]):
        attachments = [
            {
                "name": name,
                "artifactRef": await owned(self.environment, body),
                "fileName": f"{name}.json",
            }
            for name, body in inputs.items()
        ]
        jobs = ContainerJobService(
            self.environment.session,
            temporal=self.temporal,
            artifacts=self.environment.service,
            artifact_access=ArtifactAccessContext.for_service(
                owner_artifact_principal(OWNER)
            ),
            backend_settings=self.settings,
        )
        await jobs.submit(
            owner=OWNER,
            request=ContainerJobSubmitRequest.model_validate(
                {
                    "idempotencyKey": key,
                    "source": {"source": "workflow", "workflowId": "assessment"},
                    "spec": {
                        "workloadProfile": {
                            "profileRef": self.profile_ref,
                            "mode": "evaluate",
                            "arguments": arguments,
                        },
                        "workspaceRef": {"kind": "sandbox", "workspaceId": key},
                        "inputAttachments": attachments,
                        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                        "timeoutSeconds": 600,
                    },
                }
            ),
        )
        started = self.temporal.start_container_job.call_args.args[0]

        async def daemon(argv):
            self.commands.append(tuple(argv))
            if argv[0] == "inspect":
                return 1, b"", b"no such container"
            return 0, b"", b""

        self.jobs = jobs

        async def publish(request, name, payload):
            ref = await _container_job_evidence_publisher(self.environment.service)(
                request, name, payload
            )
            self.published[ref] = payload
            # The assessment fixture explicitly admits all outputs from this job.
            await self.environment.service.delegate_read_access(
                artifact_ids=[ref],
                workflow_id="assessment",
                access=ArtifactAccessContext.for_activity(),
            )
            return ref

        self.backend = DockerContainerJobBackend(
            workspace_root=self.tmp_path,
            command_runner=daemon,
            evidence_publisher=publish,
            artifact_reader=_container_job_artifact_reader(self.environment.service),
            settings=self.settings,
        )
        workspace = self.tmp_path / "temporal_sandbox" / key / "repo"
        workspace.mkdir(parents=True)
        self.request = ContainerJobActivityRequest(
            jobId=started.job_id,
            owner=started.owner,
            ownershipToken=started.ownership_token,
            request=started.request,
            resolvedWorkspaceRef=str(workspace),
            resolvedImageRef=IMAGE,
        )
        await create_container_in_activity(
            self.backend, self.request, self.environment, self.monkeypatch
        )
        self.storage = storage_for(self.tmp_path, started.job_id)
        create = [c for c in self.commands if c[0] == "create"][-1]
        return create, list(create[create.index(IMAGE) + 1 :])

    def process(self, argv: list[str]) -> subprocess.Popen:
        """The launch command as the container would run it, with job storage."""
        mounts = {
            "/inputs/": f"{self.storage.inputs}/",
            "/outputs/": f"{self.storage.outputs}/",
        }
        for old, new in mounts.items():
            argv = [arg.replace(old, new) for arg in argv]
        return subprocess.Popen(
            [sys.executable, *argv],
            env={"PATH": os.environ["PATH"], "PYTHONPATH": str(REPOSITORY)},
            cwd=self.tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    async def publish_evidence(self, *, terminal_state: str, exit_code: int | None):
        """Publish terminal evidence under the job Activity, even without a result."""
        self.request.container_ref = "fixture-container"
        self.request.terminal_state = terminal_state
        self.request.exit_code = exit_code
        from moonmind.schemas.container_job_models import container_job_workflow_id

        activity = ActivityEnvironment()
        activity.info = replace(
            activity.info, workflow_id=container_job_workflow_id(self.request.job_id)
        )
        result = await activity.run(self.backend.publish_evidence, self.request)
        self.request.artifacts_ref = result.artifacts_ref
        await _container_job_projection_writer("docker", "fixture")(self.request)
        return result

    async def publish(self, *, terminal_state: str, exit_code: int | None) -> str:
        await self.publish_evidence(terminal_state=terminal_state, exit_code=exit_code)
        return next(
            ref for ref, body in self.published.items() if self.result_schema in body
        )

    async def assess(self, ref: str, key: str, **options) -> dict:
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


async def test_profile_job_result_is_read_as_game_math_coverage(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    create, argv = await job.launch(
        "game-math",
        {
            "spec": "spec/spec.json",
            "settlements": "settlements/settlements.json",
            "samples": 20_000,
            "seed": 5199,
        },
        {
            "spec": json.dumps(SPEC).encode(),
            "settlements": "\n".join(map(json.dumps, SETTLEMENTS)).encode(),
        },
    )
    # The profile owns the image's entrypoint and network; omitted optional
    # arguments drop their argv entries.
    assert create[create.index("--entrypoint") + 1] == "python"
    assert create[create.index("--network") + 1] == "none"
    assert argv == [
        "-m",
        "moonmind.security.game_math",
        "--spec=/inputs/spec/spec.json",
        "--settlements=/inputs/settlements/settlements.json",
        "--samples=20000",
        "--seed=5199",
        f"--output=/outputs/{OUTPUT}",
    ]
    process = job.process(argv)
    _, stderr = process.communicate(timeout=300)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    result = json.loads(job.published[ref])

    # Exact theory and simulated observations are separate sections.
    assert {row["rtp"] for row in result["theoretical"]} == {"99/100"}
    simulated = {row["strategy"]: row for row in result["simulation"]["results"]}
    assert {row["decision"] for row in simulated.values()} == {"equivalent"}
    assert simulated["cash-2"]["samples"] == 20_000
    assert result["simulation"]["seed"] == 5199
    assert [(v["rule"], v["case"]) for v in result["violations"]] == [
        ("settlement", "M=1")
    ]
    assert result["provenance"]["approvalRef"] == "filing-5199"
    assert result["certification"] == "not_performed"

    outputs = await job.assess(ref, "game-math-review")
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["scanner"], reviewed["target"]) == ("game-math", GAME)
    assert (reviewed["coverage"], reviewed["outcome"]) == ("complete", "violations")
    assert reviewed["verification"] == "not_performed"
    assert reviewed["inputVerification"] == "reproduced"
    assert {"assessment", "spec", "settlements", "jobManifest"} <= reviewed[
        "nativeRefs"
    ].keys()
    assert outputs["coverageStatus"] == "complete"
    assert outputs["counts"]["new"] == 1


async def test_cancelled_profile_job_keeps_partial_evidence(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv = await job.launch(
        "game-math-cancel",
        {"spec": "spec/spec.json", "samples": 100_000_000, "seed": 1},
        {"spec": json.dumps(SPEC).encode()},
    )
    process = job.process(argv)
    # The CLI reports readiness after installing its stop handler.
    assert b"started" in process.stderr.readline()
    process.send_signal(signal.SIGTERM)
    _, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="canceled", exit_code=None)
    result = json.loads(job.published[ref])
    assert result["simulation"]["stopReason"] == "cancelled"
    assert (result["status"], result["outcome"]) == ("partial", "incomplete")
    assert {row["strategy"] for row in result["simulation"]["results"]} == {
        row["id"] for row in SPEC["strategies"]
    }
    # SIGTERM can arrive before the first sample. Every strategy still has a
    # retained row, and neither zero samples nor saved progress imply agreement.
    assert all(
        0 <= row["samples"] < 100_000_000 and row["decision"] == "incomplete"
        for row in result["simulation"]["results"]
    )
    # Exact theory computed before the stop is kept beside the partial run.
    assert {row["rtp"] for row in result["theoretical"]} == {"99/100"}

    outputs = await job.assess(ref, "game-math-cancel-review")
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["coverage"], reviewed["outcome"]) == ("partial", "incomplete")
    assert reviewed["execution"] == {"simulation": "cancelled"}
    assert "simulation_cancelled" in reviewed["limitations"]
    assert outputs["coverageStatus"] == "stale_or_partial"
    assert outputs["counts"]["new"] == 0


async def retained_job(
    environment,
    tmp_path,
    monkeypatch,
    *,
    key="retained",
    settlements=SETTLEMENTS,
    spec=SPEC,
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv = await job.launch(
        key,
        {"spec": "spec/spec.json", "settlements": "settlements/settlements.json"},
        {
            "spec": json.dumps(spec).encode(),
            "settlements": "\n".join(map(json.dumps, settlements)).encode(),
        },
    )
    process = job.process(argv)
    _, error = process.communicate(timeout=60)
    assert process.returncode == 0, error.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    return job, ref


def controls():
    from datetime import UTC, datetime
    from tests.unit.security.test_control_assessments import requirement

    day = datetime.now(UTC).date().isoformat()
    return {
        "requirements": [
            requirement(
                scope={"assets": [GAME]},
                period={"start": day, "end": day},
                title="Reference settlement conforms to supplied specification",
                objectives=[
                    {
                        "id": "settlement",
                        "description": "Check retained settlement cases",
                        "source": "game-math",
                        "tool": "game-math",
                        "ruleIds": ["settlement"],
                    }
                ],
            )
        ]
    }


async def test_job_to_control_report_export_and_explicit_retest(
    environment, tmp_path, monkeypatch
):
    from tests.unit.security.test_assessment_evidence_exports import unpack
    from tests.artifact_access import access_for_test

    service = environment.service
    job, ref = await retained_job(environment, tmp_path, monkeypatch)
    plan = controls()
    result = await job.assess(ref, "controls", controlAssessment=plan)
    control = await recurring.read(service, result["controlAssessmentRef"])
    assert control["requirements"][0]["conclusion"] == "not_satisfied"
    scan = await recurring.read(service, result["scanRef"])
    assert all(
        row["evidenceMethod"] == "deterministic_check"
        for row in scan["observation"]["coverage"]
    )
    summary = json.loads(job.published[ref])
    assert set(summary["job"]["inputRefs"].values()) <= await recurring.required(
        service, ref
    )
    assert {
        ref,
        *summary["job"]["inputRefs"].values(),
        job.request.artifacts_ref,
    } <= await recurring.required(service, result["scanRef"])
    _, report = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert b"deterministic_check" in report and b"not_satisfied" in report
    assert b"does not certify a deployed game" in report
    exported = await job.assess(
        ref,
        "controls-export",
        controlAssessment=plan,
        evidenceExport={
            "artifactRefs": [
                ref,
                result["controlAssessmentRef"],
                recurring.report_ref(result),
            ]
        },
    )
    archive, manifest = await unpack(service, exported)
    row = next(row for row in manifest["entries"] if row["sourceArtifactId"] == ref)
    projected = json.loads(archive.read(row["memberName"]))
    assert projected["certification"] == "not_performed"
    assert projected["job"]["inputRefs"] == summary["job"]["inputRefs"]
    assert projected["theoretical"][0]["rtp"] == "99/100"

    fixed, fixed_ref = await retained_job(
        environment,
        tmp_path,
        monkeypatch,
        key="fixed",
        settlements=[
            SETTLEMENTS[0],
            {**SETTLEMENTS[1], "payouts": ["0"], "total": "0"},
        ],
    )
    unreviewed = await fixed.assess(
        fixed_ref,
        "unreviewed",
        baselineRef=result["comparisonRef"],
        controlAssessment=plan,
    )
    assert unreviewed["counts"]["absent_comparable"] == 1
    compared = await recurring.read(service, result["comparisonRef"])
    finding_id = compared["findings"][0]["findingId"]
    decision = recurring.decision(finding_id, [fixed_ref])
    decision["scope"] = {"assets": [GAME]}
    decisions = await recurring.put(service, [decision])
    reviewed = await fixed.assess(
        fixed_ref,
        "reviewed",
        baselineRef=result["comparisonRef"],
        decisionsRef=decisions,
        controlAssessment=plan,
    )
    assert reviewed["counts"]["remediated_verified"] == 1
    assert (await recurring.read(service, reviewed["controlAssessmentRef"]))[
        "requirements"
    ][0]["conclusion"] == "satisfied"


@pytest.mark.parametrize(
    "attack",
    [
        "copy",
        "changed_input",
        "missing_input",
        "revoked_input",
        "replaced_output",
        "wrong_terminal",
        "missing_manifest",
    ],
)
async def test_untrusted_job_evidence_cannot_be_control_execution(
    environment, tmp_path, monkeypatch, attack
):
    from api_service.db.models import ContainerJobRecord
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
    )

    job, ref = await retained_job(environment, tmp_path, monkeypatch)
    summary = json.loads(job.published[ref])
    original_read = environment.service.read
    input_ref = summary["job"]["inputRefs"]["spec"]
    if attack == "copy":
        ref = await recurring.put(environment.service, summary)
    elif attack == "wrong_terminal":
        record = await environment.session.get(ContainerJobRecord, job.request.job_id)
        record.state = "failed"
        await environment.session.commit()
    elif attack == "missing_manifest":
        record = await environment.session.get(ContainerJobRecord, job.request.job_id)
        record.artifacts_ref = None
        await environment.session.commit()
    else:

        async def damaged(*, artifact_id, access):
            if artifact_id == input_ref:
                if attack == "missing_input":
                    raise TemporalArtifactNotFoundError(input_ref)
                if attack == "revoked_input":
                    raise TemporalArtifactAuthorizationError("fixture revoked")
                if attack == "changed_input":
                    metadata, body = await original_read(
                        artifact_id=artifact_id, access=access
                    )
                    return metadata, body + b" "
            metadata, body = await original_read(artifact_id=artifact_id, access=access)
            if attack == "replaced_output" and artifact_id == ref:
                body = body + b" "
            return metadata, body

        monkeypatch.setattr(environment.service, "read", damaged)
    if attack in {"missing_input", "revoked_input", "replaced_output"}:
        from temporalio.exceptions import ApplicationError

        with pytest.raises(
            ApplicationError, match="(not found|revoked|digest mismatch)"
        ):
            await job.assess(ref, "attack", controlAssessment=controls())
        return
    result = await job.assess(ref, "attack", controlAssessment=controls())
    assert result.get("controlAssessmentCounts", {}).get("satisfied", 0) == 0
    assert result["scanOutcome"] != "completed", result


async def test_imported_summary_is_preserved_but_never_executed_control(
    environment, tmp_path, monkeypatch
):
    job, ref = await retained_job(
        environment, tmp_path, monkeypatch, settlements=[SETTLEMENTS[0]]
    )
    summary = json.loads(job.published[ref])
    summary.pop("job")
    imported = await recurring.put(environment.service, summary)
    result = await job.assess(imported, "import", controlAssessment=controls())
    assert result["scanOutcome"] == "completed"
    assert result["scannerSummaries"][0]["inputVerification"] == "unverified"
    control = await recurring.read(environment.service, result["controlAssessmentRef"])
    assert control["requirements"][0]["conclusion"] == "inconclusive"


async def test_removing_a_targeted_case_cannot_clear_its_violation(
    environment, tmp_path, monkeypatch
):
    job, ref = await retained_job(environment, tmp_path, monkeypatch)
    original = await job.assess(ref, "case-original")
    reduced, reduced_ref = await retained_job(
        environment, tmp_path, monkeypatch, key="reduced", settlements=[SETTLEMENTS[0]]
    )
    result = await reduced.assess(
        reduced_ref, "case-reduced", baselineRef=original["comparisonRef"]
    )
    assert result["counts"]["not_reassessed"] == 1
    assert result["counts"]["absent_comparable"] == 0


async def test_import_cannot_clear_verified_declared_value_finding(
    environment, tmp_path, monkeypatch
):
    import copy

    spec = copy.deepcopy(SPEC)
    spec["declared"][0]["rtp"] = "0.8"
    job, ref = await retained_job(
        environment, tmp_path, monkeypatch, spec=spec, settlements=[]
    )
    baseline = await job.assess(ref, "trusted-theory")
    report = json.loads(job.published[ref])
    report.pop("job")
    report["violations"] = []
    report["outcome"] = "consistent"
    for row in report["theoretical"]:
        row["consistent"] = True
    forged = await recurring.put(environment.service, report)
    result = await job.assess(
        forged, "imported-theory", baselineRef=baseline["comparisonRef"]
    )
    assert result["counts"]["not_reassessed"] == 1
    assert result["counts"]["absent_comparable"] == 0
    compared = await recurring.read(environment.service, baseline["comparisonRef"])
    decision = recurring.decision(compared["findings"][0]["findingId"], [forged])
    decision["scope"] = {"assets": [GAME]}
    decisions = await recurring.put(environment.service, [decision])
    result = await job.assess(
        forged,
        "imported-theory-retest",
        baselineRef=baseline["comparisonRef"],
        decisionsRef=decisions,
    )
    assert result["counts"]["remediated_verified"] == 0


@pytest.mark.parametrize("mutation", ["quarantine", "revoke"])
async def test_revoked_retained_input_blocks_cached_positive_scan(
    environment, tmp_path, monkeypatch, mutation
):
    from api_service.db.models import TemporalArtifact, TemporalArtifactLink
    from sqlalchemy import delete
    from temporalio.exceptions import ApplicationError

    job, ref = await retained_job(
        environment, tmp_path, monkeypatch, settlements=[SETTLEMENTS[0]]
    )
    result = await job.assess(ref, "cache", controlAssessment=controls())
    assert result["controlAssessmentCounts"]["satisfied"] == 1
    blocked = json.loads(job.published[ref])["job"]["inputRefs"]["spec"]
    if mutation == "quarantine":
        artifact = await environment.session.get(TemporalArtifact, blocked)
        artifact.metadata_json = {**artifact.metadata_json, "quarantine": True}
    else:
        await environment.session.execute(delete(TemporalArtifactLink).where(
            TemporalArtifactLink.artifact_id == blocked,
            TemporalArtifactLink.workflow_id == "assessment",
        ))
    await environment.session.commit()
    with pytest.raises(ApplicationError, match="(quarantined|QUARANTINED|read|access)"):
        await job.assess(ref, "cache", controlAssessment=controls())
