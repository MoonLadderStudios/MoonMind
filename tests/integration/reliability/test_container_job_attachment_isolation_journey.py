"""Real-container isolation of captured inputs from owned outputs (MoonMind#5036).

Drives the production ``DockerContainerJobBackend`` create/start/publish/
remove/cleanup path against the real daemon: the workload's attempted write to
its input fails, an upstream edit made after capture never reaches the job,
the declared report is collected from the job-owned output root with a hash
that matches the published bytes, and job storage is gone after cleanup while
the published report remains. Docker-backed CI only; skips without a daemon.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from moonmind.config.container_backend_settings import (
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobActivityRequest,
)
from moonmind.workflows.temporal.container_job_attachments import storage_for
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
    FilesystemCapacityAdmissionLock,
)
from tests.integration.reliability.test_container_job_plan_a_qualification_journey import (
    _REAL_TEST_IMAGE,
    _real_docker_run,
    _require_real_docker_image,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.reliability_journey,
    pytest.mark.asyncio,
]

# The workload tries to modify its input, then reports what it saw and proves
# its output and scratch locations are writable.
_WORKLOAD = (
    "if echo tampered > /inputs/source/app.txt 2>/dev/null; "
    "then echo mutated > /outputs/mutation.txt; "
    "else echo refused > /outputs/mutation.txt; fi; "
    "cat /inputs/source/app.txt > /outputs/report.txt; "
    "echo scratch > /tmp/scratch.txt && echo writable >> /outputs/report.txt"
)


async def test_real_container_reads_captured_input_and_writes_owned_output(
    tmp_path: Path,
) -> None:
    _require_real_docker_image()
    job_id = "container-job:" + uuid.uuid4().hex
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    (workspace / "app.txt").write_text("original\n")
    request = ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": job_id,
                "source": {"source": "workflow", "workflowId": "mm:5036"},
                "spec": {
                    "image": _REAL_TEST_IMAGE,
                    "workspaceRef": {"kind": "sandbox", "workspaceId": "run-1"},
                    "inputAttachments": [{"name": "source", "workspacePath": "."}],
                    "entrypoint": ["sh"],
                    "command": ["-c", _WORKLOAD],
                    "networkMode": "none",
                    "resources": {"cpuMillis": 1000, "memoryMiB": 256},
                    "timeoutSeconds": 120,
                    "outputs": [
                        {"name": "report", "relativePath": "report.txt"},
                        {"name": "mutation", "relativePath": "mutation.txt"},
                    ],
                },
            },
            "resolvedWorkspaceRef": str(workspace),
            "resolvedImageRef": _REAL_TEST_IMAGE,
        }
    )
    published: dict[str, bytes] = {}

    async def publish(_request: Any, name: str, payload: bytes) -> str:
        ref = f"art_{len(published):04d}"
        published[ref] = payload
        return ref

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=_real_docker_run,
        capacity_lock=FilesystemCapacityAdmissionLock(tmp_path / "locks"),
        evidence_publisher=publish,
        settings=resolve_container_backend_settings(
            {"MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS": "8"}
        ),
    )
    name = DockerContainerJobBackend._name(request)
    storage = storage_for(tmp_path, job_id)
    try:
        created = await backend.create_container(request)
        request.container_ref = created.container_ref
        # Concurrent edits to the upstream working copy after capture.
        (workspace / "app.txt").write_text("edited concurrently\n")
        (workspace / "late.txt").write_text("added later\n")
        await backend.start_container(request)
        code, stdout, stderr = await _real_docker_run(("wait", name))
        assert code == 0, stderr
        assert stdout.strip() == b"0"
        request.terminal_state = "succeeded"
        request.exit_code = 0

        evidence = await backend.publish_evidence(request)
        page = json.loads(published[evidence.artifacts_ref])
        outputs = {item["name"]: item for item in page["artifacts"]}
        assert {item["collectionStatus"] for item in outputs.values()} == {"collected"}
        report = published[outputs["report"]["artifactRef"]]
        assert report == b"original\nwritable\n"
        assert outputs["report"]["sha256"] == hashlib.sha256(report).hexdigest()
        assert published[outputs["mutation"]["artifactRef"]] == b"refused\n"
        assert (workspace / "app.txt").read_text() == "edited concurrently\n"
        assert (storage.inputs / "source" / "app.txt").read_text() == "original\n"
        assert not (storage.inputs / "source" / "late.txt").exists()
    finally:
        request.publication = AuxiliaryOutcome(state="succeeded")
        await backend.remove_container(request)
        cleaned = await backend.cleanup(request)
        await _real_docker_run(("rm", "--force", name))

    assert cleaned.cleanup_succeeded is True
    assert not storage.root.exists()
    # The published report outlives the container and its storage.
    assert published[outputs["report"]["artifactRef"]] == b"original\nwritable\n"
