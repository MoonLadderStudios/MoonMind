"""RNG assessments as workload-profile container jobs (#5198).

The deployment's ``rng-assessment`` workload profile runs the production
``moonmind.security.rng_assessment`` CLI from a MoonMind image. Admission,
profile rendering, input capture through the real artifact authority, launch
arguments, declared-output collection, artifact storage and ``security.assess``
are production code, through the game math job harness. The Docker daemon is a
command fixture, and cancellation is the SIGTERM ``docker stop`` sends.
"""

from __future__ import annotations

import base64
import json
import signal

import pytest

from moonmind.security.rng_assessment import fixture_sample
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_game_math_jobs import Job

environment = recurring.environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
RNG = "rng:deal-fixture"
OUTPUT = "rng-assessment.json"

PROFILE = {
    "profileRef": "rng-assessment",
    "imageSourceRef": "moonmind",
    "entrypoint": ["python", "-m", "moonmind.security.rng_assessment"],
    "modes": [
        {
            "name": "evaluate",
            "actionClass": "rng-assessment",
            "argv": [
                "--request={request}",
                "--sample={sample}",
                "--outcomes={outcomes}",
                "--max-seconds={maxSeconds}",
                f"--output=/outputs/{OUTPUT}",
            ],
            "arguments": [
                {"name": "request", "type": "input", "required": True},
                {"name": "sample", "type": "input"},
                {"name": "outcomes", "type": "input"},
                {"name": "maxSeconds", "type": "integer", "minimum": 1},
            ],
        }
    ],
    "outputs": [{"name": "assessment", "relativePath": OUTPUT}],
    "maxCpuMillis": 2000,
    "maxMemoryMiB": 1024,
    "maxTimeoutSeconds": 3600,
}
REVIEW = {
    **{
        name: {"status": "no_concern_noted", "summary": f"{name} reviewed"}
        for name in (
            "generator",
            "seeding",
            "restartFork",
            "stateHandling",
            "predictableInputs",
        )
    },
    "transformations": [
        {"id": "deal", "kind": "modulo", "inputBits": 8, "range": 52},
    ],
}
# Public raw bytes, dealt to 52 cards by biased modulo.
SAMPLE = fixture_sample("sha256-counter", 5198, 25_000)
DEALT = [f"card{value % 52}" for value in SAMPLE]
REQUEST = {
    "schemaVersion": "rng-assessment-request/v1",
    "subjectRef": RNG,
    "review": REVIEW,
    "diagnostics": {
        "origin": {"kind": "recorded", "description": "deal service raw bytes"},
        "bits": len(SAMPLE) * 8,
    },
    "outcomes": {
        "description": "cards dealt by the deal service",
        "transformations": ["byte modulo 52"],
        "model": [{"id": f"card{i}", "probability": "1/52"} for i in range(52)],
    },
    "externalClaims": [{"claim": "rng_certification", "verifier": "laboratory"}],
}


class RngJob(Job):
    profile, schema, asset = PROFILE, b'"rng-assessment/v1"', RNG
    file_names = {"sample": "sample.bin", "outcomes": "outcomes.jsonl"}


def lines(values) -> bytes:
    return "\n".join(json.dumps({"outcome": value}) for value in values).encode()


async def test_profile_job_result_is_read_as_separate_rng_coverage(
    environment, tmp_path, monkeypatch
):
    job = RngJob(environment, tmp_path, monkeypatch)
    create, argv = await job.launch(
        "rng",
        {
            "request": "request/request.json",
            "sample": "sample/sample.bin",
            "outcomes": "outcomes/outcomes.jsonl",
        },
        {
            "request": json.dumps(REQUEST).encode(),
            "sample": SAMPLE,
            "outcomes": lines(DEALT),
        },
    )
    assert create[create.index("--network") + 1] == "none"
    assert argv == [
        "-m",
        "moonmind.security.rng_assessment",
        "--request=/inputs/request/request.json",
        "--sample=/inputs/sample/sample.bin",
        "--outcomes=/inputs/outcomes/outcomes.jsonl",
        f"--output=/outputs/{OUTPUT}",
    ]
    process = job.process(argv)
    _, stderr = process.communicate(timeout=300)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="succeeded", exit_code=0)
    body = job.published[ref]
    result = json.loads(body)

    # The same bits pass the battery while the dealt cards deviate.
    assert result["rawBits"]["outcome"] == "no_anomaly_detected"
    assert result["rawBits"]["sample"]["bits"] == len(SAMPLE) * 8
    assert result["mappedOutcomes"]["decision"] == "deviation_detected"
    assert result["mappedOutcomes"]["samples"] == len(DEALT)
    (step,) = result["implementation"]["transformations"]
    assert (step["distribution"], step["maxProbability"]) == ("biased", "5/256")
    assert result["externalClaims"][0]["status"] == "unverified"
    assert result["assurance"]["certification"] == "not_performed"
    # The recorded sample leaves the job only as its digest.
    assert SAMPLE[:16].hex().encode() not in body
    assert base64.b64encode(SAMPLE[:15]) not in body

    outputs = await job.assess(ref, "rng-review")
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["scanner"], reviewed["target"]) == ("rng", RNG)
    assert (reviewed["coverage"], reviewed["outcome"]) == ("complete", "findings")
    assert reviewed["verification"] == "not_performed"
    assert reviewed["assurance"]["cryptographicStrength"] == "not_established"
    assert "raw bits no_anomaly_detected" in reviewed["interpretation"]
    assert outputs["coverageStatus"] == "complete"
    assert outputs["counts"]["new"] == 2


async def test_cancelled_profile_job_keeps_partial_evidence(
    environment, tmp_path, monkeypatch
):
    job = RngJob(environment, tmp_path, monkeypatch)
    request = {
        **REQUEST,
        "review": {k: v for k, v in REVIEW.items() if k != "transformations"},
        "diagnostics": {
            "origin": {
                "kind": "fixture",
                "fixture": {"name": "sha256-counter", "seed": 5198},
            },
            "bits": 2**24,
        },
    }
    request.pop("outcomes")
    _, argv = await job.launch(
        "rng-cancel",
        {"request": "request/request.json"},
        {"request": json.dumps(request).encode()},
    )
    process = job.process(argv)
    # The CLI reports readiness after installing its stop handler.
    assert b"started" in process.stderr.readline()
    process.send_signal(signal.SIGTERM)
    _, stderr = process.communicate(timeout=120)
    assert process.returncode == 0, stderr.decode()
    ref = await job.publish(terminal_state="canceled", exit_code=None)
    result = json.loads(job.published[ref])
    raw = result["rawBits"]
    assert (raw["status"], raw["stopReason"], raw["outcome"]) == (
        "partial",
        "cancelled",
        "incomplete",
    )
    # SIGTERM can arrive before any test. Unrun tests stay listed, and decided
    # tests keep their statistics.
    assert [row["test"] for row in raw["tests"]] == [
        "frequency",
        "block-frequency",
        "runs",
        "serial",
        "cumulative-sums",
    ]
    assert "not_run" in {row["decision"] for row in raw["tests"]}
    assert all(
        bool(row["pValues"]) == (row["decision"] != "not_run") for row in raw["tests"]
    )
    assert (result["status"], result["outcome"]) == ("partial", "incomplete")
    # The review completed before the stop and is kept beside the partial run.
    assert result["implementation"]["coverage"] == "complete"

    outputs = await job.assess(ref, "rng-cancel-review")
    (reviewed,) = outputs["scannerSummaries"]
    assert (reviewed["coverage"], reviewed["outcome"]) == ("partial", "incomplete")
    assert reviewed["execution"] == {
        "rawBits": "cancelled",
        "mappedOutcomes": "not_requested",
    }
    assert "run_cancelled" in reviewed["limitations"]
    assert outputs["coverageStatus"] == "stale_or_partial"
