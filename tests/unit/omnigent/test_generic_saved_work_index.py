"""Generic checkpoints can be published without retaining their source host."""

import hashlib
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.publish.saved_work_source import materialize_saved_work
from moonmind.schemas.saved_work_models import workspace_content_digest
from moonmind.workflows.temporal.runtime.workspace_archive import build_workspace_archive


def digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class Artifacts:
    def __init__(self):
        self.objects = {}
        self.writes = []
        self.corrupt = False

    async def read_bytes(self, ref):
        payload = self.objects[ref]
        return payload + b"corrupt" if self.corrupt and ref == "artifact:new" else payload

    async def write_bytes(self, **kwargs):
        self.writes.append(kwargs)
        self.objects["artifact:new"] = kwargs["payload"]
        return "artifact:new"


def fixture(tmp_path, target=b"report.md"):
    source = tmp_path / "source"
    source.mkdir()
    (source / "report.md").write_text("generated report\n")
    (source / os.fsdecode(target)).write_text("generated report\n")
    (source / "latest").symlink_to(os.fsdecode(target))
    (source / "image.bin").write_bytes(b"\x00\xff\x01")
    archive, entries = build_workspace_archive(source, high_security_mode=False)
    artifacts = Artifacts()
    artifacts.objects["artifact:archive"] = archive
    checkpoint = {
        "archiveRef": "artifact:archive", "archiveDigest": digest(archive),
        "entries": entries,
    }
    payload = json.dumps(checkpoint).encode()
    artifacts.objects["artifact:checkpoint"] = payload
    saved = {
        "archiveRef": "artifact:archive", "archiveDigest": digest(archive),
        "manifestRef": "artifact:checkpoint", "manifestDigest": digest(payload),
        "workspaceDigest": workspace_content_digest(entries),
    }
    request = SimpleNamespace(idempotency_key="attempt", step_execution=SimpleNamespace(
        workflow_id="workflow", run_id="run", logical_step_id="step", execution_ordinal=1,
    ))
    return source, artifacts, saved, request


@pytest.mark.asyncio
async def test_saved_index_retains_the_source_attempts_original_task(tmp_path):
    _source, artifacts, saved, request = fixture(tmp_path)
    request.step_execution.omnigent_execution_plan = SimpleNamespace(
        task_input_snapshot_ref="artifact:original-task",
        task_input_snapshot_digest="sha256:" + "a" * 64,
    )
    refs = await OmnigentWorkspacePublicationService(
        tmp_path,
        artifact_gateway=artifacts,
    )._index_saved_workspace(
        request, saved, manifest_bytes=artifacts.objects["artifact:checkpoint"]
    )
    manifest = json.loads(artifacts.objects[refs["savedWorkRef"]])
    assert manifest["source"]["taskInputSnapshotRef"] == "artifact:original-task"
    assert manifest["source"]["taskInputSnapshotDigest"] == "sha256:" + "a" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [b"report.md", b"report-\xff.md"])
async def test_generic_index_materializes_files_and_links_without_source(tmp_path, target):
    source, artifacts, saved, request = fixture(tmp_path, target)
    refs = await OmnigentWorkspacePublicationService(
        tmp_path, artifact_gateway=artifacts,
    )._index_saved_workspace(request, saved, manifest_bytes=artifacts.objects["artifact:checkpoint"])
    shutil.rmtree(source)

    async def read(ref, _types):
        return await artifacts.read_bytes(ref)

    result = await materialize_saved_work(
        read=read, saved_work_ref=refs["savedWorkRef"],
        saved_work_digest=refs["savedWorkDigest"], root=tmp_path / "restored",
    )
    assert (result.root / "report.md").read_text() == "generated report\n"
    assert (result.root / "latest").is_symlink()
    assert os.readlink(os.fsencode(result.root / "latest")) == target
    assert (result.root / "image.bin").read_bytes() == b"\x00\xff\x01"
    manifest = json.loads(artifacts.objects[refs["savedWorkRef"]])
    assert "git" not in manifest
    assert manifest["requiredFormats"] == ["full_snapshot"]
    assert manifest["scan"]["disposition"] in {"unknown", "not_scanned"}
    assert manifest["limitations"]
    self_digest = manifest.pop("manifestDigest")
    assert self_digest == digest(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    assert len(artifacts.writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["manifest_bytes", "archive_identity", "workspace_identity"])
async def test_generic_index_failure_never_returns_saved_reference(tmp_path, failure):
    _source, artifacts, saved, request = fixture(tmp_path)
    if failure == "manifest_bytes":
        artifacts.objects["artifact:checkpoint"] += b"corrupt"
    elif failure == "archive_identity":
        saved["archiveDigest"] = "sha256:" + "0" * 64
    elif failure == "workspace_identity":
        saved["workspaceDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        await OmnigentWorkspacePublicationService(
            tmp_path, artifact_gateway=artifacts,
        )._index_saved_workspace(request, saved, manifest_bytes=artifacts.objects["artifact:checkpoint"])
    assert artifacts.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "write", "read", "mismatch", "lookup"])
async def test_durable_saved_index_is_linked_only_after_verified_readback(monkeypatch, failure):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from moonmind.omnigent.bridge_artifacts import (
        OmnigentArtifactError, TemporalOmnigentArtifactGateway,
    )
    from moonmind.workflows.temporal import artifacts as artifact_module

    payload = b'{"outputs": [], "requiredFormats": []}'
    service = SimpleNamespace(
        put_content_addressed_payload_complete=AsyncMock(
            return_value=(SimpleNamespace(artifact_id="art_saved"), False)
        ),
        read=AsyncMock(return_value=(None, payload)),
        link_artifact=AsyncMock(),
        list_for_execution=AsyncMock(return_value=[]),
    )
    if failure == "lookup":
        service.list_for_execution.side_effect = OSError("lookup failed")
    elif failure == "write":
        service.put_content_addressed_payload_complete.side_effect = OSError("write failed")
    elif failure == "read":
        service.read.side_effect = OSError("read failed")
    elif failure == "mismatch":
        service.read.return_value = (None, b"different")
    monkeypatch.setattr(artifact_module, "TemporalArtifactService", lambda _repo: service)

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace()

    gateway = TemporalOmnigentArtifactGateway(sessions)
    monkeypatch.setattr(gateway, "_execution_link", lambda *args, **kwargs: SimpleNamespace(
        namespace="default", workflow_id="workflow", run_id="run",
    ))
    call = gateway.write_bytes(
        request=SimpleNamespace(), name="saved_work_manifest", payload=payload,
        content_type="application/vnd.moonmind.saved-work-manifest+json;version=1",
        link_type="checkpoint.saved_work",
    )
    if failure:
        with pytest.raises((OSError, OmnigentArtifactError)):
            await call
        service.link_artifact.assert_not_awaited()
    else:
        assert await call == "artifact:art_saved"
        service.link_artifact.assert_awaited_once()
    if failure == "lookup":
        service.put_content_addressed_payload_complete.assert_not_awaited()
        return
    kwargs = service.put_content_addressed_payload_complete.call_args.kwargs
    assert "link" not in kwargs
    assert kwargs["metadata_json"]["artifact_kind"] == "saved_work_manifest"


@pytest.mark.asyncio
async def test_checkpoint_retry_reuses_durable_objects_before_saved_index(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.workflows.temporal import artifacts as artifact_module
    from moonmind.workflows.temporal.artifacts import ExecutionRef
    from tests.unit.publish.test_saved_work_publication_journey import _artifact_service

    async with _artifact_service(tmp_path) as service:
        monkeypatch.setattr(artifact_module, "TemporalArtifactService", lambda _repo: service)

        @asynccontextmanager
        async def sessions():
            yield SimpleNamespace()

        gateway = TemporalOmnigentArtifactGateway(sessions)
        request = SimpleNamespace(correlation_id="workflow", step_execution=None)
        monkeypatch.setattr(gateway, "_execution_link", lambda *args, **kwargs: ExecutionRef(
            namespace="default", workflow_id="workflow", run_id="run", link_type="evidence.recovery",
        ))
        first = await gateway.write_bytes(
            request=request, name="checkpoint_archive", payload=b"unchanged captured bytes",
            content_type="application/vnd.moonmind.worktree-archive", link_type="evidence.recovery",
        )
        second = await gateway.write_bytes(
            request=request, name="checkpoint_archive", payload=b"unchanged captured bytes",
            content_type="application/vnd.moonmind.worktree-archive", link_type="evidence.recovery",
        )
        assert second == first


@pytest.mark.asyncio
async def test_lost_saved_link_ack_reuses_one_saved_result(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
    from moonmind.workflows.temporal import artifacts as artifact_module
    from tests.unit.omnigent.test_legacy_github_host_recovery import _recovery_fixture
    from tests.unit.publish.test_saved_work_publication_journey import _artifact_service

    fixture = await _recovery_fixture(tmp_path, monkeypatch)
    async with _artifact_service(tmp_path) as service:
        monkeypatch.setattr(artifact_module, "TemporalArtifactService", lambda _repo: service)

        @asynccontextmanager
        async def sessions():
            yield SimpleNamespace()

        gateway = TemporalOmnigentArtifactGateway(sessions)
        publisher = OmnigentWorkspacePublicationService(tmp_path, artifact_gateway=gateway)
        link = service.link_artifact
        lost = False

        async def lost_ack(**kwargs):
            nonlocal lost
            await link(**kwargs)
            if kwargs["execution_ref"].link_type == "checkpoint.saved_work" and not lost:
                lost = True
                raise OSError("worker lost after saved index was linked")

        monkeypatch.setattr(service, "link_artifact", lost_ack)
        with pytest.raises(OSError, match="worker lost"):
            await publisher.save_request_workspace(fixture.request)
        saved = await publisher.save_request_workspace(fixture.request)
        listed = await service.list_for_execution(
            namespace="default", workflow_id="workflow-1", run_id="run-1",
            principal="service:omnigent-generic-host",
        )
        indexes = [item for item in listed if item.content_type.startswith("application/vnd.moonmind.saved-work-manifest")]
        assert len(indexes) == 1
        assert saved["savedWorkRef"] == "artifact:" + indexes[0].artifact_id
