"""Dependency admission reports contention without deadlocking caller transactions."""

import asyncio

import pytest
from sqlalchemy import select

from api_service.db.models import Base, TemporalArtifact
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ArtifactDependency,
    ArtifactTransactionOwnership,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
    TemporalArtifactUnavailableError,
)
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]
ACCESS = ArtifactAccessContext.for_service("service:dependency-owner")


def _service(session, store, *, standalone=False):
    return TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=store,
        transaction_ownership=(
            ArtifactTransactionOwnership.STANDALONE
            if standalone
            else ArtifactTransactionOwnership.CALLER
        ),
    )


async def _put(service, payload):
    artifact, _ = await service.create(
        principal=ACCESS.principal, content_type="text/plain"
    )
    return await service.write_complete(
        artifact_id=artifact.artifact_id, principal=ACCESS.principal, payload=payload
    )


async def _seed(sessions, store, *, sealed=False):
    async with sessions() as session:
        service = _service(session, store)
        child = await _put(service, b"child")
        root = await _put(service, b"root")
        assert child.artifact_id < root.artifact_id
        dependency = ArtifactDependency(child.artifact_id, child.sha256)
        if sealed:
            await service.record_verified_dependencies(
                root_artifact_id=root.artifact_id,
                dependencies=[dependency],
                access=ACCESS,
            )
        await session.commit()
        return root.artifact_id, dependency, child.storage_key


async def test_preheld_root_writer_and_dependency_reader_retry_without_deadlock(
    tmp_path,
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        root_id, dependency, _ = await _seed(sessions, store, sealed=True)
        root_held, child_held, writer_sealing = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        sentinels = []

        async def writer():
            async with sessions() as session:
                service = _service(session, store)
                await service.write_complete(
                    artifact_id=root_id, principal=ACCESS.principal, payload=b"root"
                )
                root_held.set()
                await child_held.wait()
                writer_sealing.set()
                try:
                    await service.record_verified_dependencies(
                        root_artifact_id=root_id,
                        dependencies=[dependency],
                        access=ACCESS,
                    )
                except TemporalArtifactUnavailableError as exc:
                    assert exc.retryable and exc.dependency == "authority"
                    assert await session.scalar(select(1)) == 1
                    await session.rollback()
                    return "retry"
                await session.commit()
                return "complete"

        async def reader():
            await root_held.wait()
            async with sessions() as session:
                service = _service(session, store)
                sentinel = await _put(service, b"uncommitted caller work")
                sentinels.append(sentinel.artifact_id)
                original = service._repository.get_artifact_for_update

                async def locked(artifact_id, **kwargs):
                    artifact = await original(artifact_id, **kwargs)
                    if artifact_id == dependency.artifact_id:
                        child_held.set()
                        await writer_sealing.wait()
                    return artifact

                service._repository.get_artifact_for_update = locked
                try:
                    closure = await service.verified_dependencies(
                        root_artifact_id=root_id, access=ACCESS
                    )
                    assert closure.dependencies == (dependency,)
                    outcome = "complete"
                except TemporalArtifactUnavailableError as exc:
                    assert exc.retryable and exc.dependency == "authority"
                    outcome = "retry"
                # A contention result must neither poison nor secretly rollback/commit
                # the caller's unrelated in-flight changes.
                assert await session.scalar(select(1)) == 1
                assert (
                    await session.get(TemporalArtifact, sentinel.artifact_id)
                    is sentinel
                )
                async with sessions() as observer:
                    assert (
                        await observer.get(TemporalArtifact, sentinel.artifact_id)
                        is None
                    )
                await session.rollback()
                return outcome

        outcomes = await asyncio.wait_for(
            asyncio.gather(writer(), reader()), timeout=10
        )
        assert "retry" in outcomes
        async with sessions() as retry:
            service = _service(retry, store)
            await service.record_verified_dependencies(
                root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
            )
            closure = await service.verified_dependencies(
                root_artifact_id=root_id, access=ACCESS
            )
            assert closure.dependencies == (dependency,)
            await retry.commit()
        async with sessions() as observer:
            assert await observer.get(TemporalArtifact, sentinels[0]) is None


@pytest.mark.parametrize("required", [True, False])
async def test_busy_dependency_is_retryable_not_missing_or_partially_sealed(
    tmp_path, required
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        root_id, dependency, _ = await _seed(sessions, store)
        dependency = ArtifactDependency(
            dependency.artifact_id, dependency.sha256, required
        )
        async with sessions() as holder, sessions() as caller:
            await TemporalArtifactRepository(holder).get_artifact_for_update(
                dependency.artifact_id
            )
            service = _service(caller, store)
            sentinel = await _put(service, b"unrelated pending write")
            with pytest.raises(TemporalArtifactUnavailableError) as caught:
                await service.record_verified_dependencies(
                    root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
                )
            assert caught.value.retryable
            assert await caller.scalar(select(1)) == 1
            assert await caller.get(TemporalArtifact, sentinel.artifact_id) is sentinel
            assert await service._repository.get_verified_dependencies(root_id) is None
            await caller.rollback()
            await holder.commit()
        async with sessions() as retry:
            service = _service(retry, store)
            result = await service.record_verified_dependencies(
                root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
            )
            assert result.dependencies == (dependency,)
            assert not result.unavailable_optional
            await retry.commit()


async def test_committed_producer_blocks_independent_sweeper(tmp_path):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        root_id, dependency, key = await _seed(sessions, store)
        deletion_started = asyncio.Event()
        async with sessions() as producer:
            await _service(producer, store).record_verified_dependencies(
                root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
            )

            async def sweep():
                async with sessions() as session:
                    sweeper = _service(session, store, standalone=True)
                    original = sweeper._repository.get_artifact_for_update

                    async def locking(artifact_id, **kwargs):
                        deletion_started.set()
                        return await original(artifact_id, **kwargs)

                    sweeper._repository.get_artifact_for_update = locking
                    with pytest.raises(TemporalArtifactStateError, match="retained"):
                        await sweeper.soft_delete(
                            artifact_id=dependency.artifact_id,
                            principal=ACCESS.principal,
                        )
                    await session.rollback()

            task = asyncio.create_task(sweep())
            await deletion_started.wait()
            await producer.commit()
            await asyncio.wait_for(task, timeout=10)
        assert store.object_exists(key)


async def test_sweeper_winner_cannot_be_attached_after_retry(tmp_path):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        root_id, dependency, key = await _seed(sessions, store)
        async with sessions() as deleting, sessions() as producing:
            await _service(deleting, store).soft_delete(
                artifact_id=dependency.artifact_id, principal=ACCESS.principal
            )
            service = _service(producing, store)
            with pytest.raises(TemporalArtifactUnavailableError):
                await service.record_verified_dependencies(
                    root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
                )
            assert await service._repository.get_verified_dependencies(root_id) is None
            await producing.rollback()
            await deleting.commit()
        async with sessions() as deletion:
            await _service(deletion, store, standalone=True).hard_delete(
                artifact_id=dependency.artifact_id, principal=ACCESS.principal
            )
        assert not store.object_exists(key)
        async with sessions() as retry:
            service = _service(retry, store)
            with pytest.raises(TemporalArtifactStateError):
                await service.record_verified_dependencies(
                    root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
                )
            assert await service._repository.get_verified_dependencies(root_id) is None


@pytest.mark.parametrize("fault", ["rollback", "storage"])
async def test_failed_dependency_admission_does_not_publish_or_delete_bytes(
    tmp_path, monkeypatch, fault
):
    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        root_id, dependency, key = await _seed(sessions, store)
        async with sessions() as producer:
            service = _service(producer, store)
            if fault == "storage":
                original = store.object_matches

                def unavailable(storage_key, **kwargs):
                    if storage_key == key:
                        raise TimeoutError("synthetic storage interruption")
                    return original(storage_key, **kwargs)

                monkeypatch.setattr(store, "object_matches", unavailable)
                with pytest.raises(TemporalArtifactUnavailableError):
                    await service.record_verified_dependencies(
                        root_artifact_id=root_id,
                        dependencies=[dependency],
                        access=ACCESS,
                    )
                assert await producer.scalar(select(1)) == 1
            else:
                await service.record_verified_dependencies(
                    root_artifact_id=root_id, dependencies=[dependency], access=ACCESS
                )
            await producer.rollback()
        async with sessions() as observer:
            assert (
                await TemporalArtifactRepository(observer).get_verified_dependencies(
                    root_id
                )
                is None
            )
            assert (
                await observer.get(TemporalArtifact, dependency.artifact_id) is not None
            )
        assert store.object_exists(key)


async def test_root_read_access_does_not_authorize_dependency_bytes(tmp_path):
    from moonmind.workflows.temporal.artifacts import (
        ExecutionRef,
        TemporalArtifactAuthorizationError,
    )

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    execution = ExecutionRef(
        namespace="dependency-test",
        workflow_id="consumer",
        run_id="run",
        link_type="input.plan",
    )
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        root_id, dependency, _ = await _seed(sessions, store, sealed=True)
        async with sessions() as producer:
            await _service(producer, store).link_artifact(
                artifact_id=root_id,
                principal=ACCESS.principal,
                execution_ref=execution,
            )
            await producer.commit()
        async with sessions() as reader:
            service = _service(reader, store)
            access = ArtifactAccessContext.for_execution(execution.workflow_id)
            assert (await service.read(artifact_id=root_id, access=access))[
                1
            ] == b"root"
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.verified_dependencies(
                    root_artifact_id=root_id, access=access
                )
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(artifact_id=dependency.artifact_id, access=access)
            assert await service._repository.list_links(dependency.artifact_id) == []


async def _legacy_pair(
    session,
    store,
    *,
    name,
    root_link="output.checkpoint",
    child_link=True,
    expired_root=False,
):
    import json
    from datetime import UTC, datetime, timedelta

    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )
    from moonmind.workflows.temporal.artifacts import ExecutionRef

    service = _service(session, store)
    child = await _put(service, name.encode())
    root, _ = await service.create(
        principal=ACCESS.principal,
        content_type="application/vnd.moonmind.managed-workspace-checkpoint-manifest+json;version=1",
    )
    root = await service.write_complete(
        artifact_id=root.artifact_id,
        principal=ACCESS.principal,
        payload=json.dumps(
            {
                "archive": {
                    "ref": "artifact:" + child.artifact_id,
                    "sha256": child.sha256,
                }
            }
        ).encode(),
    )
    session.add(
        TemporalExecutionCanonicalRecord(
            namespace="default",
            workflow_id=name,
            run_id="new-run",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id="operator",
            state=MoonMindWorkflowState.COMPLETED,
            entry="run",
        )
    )
    if root_link:
        await service.link_artifact(
            artifact_id=root.artifact_id,
            principal=ACCESS.principal,
            execution_ref=ExecutionRef(
                namespace="default",
                workflow_id=name,
                run_id="old-run",
                link_type=root_link,
            ),
        )
    if child_link:
        await service.link_artifact(
            artifact_id=child.artifact_id,
            principal=ACCESS.principal,
            execution_ref=ExecutionRef(
                namespace="default",
                workflow_id=name,
                run_id="old-run",
                link_type="input.context",
            ),
        )
    now = datetime.now(UTC)
    child.expires_at = now - timedelta(days=1)
    if expired_root:
        root.expires_at = now - timedelta(days=1)
    else:
        await service.pin(
            artifact_id=root.artifact_id,
            principal=ACCESS.principal,
            reason="Retained historical result",
        )
    await session.commit()
    return root.artifact_id, child.artifact_id


@pytest.mark.parametrize(
    "root_link,child_link,expired_root,protected",
    [
        ("output.checkpoint", True, False, True),
        ("input.context", True, False, False),
        (None, True, False, False),
        ("output.checkpoint", False, False, False),
        ("output.checkpoint", True, True, False),
    ],
)
async def test_legacy_guard_is_original_producer_run_only(
    tmp_path, root_link, child_link, expired_root, protected
):
    from datetime import UTC, datetime

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with (
        isolated_postgres(list(Base.metadata.sorted_tables)) as sessions,
        sessions() as session,
    ):
        root_id, child_id = await _legacy_pair(
            session,
            store,
            name="legacy",
            root_link=root_link,
            child_link=child_link,
            expired_root=expired_root,
        )
        service = _service(session, store)
        assert (
            await service._repository.has_saved_definition_reference(child_id)
            is protected
        )
        expired = await service._repository.list_expired_artifacts(
            now=datetime.now(UTC), limit=10
        )
        assert (child_id in {item.artifact_id for item in expired}) is (not protected)
        candidates = await service._repository.list_unsealed_saved_work_roots(
            now=datetime.now(UTC), limit=1
        )
        assert bool(candidates) is (not expired_root)
        if candidates:
            assert candidates[0][0] == root_id
            if root_link == "output.checkpoint":
                assert candidates[0][3] == "old-run"
            else:
                assert candidates[0][2] is None
        if not child_link:
            from moonmind.workflows.temporal.artifacts import (
                TemporalArtifactAuthorizationError,
            )

            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=child_id,
                    access=ArtifactAccessContext.for_execution("legacy"),
                )


async def test_legacy_backfill_page_restart_preserves_unvisited_old_run(tmp_path):
    from datetime import UTC, datetime

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        async with sessions() as session:
            first, _ = await _legacy_pair(session, store, name="first")
            second, child = await _legacy_pair(session, store, name="second")
            service = _service(session, store, standalone=True)
            assert await service._backfill_saved_work_dependencies(
                now=datetime.now(UTC), limit=1
            )
            assert (
                await service._repository.get_verified_dependencies(first) is not None
            )
            assert await service._repository.get_verified_dependencies(second) is None
            assert await service._repository.has_saved_definition_reference(child)
        # Simulate a process stopping after its first committed bounded page.
        async with sessions() as session:
            service = _service(session, store, standalone=True)
            assert not await service._backfill_saved_work_dependencies(
                now=datetime.now(UTC), limit=1
            )
            proof = await service._repository.get_verified_dependencies(second)
            assert proof is not None and [entry.artifact_id for entry in proof] == [
                child
            ]
            assert await service._repository.has_saved_definition_reference(child)


async def test_legacy_ownerless_first_page_does_not_starve_owned_root(tmp_path, caplog):
    from datetime import UTC, datetime, timedelta

    store = LocalTemporalArtifactStore(tmp_path / "bytes")
    async with (
        isolated_postgres(list(Base.metadata.sorted_tables)) as sessions,
        sessions() as session,
    ):
        ownerless, orphan_child = await _legacy_pair(
            session, store, name="ownerless", root_link=None
        )
        owned, child = await _legacy_pair(session, store, name="owned")
        service = _service(session, store, standalone=True)
        result = await asyncio.wait_for(
            service.drain_lifecycle(
                principal="service:sweeper",
                now=datetime.now(UTC),
                limit=1,
                time_budget=timedelta(seconds=3),
            ),
            timeout=8,
        )
        assert not result.drained  # The unsupported retained root remains observable.
        assert await service._repository.get_verified_dependencies(ownerless) is None
        proof = await service._repository.get_verified_dependencies(owned)
        assert proof is not None and [item.artifact_id for item in proof] == [child]
        assert await service._repository.has_saved_definition_reference(child)
        assert not await service._repository.has_saved_definition_reference(
            orphan_child
        )
        assert await service._repository.list_links(ownerless) == []
        warnings = [
            record
            for record in caplog.records
            if "no recorded producer execution" in record.message
        ]
        assert len(warnings) == 1
