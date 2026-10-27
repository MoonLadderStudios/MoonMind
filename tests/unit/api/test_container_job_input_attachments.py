"""Artifact inputs are admitted under the job owner's read authority (#5036)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api_service.services.container_jobs import ContainerJobService
from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.security import repository_scanner_runner
from moonmind.security.repository_scanners import RepositoryScan, build_scanner_job

OWNER = OwnerIdentity(principalType="user", principalId="operator")


class _Artifacts:
    def __init__(self, *, status="complete", size=10) -> None:
        self.calls: list[tuple[str, str]] = []
        self.status = status
        self.size = size

    async def get_metadata(self, *, artifact_id, principal):
        self.calls.append((artifact_id, principal))
        if principal != "user:operator" or artifact_id != "art_01OWNED":
            raise PermissionError("artifact is not readable")
        artifact = SimpleNamespace(
            status=SimpleNamespace(value=self.status), size_bytes=self.size
        )
        return artifact, [], False, None


def _submission(*attachments) -> ContainerJobSubmitRequest:
    return ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "issue-5036",
            "source": {"source": "http"},
            "spec": {
                "image": "python:3.13",
                "workspaceRef": {"kind": "sandbox", "workspaceId": "run-1"},
                "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                "inputAttachments": list(attachments),
            },
        }
    )


def _service(artifacts) -> ContainerJobService:
    service = ContainerJobService.__new__(ContainerJobService)
    service._artifacts = artifacts
    return service


@pytest.mark.asyncio
async def test_owner_readable_complete_artifact_is_admitted():
    artifacts = _Artifacts()
    request = _submission(
        {"name": "image", "artifactRef": "art_01OWNED"},
        {"name": "source", "workspacePath": "."},
    )
    await _service(artifacts)._authorize_input_attachments(
        owner=OWNER, spec=request.spec
    )
    assert artifacts.calls == [("art_01OWNED", "user:operator")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("artifacts", "owner", "ref", "match"),
    [
        (_Artifacts(), OWNER, "art_01SIBLING", "not readable"),
        (
            _Artifacts(),
            OwnerIdentity(principalType="user", principalId="sibling"),
            "art_01OWNED",
            "not readable",
        ),
        (_Artifacts(status="pending_upload"), OWNER, "art_01OWNED", "not complete"),
        (_Artifacts(size=4 * 1024**3 + 1), OWNER, "art_01OWNED", "bound"),
        (None, OWNER, "art_01OWNED", "artifact store"),
    ],
)
async def test_unreadable_or_unusable_artifacts_are_refused_before_side_effects(
    artifacts, owner, ref, match
):
    service = _service(artifacts)
    # Refusal happens before durable identity: no repository or Temporal
    # dependency exists on this service.
    with pytest.raises(ValueError, match=match):
        await service.submit(
            owner=owner, request=_submission({"name": "image", "artifactRef": ref})
        )


@pytest.mark.parametrize(
    ("argv", "paths"),
    [
        (
            build_scanner_job(
                RepositoryScan(scanner="gitleaks", outputDirectory="reports")
            )["command"],
            {"source": "/inputs/source", "output": "/outputs/reports"},
        ),
        (
            build_scanner_job(
                RepositoryScan(scanner="gitleaks", outputDirectory="reports")
            )["command"][:-2],
            {},
        ),
    ],
)
def test_runner_reads_the_paths_its_admitted_command_names(monkeypatch, argv, paths):
    seen = {}

    def run_scanner(scan, **kwargs):
        seen.update({k: str(v) for k, v in kwargs.items()})
        return 0

    monkeypatch.setattr(repository_scanner_runner, "run_scanner", run_scanner)
    # ``python -I -m <module> <args>`` leaves the module path in argv[0].
    assert repository_scanner_runner.main(["runner", *argv[3:]]) == 0
    assert seen == paths
