"""Saved output publication verifies dependency authority before it is listed."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from api_service.db.models import TemporalArtifactLink
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.unit.omnigent.test_generic_saved_work_index import fixture
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
    _complete_artifact,
    _service,
    temporal_db,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("owner", ["foreign-owner", "service:omnigent-generic-host"])
async def test_saved_index_rejects_foreign_archive_even_with_matching_digest(
    tmp_path, monkeypatch, owner
):
    _, objects, saved, request = fixture(tmp_path)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            artifact = await _complete_artifact(
                _service(session, tmp_path),
                principal=owner,
                payload=objects.objects["artifact:archive"],
            )
            saved["archiveRef"] = f"artifact:{artifact.artifact_id}"
        checkpoint = json.loads(objects.objects["artifact:checkpoint"])
        checkpoint["archiveRef"] = saved["archiveRef"]
        manifest_bytes = json.dumps(checkpoint).encode()
        saved["manifestDigest"] = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        gateway = TemporalOmnigentArtifactGateway(sessions)
        publisher = OmnigentWorkspacePublicationService(
            tmp_path, artifact_gateway=gateway
        )
        with pytest.raises(TemporalArtifactAuthorizationError):
            await publisher._index_saved_workspace(
                request, saved, manifest_bytes=manifest_bytes
            )
        async with sessions() as session:
            assert not list(
                (
                    await session.scalars(
                        select(TemporalArtifactLink).where(
                            TemporalArtifactLink.link_type == "checkpoint.saved_work"
                        )
                    )
                ).all()
            )


@pytest.mark.parametrize("optional_missing", [False, True])
async def test_saved_index_seals_exact_output_and_preserves_optional_absence(
    tmp_path, monkeypatch, optional_missing
):
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

    _, objects, saved, request = fixture(tmp_path)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    async with temporal_db(tmp_path) as sessions:
        gateway = TemporalOmnigentArtifactGateway(sessions)
        saved["archiveRef"] = await gateway.write_bytes(
            request=request,
            name="checkpoint_archive",
            payload=objects.objects["artifact:archive"],
            link_type="evidence.recovery",
            content_type="application/octet-stream",
        )
        checkpoint = json.loads(objects.objects["artifact:checkpoint"])
        checkpoint["archiveRef"] = saved["archiveRef"]
        manifest_bytes = json.dumps(checkpoint).encode()
        saved["manifestDigest"] = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        if optional_missing:
            request.omnigent_execution_plan = SimpleNamespace(
                task_input_snapshot_ref="artifact:art_unavailable_source",
                task_input_snapshot_digest="sha256:" + "a" * 64,
            )
        publisher = OmnigentWorkspacePublicationService(
            tmp_path, artifact_gateway=gateway
        )
        result = await publisher._index_saved_workspace(
            request, saved, manifest_bytes=manifest_bytes
        )
        root_id = result["savedWorkRef"].removeprefix("artifact:")
        archive_id = saved["archiveRef"].removeprefix("artifact:")
        async with sessions() as session:
            service = _service(session, tmp_path)
            closure = await service.verified_dependencies(
                root_artifact_id=root_id,
                access=ArtifactAccessContext.for_service(
                    "service:omnigent-generic-host"
                ),
            )
            assert closure is not None
            archive = next(
                item for item in closure.dependencies if item.artifact_id == archive_id
            )
            assert archive.required
            assert archive.sha256.removeprefix("sha256:") == saved[
                "archiveDigest"
            ].removeprefix("sha256:")
            if optional_missing:
                assert closure.unavailable_optional == ("art_unavailable_source",)
            else:
                assert not closure.unavailable_optional
            assert await gateway.read_bytes(result["savedWorkRef"])


async def test_managed_capture_publishes_only_sealed_output_dependencies(tmp_path):
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
    from tests.unit.workflows.temporal.test_saved_work_capture import (
        _artifact_capture_harness,
        _request,
    )

    async with _artifact_capture_harness(tmp_path) as (
        _,
        activities,
        service,
        session,
        digest,
    ):
        result = await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=digest)
        )
        root_id = (
            result["savedWorkRef"].removeprefix("artifact://").removeprefix("artifact:")
        )
        closure = await service.verified_dependencies(
            root_artifact_id=root_id, access=ArtifactAccessContext.for_service("system")
        )
        assert closure is not None
        assert any(
            entry.required and entry.role == "saved_work.output.full_snapshot"
            for entry in closure.dependencies
        )
        links = list(
            (
                await session.scalars(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.artifact_id == root_id
                    )
                )
            ).all()
        )
        assert links and all(link.workflow_id == "wf-1" for link in links)


@pytest.mark.parametrize("historical_git_only", [False, True])
async def test_retained_saved_work_keeps_optional_delta_needed_for_publication(
    tmp_path, historical_git_only
):
    from datetime import UTC, datetime, timedelta

    from api_service.db.models import TemporalArtifact
    from moonmind.publish.saved_work_source import (
        SAVED_WORK_MANIFEST_CONTENT_TYPE,
        materialize_saved_work,
        parse_saved_work_publication_manifest,
        saved_work_artifact_id,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactStateError,
    )
    from moonmind.workflows.temporal.saved_work_dependencies import (
        verify_saved_work_dependencies,
    )
    from tests.unit.workflows.temporal.test_saved_work_capture import (
        _artifact_capture_harness,
        _request,
    )

    access = ArtifactAccessContext.for_service("system")
    async with _artifact_capture_harness(tmp_path) as (
        repo,
        activities,
        service,
        session,
        digest,
    ):
        (repo / "tracked.txt").unlink()
        (repo / "added.txt").write_text("retained output\n")
        result = await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=digest)
        )
        root_id = saved_work_artifact_id(result["savedWorkRef"])
        root, payload = await service.read(artifact_id=root_id, access=access)
        manifest = json.loads(payload)
        assert "exact_baseline_delta" not in manifest["requiredFormats"]
        assert manifest["git"]["deltaRef"]
        if historical_git_only:
            manifest["outputs"] = [
                item
                for item in manifest["outputs"]
                if item["format"] != "exact_baseline_delta"
            ]
            payload = json.dumps(manifest).encode()
            root = await _complete_artifact(
                service,
                principal="system",
                payload=payload,
                content_type=SAVED_WORK_MANIFEST_CONTENT_TYPE,
            )
            root_id = root.artifact_id
        closure = await verify_saved_work_dependencies(
            service, artifact=root, payload=payload, access=access
        )
        _, required_objects = parse_saved_work_publication_manifest(payload)
        assert len(required_objects) == 2
        for item in required_objects:
            assert any(
                entry.artifact_id == saved_work_artifact_id(item.ref)
                and entry.required
                and entry.sha256 == item.digest.removeprefix("sha256:")
                for entry in closure.dependencies
            )
        delta_id = saved_work_artifact_id(manifest["git"]["deltaRef"])
        delta = await session.get(TemporalArtifact, delta_id)
        delta.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        with pytest.raises(TemporalArtifactStateError, match="retained"):
            await service.soft_delete(artifact_id=delta_id, principal="system")

        async def read(ref, content_types):
            artifact, body = await service.read(
                artifact_id=saved_work_artifact_id(ref), access=access
            )
            assert artifact.content_type in content_types
            return body

        content = await materialize_saved_work(
            read=read,
            saved_work_ref=f"artifact:{root_id}",
            saved_work_digest="sha256:" + root.sha256,
            root=tmp_path / "materialized",
        )
        assert content.recorded_deletions == ("tracked.txt",)
        assert (content.root / "added.txt").read_text() == "retained output\n"


@pytest.mark.parametrize("nested", [False, True])
async def test_retained_checkpoint_restores_expired_submodule_objects(tmp_path, nested):
    import shutil
    from datetime import UTC, datetime, timedelta

    from api_service.db.models import TemporalArtifact
    from moonmind.publish.saved_work_source import saved_work_artifact_id
    from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
    from moonmind.workflows.temporal.activity_runtime import TemporalSandboxActivities
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactStateError,
    )
    from moonmind.workflows.temporal.runtime.checkpoint_restore import (
        ManagedCheckpointRestoreService,
    )
    from tests.unit.workflows.temporal.test_checkpoint_restore import _request
    from tests.unit.workflows.temporal.test_checkpoint_submodules import (
        capture_checkpoint,
        git,
        initialize_submodule,
        repository_with_submodule,
    )

    remote, source = repository_with_submodule(tmp_path)
    module = initialize_submodule(source, tmp_path / "module-origin")
    repositories = [module]
    if nested:
        git(
            module,
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(tmp_path / "module-origin"),
            "nested",
        )
        git(
            module,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-am",
            "Nested dependency",
        )
        repositories.append(module / "nested")
    for repo in repositories:
        (repo / "tracked.txt").write_text("staged recovery bytes\n")
        git(repo, "add", "tracked.txt")
        (repo / "tracked.txt").write_text("unstaged recovery bytes\n")
    base = git(source, "rev-parse", "HEAD")
    access = ArtifactAccessContext.for_service("system")
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        activities = TemporalSandboxActivities(
            artifact_service=service, workspace_root=source.parent.parent
        )
        capture = await capture_checkpoint(source, InMemoryArtifactStore(), activities)
        assert capture["status"] == "captured", capture
        _, payload = await service.read(
            artifact_id=saved_work_artifact_id(capture["workspace"]["manifestRef"]),
            access=access,
        )
        manifest = json.loads(payload)
        modules = manifest["git"]["submodules"]
        assert len(modules) == len(repositories)
        checkpoint_ref = await activities._put_checkpoint_bytes(
            json.dumps(
                {
                    "contentType": "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
                    "source": {
                        "workflowId": "source",
                        "runId": "source-run",
                        "logicalStepId": "implement",
                        "executionOrdinal": 1,
                    },
                    "boundary": "before_execution",
                    "workspace": capture["workspace"],
                }
            ).encode(),
            content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
            metadata={"artifact_kind": "step_execution_checkpoint"},
        )
        parts = [
            part
            for module in modules
            for part in (module["gitHistory"], module["git"]["indexPatch"])
        ]
        for part in parts:
            row = await session.get(
                TemporalArtifact, saved_work_artifact_id(part["ref"])
            )
            row.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        for part in parts:
            with pytest.raises(TemporalArtifactStateError, match="retained"):
                await service.soft_delete(
                    artifact_id=saved_work_artifact_id(part["ref"]), principal="system"
                )
        request = _request(checkpoint_ref=checkpoint_ref, capture=capture, base=base)
        shutil.rmtree(source)
        shutil.rmtree(tmp_path / "module-origin")
        restore = ManagedCheckpointRestoreService(
            authority_root=tmp_path / "authority",
            artifact_service=service,
            repository_source_root=remote,
        )
        await restore.restore(request, access=access)
        for module in modules:
            restored = tmp_path / "authority" / "new-run" / "repo" / module["path"]
            assert git(restored, "rev-parse", "HEAD") == module["baseCommit"]
            assert git(restored, "show", ":tracked.txt") == "staged recovery bytes"
            assert (restored / "tracked.txt").read_text() == "unstaged recovery bytes\n"


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("caller_owned", [False, True])
@pytest.mark.parametrize("restricted", [False, True])
async def test_checkpoint_activity_uses_execution_authority_not_shared_system_creator(
    tmp_path, admitted, caller_owned, restricted
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from temporalio.testing import ActivityEnvironment

    from api_service.db.models import TemporalArtifact, TemporalArtifactRedactionLevel
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalCheckpointActivities,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactTransactionOwnership,
        ExecutionRef,
    )

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        source = await _complete_artifact(
            service,
            principal="system",
            payload=b'{"objective":"original"}',
            redaction_level=TemporalArtifactRedactionLevel.RESTRICTED
            if restricted
            else TemporalArtifactRedactionLevel.NONE,
        )
        if admitted:
            await service.link_artifact(
                artifact_id=source.artifact_id,
                principal="system",
                execution_ref=ExecutionRef(
                    namespace="default",
                    workflow_id="checkpoint-owner",
                    run_id="run",
                    link_type="input",
                ),
            )
        environment = ActivityEnvironment()
        environment.info = replace(
            environment.info, workflow_id="checkpoint-owner", workflow_run_id="run"
        )
        request = {
            "identity": {
                "workflowId": "checkpoint-owner",
                "runId": "run",
                "logicalStepId": "step",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "taskInputSnapshotRef": f"artifact:{source.artifact_id}",
            "workspace": {"kind": "git_commit", "headCommit": "a" * 40},
            "createdAt": datetime.now(UTC).isoformat(),
            "idempotencyKey": "checkpoint",
            "planDigest": "sha256:" + "b" * 64,
        }
        if caller_owned:
            service = _service(
                session,
                tmp_path,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
        activities = TemporalCheckpointActivities(artifact_service=service)
        if admitted:
            result = await environment.run(activities.step_checkpoint_create, request)
            assert result["checkpointRef"]
            if restricted:
                from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

                with pytest.raises(TemporalArtifactAuthorizationError):
                    await service.read(
                        artifact_id=source.artifact_id,
                        access=ArtifactAccessContext.for_execution(
                            "checkpoint-owner", principal="system"
                        ),
                    )
            if caller_owned:
                root_id = (
                    result["checkpointRef"]
                    .removeprefix("artifact://")
                    .removeprefix("artifact:")
                )
                async with sessions() as observer:
                    assert await observer.get(TemporalArtifact, root_id) is None
                await session.rollback()
                async with sessions() as observer:
                    assert await observer.get(TemporalArtifact, root_id) is None
        else:
            with pytest.raises(TemporalArtifactAuthorizationError):
                await environment.run(activities.step_checkpoint_create, request)
            if caller_owned:
                assert session.in_transaction()
                await session.rollback()
            assert not list(
                (
                    await session.scalars(
                        select(TemporalArtifactLink).where(
                            TemporalArtifactLink.workflow_id == "checkpoint-owner"
                        )
                    )
                ).all()
            )


async def test_managed_sealed_upload_remains_unpublished_when_final_link_fails(
    tmp_path, monkeypatch
):
    from api_service.db.models import TemporalArtifact
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
    from tests.unit.workflows.temporal.test_saved_work_capture import (
        _artifact_capture_harness,
        _request,
    )

    async with _artifact_capture_harness(tmp_path) as (
        _,
        activities,
        service,
        session,
        digest,
    ):
        original_link = service.link_artifact

        async def fail_saved_link(**kwargs):
            artifact = await session.get(TemporalArtifact, kwargs["artifact_id"])
            if (artifact.metadata_json or {}).get(
                "artifact_kind"
            ) == "saved_work_manifest":
                raise ConnectionError("final saved-result link unavailable")
            return await original_link(**kwargs)

        monkeypatch.setattr(service, "link_artifact", fail_saved_link)
        with pytest.raises(ConnectionError, match="final saved-result"):
            await activities.agent_runtime_capture_workspace_checkpoint(
                _request(digest=digest)
            )
        roots = [
            artifact
            for artifact in (await session.scalars(select(TemporalArtifact))).all()
            if (artifact.metadata_json or {}).get("artifact_kind")
            == "saved_work_manifest"
        ]
        assert roots
        for root in roots:
            assert root.expires_at is not None
            assert not list(
                (
                    await session.scalars(
                        select(TemporalArtifactLink).where(
                            TemporalArtifactLink.artifact_id == root.artifact_id
                        )
                    )
                ).all()
            )
            closure = await service.verified_dependencies(
                root_artifact_id=root.artifact_id,
                access=ArtifactAccessContext.for_service("system"),
            )
            assert closure is not None and closure.dependencies


async def test_restore_reads_frozen_closure_not_only_manifest_json(tmp_path):
    from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        ArtifactDependency,
    )
    from moonmind.workflows.temporal.runtime.checkpoint_restore import (
        ManagedCheckpointRestoreService,
    )

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        access = ArtifactAccessContext.for_service("owner")
        child = await _complete_artifact(service, principal="owner")
        root = await _complete_artifact(
            service,
            principal="owner",
            payload=b"{}",
            content_type="application/json",
            metadata_json={"artifact_kind": "checkpoint_manifest"},
        )
        await service.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[ArtifactDependency(child.artifact_id, child.sha256)],
            access=access,
        )
        service._store.delete(child.storage_key)
        restore = ManagedCheckpointRestoreService(
            authority_root=tmp_path / "restore", artifact_service=service
        )
        with pytest.raises(CheckpointRestoreError):
            await restore._read(
                root.artifact_id, content_types={"application/json"}, access=access
            )


@pytest.mark.parametrize("corrupt_seal", [False, True])
async def test_restore_backfills_only_unsealed_original_manifest(
    tmp_path, corrupt_seal
):
    from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
    from moonmind.workflows.temporal.runtime.checkpoint_restore import (
        ManagedCheckpointRestoreService,
    )

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        access = ArtifactAccessContext.for_service("owner")
        child = await _complete_artifact(service, principal="owner")
        payload = json.dumps(
            {
                "archive": {
                    "ref": "artifact:" + child.artifact_id,
                    "sha256": child.sha256,
                }
            }
        ).encode()
        root = await _complete_artifact(
            service,
            principal="owner",
            payload=payload,
            content_type="application/json",
            metadata_json={"artifact_kind": "checkpoint_manifest"},
        )
        if corrupt_seal:
            root.dependency_closure_version = 1
            root.dependency_closure_sha256 = "f" * 64
            await session.commit()
        restore = ManagedCheckpointRestoreService(
            authority_root=tmp_path / "restore", artifact_service=service
        )
        if corrupt_seal:
            with pytest.raises(CheckpointRestoreError):
                await restore._read(
                    root.artifact_id,
                    content_types={"application/json"},
                    access=access,
                )
        else:
            assert (
                await restore._read(
                    root.artifact_id,
                    content_types={"application/json"},
                    access=access,
                )
                == payload
            )
            closure = await service.verified_dependencies(
                root_artifact_id=root.artifact_id, access=access
            )
            assert [
                (item.artifact_id, item.sha256) for item in closure.dependencies
            ] == [(child.artifact_id, child.sha256)]
            assert (
                await restore._read(
                    root.artifact_id,
                    content_types={"application/json"},
                    access=access,
                )
                == payload
            )


async def test_legacy_restore_backfill_freezes_nested_checkpoint_before_parent(
    tmp_path,
):
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
    from moonmind.workflows.temporal.saved_work_dependencies import (
        verify_saved_work_dependencies,
    )

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        access = ArtifactAccessContext.for_service("owner")
        child = await _complete_artifact(service, principal="owner")
        checkpoint_body = json.dumps(
            {
                "archive": {
                    "ref": "artifact:" + child.artifact_id,
                    "sha256": child.sha256,
                }
            }
        ).encode()
        checkpoint = await _complete_artifact(
            service,
            principal="owner",
            payload=checkpoint_body,
            content_type="application/json",
            metadata_json={"artifact_kind": "checkpoint_manifest"},
        )
        body = json.dumps(
            {
                "requiredFormats": ["checkpoint"],
                "outputs": [
                    {
                        "format": "checkpoint",
                        "ref": "artifact:" + checkpoint.artifact_id,
                        "digest": "sha256:" + checkpoint.sha256,
                    }
                ],
            }
        ).encode()
        root = await _complete_artifact(
            service,
            principal="owner",
            payload=body,
            content_type="application/vnd.moonmind.saved-work-manifest+json;version=1",
        )
        closure = await verify_saved_work_dependencies(
            service, artifact=root, payload=body, access=access
        )
        assert {item.artifact_id for item in closure.dependencies if item.required} == {
            checkpoint.artifact_id,
            child.artifact_id,
        }


async def test_sweep_backfills_pinned_original_run_before_expiring_its_archive(
    tmp_path,
):
    from datetime import UTC, datetime, timedelta

    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        ExecutionRef,
    )

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        child = await _complete_artifact(service, principal="system")
        body = json.dumps(
            {
                "archive": {
                    "ref": "artifact:" + child.artifact_id,
                    "sha256": child.sha256,
                }
            }
        ).encode()
        root = await _complete_artifact(
            service,
            principal="system",
            payload=body,
            content_type="application/json",
            metadata_json={"artifact_kind": "checkpoint_manifest"},
        )
        for item in (root, child):
            await service.link_artifact(
                artifact_id=item.artifact_id,
                principal="system",
                execution_ref=ExecutionRef(
                    namespace="default",
                    workflow_id="retained-workflow",
                    run_id="original-run",
                    link_type="output.checkpoint",
                ),
            )
        session.add(
            TemporalExecutionCanonicalRecord(
                namespace="default",
                workflow_id="retained-workflow",
                run_id="later-run",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id="operator",
                state=MoonMindWorkflowState.COMPLETED,
                entry="run",
            )
        )
        await session.commit()
        await service.pin(
            artifact_id=root.artifact_id,
            principal="system",
            reason="Retained original result",
        )
        child.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        await service.sweep_lifecycle(
            principal="lifecycle", now=datetime.now(UTC), limit=1
        )
        closure = await service.verified_dependencies(
            root_artifact_id=root.artifact_id,
            access=ArtifactAccessContext.for_service("system"),
        )
        assert closure is not None
        assert [(item.artifact_id, item.sha256) for item in closure.dependencies] == [
            (child.artifact_id, child.sha256)
        ]
        _, data = await service.read(
            artifact_id=child.artifact_id,
            access=ArtifactAccessContext.for_service("system"),
        )
        assert data == b"saved-work-bytes"


async def test_legacy_restore_cycle_is_unavailable_not_sealed_empty(tmp_path):
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        TemporalArtifactStateError,
    )
    from moonmind.workflows.temporal.saved_work_dependencies import (
        verify_saved_work_dependencies,
    )

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        root, _ = await service.create(
            principal="owner",
            content_type="application/json",
            metadata_json={"artifact_kind": "checkpoint_manifest"},
        )
        payload = json.dumps({"archiveRef": "artifact:" + root.artifact_id}).encode()
        root = await service.write_complete(
            artifact_id=root.artifact_id,
            principal="owner",
            payload=payload,
            content_type="application/json",
        )
        with pytest.raises(TemporalArtifactStateError, match="cycle/depth"):
            await verify_saved_work_dependencies(
                service,
                artifact=root,
                payload=payload,
                access=ArtifactAccessContext.for_service("owner"),
            )
        assert (
            await service._repository.get_verified_dependencies(root.artifact_id)
            is None
        )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize(
    "source_link,child_link,root_namespace,restricted",
    [
        (True, True, "default", False),
        (True, False, "default", False),
        (False, True, "default", False),
        (True, True, "foreign-namespace", False),
        (True, True, "default", True),
        (True, True, "default", "granted"),
    ],
)
async def test_publication_admission_uses_required_sealed_descendants_only(
    tmp_path, monkeypatch, source_link, child_link, root_namespace, restricted, legacy
):
    from fastapi import HTTPException

    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        ArtifactDependency,
        ExecutionRef,
    )
    from tests.unit.publish.test_saved_work_publication_journey import journey

    async with journey(tmp_path, monkeypatch, admit_publication=False) as state:
        service = state.service
        original_id = state.saved.saved_work_ref.removeprefix("artifact:").removeprefix(
            "//"
        )
        original = await service._repository.get_artifact(original_id)
        _, body = await service.read(
            artifact_id=original_id, access=ArtifactAccessContext.for_service("system")
        )
        extra = await _complete_artifact(
            service, principal="system", payload=b"required nested input"
        )
        if restricted:
            from api_service.db.models import TemporalArtifactRedactionLevel

            extra.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            await service._repository.commit()
        root = await _complete_artifact(
            service,
            principal="system",
            payload=body,
            content_type=original.content_type,
        )
        for artifact, linked in ((root, source_link), (extra, child_link)):
            if linked:
                await service.link_artifact(
                    artifact_id=artifact.artifact_id,
                    principal="system",
                    execution_ref=ExecutionRef(
                        namespace=root_namespace
                        if artifact.artifact_id == root.artifact_id
                        else "default",
                        workflow_id=state.saved.workflow_id,
                        run_id=state.saved.run_id,
                        link_type="output.checkpoint",
                    ),
                )
        if restricted == "granted":
            await service.delegate_read_access(
                artifact_ids=[extra.artifact_id],
                workflow_id=state.saved.workflow_id,
                namespace="default",
                access=ArtifactAccessContext.for_service("system"),
            )
        descriptors = list(
            await service._repository.get_verified_dependencies(original_id)
        )
        descriptors.append(ArtifactDependency(extra.artifact_id, extra.sha256))
        await service.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=descriptors,
            access=ArtifactAccessContext.for_service("system"),
        )
        state.saved.saved_work_ref = "artifact:" + root.artifact_id
        contract = state.contract(
            objective="branch",
            strategy="additive_import",
            baseBranch="main",
            expectedBaseSha="a" * 40,
        )
        target = (
            "mm:source:saved-work-publication:x"
            if legacy
            else "publication-with-nested-input"
        )

        async def admit():
            if not legacy:
                return await state.admit(
                    contract,
                    target,
                    access=ArtifactAccessContext.for_execution(state.saved.workflow_id),
                )
            from moonmind.workflows.temporal import publication_recovery

            async def recorded(_contract):
                return "default", False

            monkeypatch.setattr(
                publication_recovery, "_recorded_publication_admission", recorded
            )
            parsed = publication_recovery.SavedWorkPublicationContract.model_validate(
                contract
            )
            return await publication_recovery.recover_recorded_saved_work_inputs(
                service, parsed
            )

        if (
            source_link
            and child_link
            and root_namespace == "default"
            and (not restricted or restricted == "granted")
        ):
            await admit()
            links = await service._repository.list_links(extra.artifact_id)
            assert any(link.workflow_id == target for link in links)
            if restricted == "granted":
                assert any(
                    link.workflow_id == target
                    and link.namespace == "default"
                    and link.link_type == "input.artifact.raw"
                    for link in links
                )
        else:
            with pytest.raises(
                (HTTPException, TemporalArtifactAuthorizationError)
            ) as rejected:
                await admit()
            if isinstance(rejected.value, HTTPException):
                assert rejected.value.status_code in {404, 409}
            assert not any(
                link.workflow_id == target
                for link in await service._repository.list_links(extra.artifact_id)
            )
