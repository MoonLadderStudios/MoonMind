"""Application access artifact journey with prebuilt sanitized observations.

Admission, profile rendering, input materialization, collection, artifact storage
and security.assess are production paths. Docker is a command fixture and target
observations are prebuilt here: HTTP execution is covered by the target tests.
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from moonmind.config.container_backend_settings import (
    IMAGE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.security.application_access import (
    ApplicationAccessBundle,
    ApplicationAccessSpec,
    assess_application_access,
    spec_digest,
)
from moonmind.security.application_access_target import ApplicationAccessCapture
from moonmind.security.assessments import content_key
from moonmind.security.target_assessments import TargetScope
from moonmind.security.workload_profiles import (
    TARGET_SCOPE_ENV,
    WORKLOAD_PROFILES_ENV_KEY,
)
from tests.integration.security import test_game_math_jobs as game_jobs
from tests.integration.security.test_admitted_assessment_journeys import (
    assert_assessment_retains,
    assert_retained,
    finish,
)
from tests.unit.security.test_application_access import observation, spec

environment = game_jobs.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
ORIGIN = "https://application.example.test"
PROFILE = {
    "profileRef": "application-access",
    "imageSourceRef": "moonmind",
    "entrypoint": ["python", "-m", "moonmind.security.application_access_target"],
    "modes": [
        {
            "name": "evaluate",
            "actionClass": "application-access",
            "argv": [
                "--spec={spec}",
                "--capture=/outputs/capture.json",
                "--output=/outputs/assessment.json",
                "--max-seconds={maxSeconds}",
            ],
            "arguments": [
                {"name": "spec", "type": "input", "required": True},
                {"name": "maxSeconds", "type": "integer", "minimum": 1, "maximum": 300},
            ],
        }
    ],
    "outputs": [
        {"name": "capture", "relativePath": "capture.json"},
        {"name": "assessment", "relativePath": "assessment.json"},
    ],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 1024,
    "maxTimeoutSeconds": 600,
}


def target_scope():
    return TargetScope.model_validate(
        {
            "web": {
                "origin": ORIGIN,
                "routes": [
                    {"path": "/identity", "methods": ["GET"]},
                    {"path": "/record", "methods": ["GET"]},
                ],
                "startPaths": ["/identity"],
                "mode": "baseline",
                "egressPolicyRef": "fixture",
                "timeoutSeconds": 300,
            },
            "maxRequests": 40,
        }
    )


class Job(game_jobs.Job):
    profile_ref = "application-access"
    result_schema = b'"application-access-report/v1"'

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
                WORKLOAD_PROFILES_ENV_KEY: json.dumps(
                    [
                        {
                            **PROFILE,
                            "environment": {
                                TARGET_SCOPE_ENV: target_scope().model_dump_json(
                                    by_alias=True
                                )
                            },
                        }
                    ]
                ),
            }
        )

    async def assess(self, ref, key, baseline=None):
        return await game_jobs.invoke(
            self.environment.service,
            {
                "scope": {"assets": [ORIGIN]},
                "tools": ["retained-report"],
                "reportRefs": [ref],
                "credentialRefs": [],
                "networkRefs": [],
                "budget": {
                    "baseSeconds": 60,
                    "maxSeconds": 120,
                    "progressStallSeconds": 60,
                },
                **({"baselineRef": baseline} if baseline else {}),
            },
            key=key,
        )


async def launch(job, key, *, policy=None, leak=False):
    policy = policy or spec()
    raw_spec = policy.model_dump_json(by_alias=True).encode()
    create, argv = await job.launch(
        key, {"spec": "spec/spec.json", "maxSeconds": 60}, {"spec": raw_spec}
    )
    scope = target_scope()
    observations = []
    for case in policy.cases:
        obs = observation(
            policy,
            {
                "error": "forbidden",
                **(
                    {"record": {"secret": "SANITIZE_ME"}}
                    if leak and case.case_ref == "read"
                    else {}
                ),
            },
        )
        obs.case_ref = case.case_ref
        observations.append(obs)
    bundle = ApplicationAccessBundle(
        spec_digest=spec_digest(policy), observations=observations
    )
    capture = ApplicationAccessCapture(
        scope=scope,
        scope_digest=content_key(scope.model_dump(mode="json")),
        max_seconds=60,
        started_at=time.time(),
        next_request_at=time.time(),
        requests_spent=3 * len(observations),
        bundle=bundle,
        progress=[{"caseRef": o.case_ref, "phase": "complete"} for o in observations],
        status="complete",
    )
    raw_capture = capture.model_dump_json(by_alias=True).encode()
    report = assess_application_access(policy, bundle)
    report.input_digests = {
        "spec": hashlib.sha256(raw_spec).hexdigest(),
        "capture": hashlib.sha256(raw_capture).hexdigest(),
    }
    job.storage.outputs.mkdir(parents=True, exist_ok=True)
    (job.storage.outputs / "capture.json").write_bytes(raw_capture)
    (job.storage.outputs / "assessment.json").write_text(
        report.model_dump_json(by_alias=True)
    )
    return create, argv, {"spec": raw_spec, "capture": raw_capture}


async def test_materialized_inputs_and_capture_survive_cleanup(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    create, argv, bodies = await launch(job, "access-retained", leak=True)
    assert create[create.index("--entrypoint") + 1] == "python"
    assert argv == [
        "-m",
        "moonmind.security.application_access_target",
        "--spec=/inputs/spec/spec.json",
        "--capture=/outputs/capture.json",
        "--output=/outputs/assessment.json",
        "--max-seconds=60",
    ]
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    report = json.loads(job.published[ref])
    refs = report["job"]["inputRefs"]
    assert set(refs) == {"spec", "capture"}
    assert report["inputDigests"] == {
        k: hashlib.sha256(v).hexdigest() for k, v in bodies.items()
    }
    assert all(job.published[refs[k]] == bodies[k] for k in bodies)
    assert list(job.published).index(refs["capture"]) < list(job.published).index(ref)
    assert b"SANITIZE_ME" not in b"".join(job.published.values())
    await finish(job.backend, job.request, tmp_path)
    result = await job.assess(ref, "access-retained-review")
    (summary,) = result["scannerSummaries"]
    assert (summary["scanner"], summary["target"], summary["coverage"]) == (
        "application-access",
        ORIGIN,
        "complete",
    )
    assert result["counts"]["new"] == 1
    dependencies = await game_jobs.recurring.required(
        environment.service, result["scanRef"]
    )
    assert {ref, *refs.values()} <= dependencies
    await assert_retained(environment, [ref, *refs.values()], job.published)
    await assert_assessment_retains(environment, ref, list(refs.values()), [ORIGIN])


@pytest.mark.parametrize("terminal,code", [("failed", 1), ("canceled", None)])
async def test_terminal_failure_cannot_promote_complete_capture(
    environment, tmp_path, monkeypatch, terminal, code
):
    job = Job(environment, tmp_path, monkeypatch)
    await launch(job, "access-" + terminal)
    ref = await job.publish(terminal_state=terminal, exit_code=code)
    report = json.loads(job.published[ref])
    assert report["job"]["terminalState"] == terminal
    assert (report["status"], report["outcome"]) == ("partial", "incomplete")
    result = await job.assess(ref, "access-review-" + terminal)
    assert result["coverageStatus"] == "stale_or_partial"
    assert result["scannerSummaries"][0]["coverage"] == "partial"


@pytest.mark.parametrize("name", ["spec", "capture"])
@pytest.mark.parametrize("fault", ["tampered", "missing"])
async def test_retained_dependencies_are_required_and_verified(
    environment, tmp_path, monkeypatch, name, fault
):
    job = Job(environment, tmp_path, monkeypatch)
    await launch(job, "access-corrupt-" + name + fault)
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    report = json.loads(job.published[ref])
    report["job"]["inputRefs"][name] = (
        await game_jobs.recurring.put(environment.service, b'{"tampered":true}')
        if fault == "tampered"
        else "art_missing_dependency"
    )
    corrupted = await game_jobs.recurring.put(environment.service, report)
    result = await job.assess(corrupted, "reject-" + name + fault)
    scan = await game_jobs.recurring.read(environment.service, result["scanRef"])
    assert scan["scanOutcome"] == "incomplete"
    assert not scan["observation"]["findings"]
    assert scan["diagnostics"]
    assert not scan["scannerSummaries"]


async def test_profile_rejects_uncaptured_input_path(
    environment, tmp_path, monkeypatch
):
    job = Job(environment, tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        await job.launch(
            "access-path-escape", {"spec": "../../unadmitted.json"}, {"spec": b"{}"}
        )


async def test_removed_policy_case_does_not_mark_a_finding_fixed(
    environment, tmp_path, monkeypatch
):
    original = spec().model_dump(mode="json")
    original["authorization_matrix"]["cases"].append(
        {
            **original["authorization_matrix"]["cases"][0],
            "id": "retained",
            "action": "inspect",
        }
    )
    original["cases"].append({**original["cases"][0], "case_ref": "retained"})
    policy = ApplicationAccessSpec.model_validate(original)
    job = Job(environment, tmp_path, monkeypatch)
    await launch(job, "access-before", policy=policy, leak=True)
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    first = await job.assess(ref, "access-before-review")
    assert first["counts"]["new"] == 1
    original["authorization_matrix"]["cases"] = original["authorization_matrix"][
        "cases"
    ][1:]
    original["cases"] = original["cases"][1:]
    reduced = ApplicationAccessSpec.model_validate(original)
    second = Job(environment, tmp_path, monkeypatch)
    await launch(second, "access-after", policy=reduced)
    ref = await second.publish(terminal_state="succeeded", exit_code=0)
    result = await second.assess(ref, "access-after-review", first["comparisonRef"])
    assert result["counts"]["remediated_verified"] == 0


async def test_cli_submission_reaches_signed_session_mcp_admission(
    environment, tmp_path, monkeypatch
):
    """No transport permission is implied by this offline profile admission."""
    from urllib.parse import urlsplit

    from httpx import ASGITransport, AsyncClient
    from typer.testing import CliRunner

    from moonmind.cli import app
    from moonmind.container_job_cli import ContainerJobCliError, ContainerJobMcpClient
    from tests.integration.security.test_admitted_assessment_journeys import Session

    job = Job(environment, tmp_path, monkeypatch)
    session = Session(environment, monkeypatch, workspace_id="access-cli")
    session.service = {"temporal": job.temporal, "backend_settings": job.settings}
    ref = await game_jobs.owned(
        environment, spec().model_dump_json(by_alias=True).encode()
    )
    await environment.service.link_artifact(
        artifact_id=ref,
        principal=game_jobs.owner_artifact_principal(game_jobs.OWNER),
        execution_ref={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
            "link_type": "input.container-job",
        },
    )
    path = tmp_path / "job.json"
    path.write_text(
        json.dumps(
            {
                "workloadProfile": {
                    "profileRef": "application-access",
                    "mode": "evaluate",
                    "arguments": {"spec": "spec/spec.json"},
                },
                "inputAttachments": [
                    {"name": "spec", "artifactRef": ref, "fileName": "spec.json"}
                ],
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "timeoutSeconds": 600,
            }
        )
    )
    sent = []

    def capture(client, tool, arguments):
        if tool == "container.submit":
            sent.append((client._endpoint, client._authorization_headers(), arguments))
            return {"jobId": "pending-replay"}
        if tool == "container.status":
            return {"state": "succeeded"}
        raise ContainerJobCliError("auxiliary reads are outside this fixture")

    monkeypatch.setattr(ContainerJobMcpClient, "call", capture)
    result = CliRunner().invoke(app, ["container", "run", "--spec", str(path)])
    assert result.exit_code == 0, result.output
    ((endpoint, headers, arguments),) = sent
    async with AsyncClient(
        transport=ASGITransport(app=session.app), base_url="http://api:8000"
    ) as client:
        response = await client.post(
            urlsplit(endpoint).path,
            headers=headers,
            json={"tool": "container.submit", "arguments": arguments},
        )
    assert response.status_code == 200, response.text
    started = job.temporal.start_container_job.call_args.args[0]
    assert started.request.source.source == "omnigent"
    assert started.request.spec.workload_profile.profile_ref == "application-access"
    assert started.request.spec.network_mode == "none"
