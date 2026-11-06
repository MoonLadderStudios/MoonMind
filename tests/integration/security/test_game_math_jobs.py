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
from pathlib import Path
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
from tests.integration.security.test_admitted_assessment_journeys import publisher
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


def settings(profile=PROFILE):
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
            WORKLOAD_PROFILES_ENV_KEY: json.dumps([profile]),
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

    profile, schema, asset = PROFILE, b'"game-math-assessment/v1"', GAME
    #: Input attachment file names other than ``<name>.json``.
    file_names: dict[str, str] = {}

    def __init__(self, environment, tmp_path, monkeypatch):
        self.environment, self.tmp_path = environment, tmp_path
        self.monkeypatch = monkeypatch
        self.settings = settings(self.profile)
        self.temporal = AsyncMock()
        self.commands: list[tuple] = []
        self.published: dict[str, bytes] = {}

    async def launch(self, key: str, arguments: dict, inputs: dict[str, bytes]):
        attachments = [
            {
                "name": name,
                "artifactRef": await owned(self.environment, body),
                "fileName": self.file_names.get(name, f"{name}.json"),
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
                            "profileRef": self.profile["profileRef"],
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

        self.backend = DockerContainerJobBackend(
            workspace_root=self.tmp_path,
            command_runner=daemon,
            evidence_publisher=publisher(self.environment, self.published),
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

    async def publish(self, *, terminal_state: str, exit_code: int | None) -> str:
        self.request.container_ref = "fixture-container"
        self.request.terminal_state = terminal_state
        self.request.exit_code = exit_code
        await self.backend.publish_evidence(self.request)
        return next(ref for ref, body in self.published.items() if self.schema in body)

    async def assess(self, ref: str, key: str) -> dict:
        return await invoke(
            self.environment.service,
            {
                "scope": {"assets": [self.asset]},
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
