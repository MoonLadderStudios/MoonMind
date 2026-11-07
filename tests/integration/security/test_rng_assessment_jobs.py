"""RNG jobs, artifacts and retained reports (#5198).

The real service, profile admission, attachment authority, CLI process, output
collector and security.assess execute. Docker commands are a daemon fixture;
these tests do not claim live Docker, SIGKILL-container or OOM qualification.
"""

from __future__ import annotations

import json
import signal

import pytest

from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.security.workload_profiles import WORKLOAD_PROFILES_ENV_KEY
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactAuthorizationError,
)
from tests.integration.security import test_game_math_jobs as game_jobs
from tests.unit.security.test_rng_assessment import specification

environment = game_jobs.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
OUTPUT = "rng-assessment.json"
RAW = bytes(range(256)) * 128
PROFILE = {
    "profileRef": "rng-review",
    "imageSourceRef": "moonmind",
    "entrypoint": ["python", "-m", "moonmind.security.rng_assessment"],
    "modes": [
        {
            "name": "evaluate",
            "actionClass": "rng-review",
            "argv": [
                "--spec={spec}",
                "--raw={raw}",
                "--outcomes={outcomes}",
                "--max-seconds={maxSeconds}",
                f"--output=/outputs/{OUTPUT}",
            ],
            "arguments": [
                {"name": "spec", "type": "input", "required": True},
                {"name": "raw", "type": "input"},
                {"name": "outcomes", "type": "input"},
                {"name": "maxSeconds", "type": "integer", "minimum": 1},
            ],
        }
    ],
    "outputs": [{"name": "assessment", "relativePath": OUTPUT}],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 1024,
    "maxTimeoutSeconds": 600,
}


class Job(game_jobs.Job):
    profile_ref = "rng-review"
    result_schema = b'"rng-assessment/v1"'

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

    async def assess(self, ref, key):
        from tests.integration.security.test_recurring_assessments import invoke

        return await invoke(
            self.environment.service,
            {
                "scope": {"assets": ["game:rng-fixture"]},
                "tools": ["retained-report"],
                "reportRefs": [ref],
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


async def test_profile_rng_to_retained_report_preserves_separate_claims(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    spec = specification(mapped=True)
    spec["implementationReview"][0]["observation"] = (
        "seed=LIVE_SEED_DO_NOT_EXPORT internal_state=LIVE_STATE_DO_NOT_EXPORT "
        "password=CREDENTIAL_DO_NOT_EXPORT"
    )
    create, argv = await job.launch(
        "rng-fixture",
        {
            "spec": "spec/spec.json",
            "raw": "raw/raw.json",
            "outcomes": "outcomes/outcomes.json",
        },
        {
            "spec": json.dumps(spec).encode(),
            "raw": b"SAMPLE_DO_NOT_EXPORT" + RAW,
            "outcomes": "\n".join(str((b & 7) % 3) for b in RAW).encode(),
        },
    )
    assert create[create.index("--network") + 1] == "none"
    assert create[create.index("--memory") + 1] == "512m"
    assert float(create[create.index("--cpus") + 1]) == 1
    process = job.process(argv)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    result = json.loads(job.published[ref])
    assert result["raw"]["results"][0]["decision"] == "no_anomaly_detected"
    assert result["mapped"]["results"][0]["decision"] == "anomaly"
    assert result["cryptographicSecurity"] == "unverified"
    for secret in (
        b"SAMPLE_DO_NOT_EXPORT",
        b"LIVE_SEED_DO_NOT_EXPORT",
        b"LIVE_STATE_DO_NOT_EXPORT",
        b"CREDENTIAL_DO_NOT_EXPORT",
    ):
        assert secret not in stdout + stderr + job.published[ref]
    outputs = await job.assess(ref, "rng-retained")
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["scanner"], reviewed["outcome"]) == ("rng", "anomalies")
    assert reviewed["implementationReview"][0]["status"] == "concern"
    assert reviewed["externalClaims"][0]["authentication"] == "unverified"
    assert reviewed["verification"] == "not_performed"
    assert "provenance" in reviewed["interpretation"].lower()
    assert outputs["counts"]["new"] == 1
    assert "LIVE_SEED_DO_NOT_EXPORT" not in json.dumps(outputs)

    # The report artifact still uses the existing execution-scoped authority.
    with pytest.raises(TemporalArtifactAuthorizationError):
        await environment.service.read(
            artifact_id=ref,
            access=ArtifactAccessContext.for_service("unrelated"),
        )


async def test_sigterm_preserves_completed_raw_diagnostics(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    spec = specification(mapped=True)
    spec["mapped"]["amount"] = 1_000_000
    _, argv = await job.launch(
        "rng-cancel",
        {
            "spec": "spec/spec.json",
            "raw": "raw/raw.json",
            "outcomes": "outcomes/outcomes.json",
        },
        {"spec": json.dumps(spec).encode(), "raw": RAW, "outcomes": b"0\n" * 1_000_000},
    )
    process = job.process(argv)
    assert b"started" in process.stderr.readline()
    assert b"raw" in process.stderr.readline()
    process.send_signal(signal.SIGTERM)
    _, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="canceled", exit_code=0)
    result = json.loads(job.published[ref])
    assert result["raw"]["processedAmount"] == len(RAW)
    assert result["mapped"]["stopReason"] == "cancelled"
    assert (result["status"], result["outcome"]) == ("partial", "incomplete")
    outputs = await job.assess(ref, "rng-cancel-review")
    assert outputs["coverageStatus"] == "stale_or_partial"


@pytest.mark.parametrize(
    "terminal,exit_code",
    [("failed", 137), ("failed", 1), ("canceled", 137), ("timed_out", 137)],
)
async def test_abrupt_job_failure_cannot_publish_clean_saved_result(
    environment, tmp_path, monkeypatch, terminal, exit_code
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv = await job.launch(
        "rng-failure",
        {"spec": "spec/spec.json", "raw": "raw/raw.json"},
        {"spec": json.dumps(specification()).encode(), "raw": RAW},
    )
    process = job.process(argv)
    process.communicate(timeout=30)
    assert process.returncode == 0
    ref = await job.publish(terminal_state=terminal, exit_code=exit_code)
    result = json.loads(job.published[ref])
    assert (result["status"], result["outcome"]) == ("partial", "incomplete")
    assert result["job"]["terminalState"] == terminal
    outputs = await job.assess(ref, "rng-failure-review")
    assert outputs["coverageStatus"] == "stale_or_partial"


async def test_sigkill_without_result_stays_missing(environment, tmp_path, monkeypatch):
    job = Job(environment, tmp_path, monkeypatch)
    spec = specification(mapped=True)
    spec["mapped"]["amount"] = 1_000_000
    _, argv = await job.launch(
        "rng-kill",
        {
            "spec": "spec/spec.json",
            "raw": "raw/raw.json",
            "outcomes": "outcomes/outcomes.json",
        },
        {"spec": json.dumps(spec).encode(), "raw": RAW, "outcomes": b"0\n" * 1_000_000},
    )
    process = job.process(argv)
    assert b"started" in process.stderr.readline()
    process.kill()
    process.communicate(timeout=10)
    assert process.returncode == -signal.SIGKILL
    job.request.terminal_state = "failed"
    job.request.exit_code = 137
    await job.backend.publish_evidence(job.request)
    assert not any(job.result_schema in value for value in job.published.values())
    diagnostics = next(
        json.loads(v) for v in job.published.values() if b'"terminalState"' in v
    )
    assert diagnostics["terminalState"] == "failed"
    assert diagnostics["outputs"][0]["collectionStatus"] == "missing"


async def test_protected_fields_in_invalid_output_never_reach_artifact_export(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    _, argv = await job.launch(
        "rng-invalid-output",
        {"spec": "spec/spec.json", "raw": "raw/raw.json"},
        {"spec": json.dumps(specification()).encode(), "raw": RAW},
    )
    process = job.process(argv)
    process.communicate(timeout=30)
    assert process.returncode == 0
    path = job.storage.outputs / OUTPUT
    data = json.loads(path.read_text())
    data["internalState"] = "LIVE_STATE_DO_NOT_EXPORT"
    path.write_text(json.dumps(data))
    job.request.terminal_state = "succeeded"
    job.request.exit_code = 0
    await job.backend.publish_evidence(job.request)
    assert not any(
        b"LIVE_STATE_DO_NOT_EXPORT" in value for value in job.published.values()
    )
    diagnostics = next(
        json.loads(v) for v in job.published.values() if b'"terminalState"' in v
    )
    assert diagnostics["outputs"][0]["collectionStatus"] == "rejected"


async def test_profile_rejects_resource_excess_before_launch(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    from moonmind.schemas.container_job_models import ContainerJobSpec
    from moonmind.security.workload_profiles import resolve_profile_job

    spec = ContainerJobSpec.model_validate(
        {
            "workloadProfile": {
                "profileRef": "rng-review",
                "mode": "evaluate",
                "arguments": {"spec": "spec/spec.json"},
            },
            "workspaceRef": {"kind": "sandbox", "workspaceId": "rng-limit"},
            "inputAttachments": [
                {
                    "name": "spec",
                    "artifactRef": "art_01M6SYN841WSRS0BHK19MS2A96",
                    "fileName": "spec.json",
                }
            ],
            "resources": {"cpuMillis": 1000, "memoryMiB": 2048},
            "timeoutSeconds": 60,
        }
    )
    with pytest.raises(ValueError, match="resources exceed"):
        resolve_profile_job(spec, job.settings.workload_profiles)
