"""One producer-owned immutable closure protects exact retained input bytes."""

from datetime import UTC, datetime, timedelta

import pytest

from api_service.db import models
from moonmind.workflows.temporal import artifacts as module
from tests.unit.workflows.temporal.test_artifacts import temporal_db

pytestmark = pytest.mark.asyncio


def service(session, store):
    return module.TemporalArtifactService(
        module.TemporalArtifactRepository(session),
        store=store,
        transaction_ownership=module.ArtifactTransactionOwnership.CALLER,
    )


async def seed(session, store, payload):
    owner = service(session, store)
    item, _ = await owner.create(principal="service:owner", content_type="text/plain")
    await owner.write_complete(
        artifact_id=item.artifact_id, principal="service:owner", payload=payload
    )
    return item


async def test_sealed_closure_survives_commit_but_not_caller_rollback(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            root, child = await seed(session, store, b"root"), await seed(
                session, store, b"child"
            )
            await session.commit()
            root_id, child_id, digest = (
                root.artifact_id,
                child.artifact_id,
                child.sha256,
            )
            await service(session, store).record_verified_dependencies(
                root_artifact_id=root_id,
                dependencies=[module.ArtifactDependency(child_id, digest)],
                access=module.ArtifactAccessContext.for_service("service:owner"),
            )
            await session.rollback()
        async with maker() as session:
            assert (
                await module.TemporalArtifactRepository(
                    session
                ).get_verified_dependencies(root_id)
                is None
            )
            await service(session, store).record_verified_dependencies(
                root_artifact_id=root_id,
                dependencies=[module.ArtifactDependency(child_id, digest)],
                access=module.ArtifactAccessContext.for_service("service:owner"),
            )
            await session.commit()
        async with maker() as session:
            values = await module.TemporalArtifactRepository(
                session
            ).get_verified_dependencies(root_id)
            assert [item.artifact_id for item in values] == [child_id]


async def test_live_root_protects_required_not_optional_and_releases_after_expiry(
    tmp_path,
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        await service(session, store).record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, child.sha256)],
            access=module.ArtifactAccessContext.for_service("service:owner"),
        )
        child.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        with pytest.raises(module.TemporalArtifactStateError, match="retained"):
            await service(session, store).soft_delete(
                artifact_id=child.artifact_id, principal="service:owner"
            )
        root.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        await service(session, store).soft_delete(
            artifact_id=child.artifact_id, principal="service:owner"
        )
        assert child.status is models.TemporalArtifactStatus.DELETED


async def test_empty_seal_and_optional_missing_are_distinct_from_unverified(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root = await seed(session, store, b"root")
        api = service(session, store)
        assert (
            await api.verified_dependencies(
                root_artifact_id=root.artifact_id, access=access
            )
            is None
        )
        result = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[
                module.ArtifactDependency("art_missing", None, False, "evidence")
            ],
            access=access,
        )
        assert result.dependencies == ()
        assert result.unavailable_optional == ("art_missing",)
        assert (
            await api.verified_dependencies(
                root_artifact_id=root.artifact_id, access=access
            )
            == result
        )
        with pytest.raises(module.TemporalArtifactStateError, match="differs"):
            await api.record_verified_dependencies(
                root_artifact_id=root.artifact_id, dependencies=[], access=access
            )


@pytest.mark.parametrize("fault", ["digest", "foreign", "corrupt_seal"])
async def test_sealed_dependencies_never_replace_authority_or_integrity(
    tmp_path, fault
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        api = service(session, store)
        if fault == "foreign":
            child.created_by_principal = "service:foreign"
        if fault != "corrupt_seal":
            with pytest.raises(module.TemporalArtifactError):
                await api.record_verified_dependencies(
                    root_artifact_id=root.artifact_id,
                    dependencies=[
                        module.ArtifactDependency(
                            child.artifact_id,
                            "0" * 64 if fault == "digest" else child.sha256,
                        )
                    ],
                    access=access,
                )
            assert root.dependency_closure_version is None
        else:
            await api.record_verified_dependencies(
                root_artifact_id=root.artifact_id,
                dependencies=[
                    module.ArtifactDependency(child.artifact_id, child.sha256)
                ],
                access=access,
            )
            root.dependency_closure_sha256 = "0" * 64
            with pytest.raises(module.TemporalArtifactStateError, match="integrity"):
                await api.verified_dependencies(
                    root_artifact_id=root.artifact_id, access=access
                )


async def test_flattening_preserves_optional_ancestry_and_frozen_closure(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child, leaf = [
            await seed(session, store, name) for name in [b"root", b"child", b"leaf"]
        ]
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=child.artifact_id,
            dependencies=[module.ArtifactDependency(leaf.artifact_id, leaf.sha256)],
            access=access,
        )
        result = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[
                module.ArtifactDependency(child.artifact_id, child.sha256, False)
            ],
            access=access,
        )
        assert {item.artifact_id for item in result.dependencies} == {
            child.artifact_id,
            leaf.artifact_id,
        }
        assert not any(item.required for item in result.dependencies)


async def test_expired_unprotected_required_input_cannot_be_newly_admitted(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        child.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        with pytest.raises(module.TemporalArtifactStateError, match="expired"):
            await service(session, store).record_verified_dependencies(
                root_artifact_id=root.artifact_id,
                dependencies=[
                    module.ArtifactDependency(child.artifact_id, child.sha256)
                ],
                access=module.ArtifactAccessContext.for_service("service:owner"),
            )


async def test_present_optional_input_records_actual_verified_digest(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        result = await service(session, store).record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, None, False)],
            access=module.ArtifactAccessContext.for_service("service:owner"),
        )
        assert result.dependencies[0].sha256 == child.sha256


async def test_later_child_seal_does_not_rewrite_parent_and_self_reference_rejected(
    tmp_path,
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child, leaf = [
            await seed(session, store, name) for name in [b"root", b"child", b"leaf"]
        ]
        api = service(session, store)
        original = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, child.sha256)],
            access=access,
        )
        await api.record_verified_dependencies(
            root_artifact_id=child.artifact_id,
            dependencies=[module.ArtifactDependency(leaf.artifact_id, leaf.sha256)],
            access=access,
        )
        assert (
            await api.verified_dependencies(
                root_artifact_id=root.artifact_id, access=access
            )
            == original
        )
        with pytest.raises(module.TemporalArtifactValidationError, match="root"):
            await api.record_verified_dependencies(
                root_artifact_id=leaf.artifact_id,
                dependencies=[module.ArtifactDependency(leaf.artifact_id, leaf.sha256)],
                access=access,
            )


async def test_optional_child_never_retained_and_unknown_foreign_is_not_authority(
    tmp_path,
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[
                module.ArtifactDependency(child.artifact_id, child.sha256, False)
            ],
            access=access,
        )
        await api.soft_delete(artifact_id=child.artifact_id, principal="service:owner")
        result = await api.verified_dependencies(
            root_artifact_id=root.artifact_id, access=access
        )
        assert result.dependencies == ()
        assert result.unavailable_optional == (child.artifact_id,)
        root2 = await seed(session, store, b"root2")
        child.created_by_principal = "service:foreign"
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await api.record_verified_dependencies(
                root_artifact_id=root2.artifact_id,
                dependencies=[
                    module.ArtifactDependency(child.artifact_id, None, False)
                ],
                access=access,
            )


async def test_restricted_dependency_integrity_does_not_grant_raw_access(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    from moonmind.security.operator_admission import OperatorAdmission

    access = module.ArtifactAccessContext.for_operator(
        OperatorAdmission(via="loopback")
    )
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        child.redaction_level = models.TemporalArtifactRedactionLevel.RESTRICTED
        api = service(session, store)
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await api.read(artifact_id=child.artifact_id, access=access)
        result = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, child.sha256)],
            access=access,
        )
        assert result.dependencies[0].artifact_id == child.artifact_id
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await api.read(artifact_id=child.artifact_id, access=access)


async def test_one_logical_dependency_cannot_have_conflicting_role_digests(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        with pytest.raises(module.TemporalArtifactStateError, match="Conflicting"):
            await service(session, store).record_verified_dependencies(
                root_artifact_id=root.artifact_id,
                dependencies=[
                    module.ArtifactDependency(
                        child.artifact_id, child.sha256, False, "first"
                    ),
                    module.ArtifactDependency(
                        child.artifact_id, "0" * 64, False, "second"
                    ),
                ],
                access=module.ArtifactAccessContext.for_service("service:owner"),
            )


async def test_two_roots_release_only_their_own_required_retention(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        first, second, child = [
            await seed(session, store, name) for name in [b"first", b"second", b"child"]
        ]
        api = service(session, store)
        for root in (first, second):
            await api.record_verified_dependencies(
                root_artifact_id=root.artifact_id,
                dependencies=[
                    module.ArtifactDependency(child.artifact_id, child.sha256)
                ],
                access=access,
            )
        first.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        with pytest.raises(module.TemporalArtifactStateError, match="retained"):
            await api.soft_delete(
                artifact_id=child.artifact_id, principal="service:owner"
            )
        second.expires_at = first.expires_at
        await session.flush()
        await api.soft_delete(artifact_id=child.artifact_id, principal="service:owner")
        assert child.status is models.TemporalArtifactStatus.DELETED


async def test_flat_cycles_without_intrinsic_owner_do_not_self_retain(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        first, second, third = [
            await seed(session, store, name) for name in [b"first", b"second", b"third"]
        ]
        api = service(session, store)
        # Each root freezes the child's closure as it existed at admission.
        for root, child in ((first, second), (second, third), (third, first)):
            await api.record_verified_dependencies(
                root_artifact_id=root.artifact_id,
                dependencies=[
                    module.ArtifactDependency(child.artifact_id, child.sha256)
                ],
                access=access,
            )
        for root in (first, second, third):
            root.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        for root in (first, second, third):
            await api.soft_delete(
                artifact_id=root.artifact_id, principal="service:owner"
            )
            assert root.status is models.TemporalArtifactStatus.DELETED


async def test_root_without_expiry_retains_children_until_explicitly_deleted(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, child.sha256)],
            access=access,
        )
        root.expires_at = None
        child.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        with pytest.raises(module.TemporalArtifactStateError, match="retained"):
            await api.soft_delete(
                artifact_id=child.artifact_id, principal="service:owner"
            )
        await api.soft_delete(artifact_id=root.artifact_id, principal="service:owner")
        await api.soft_delete(artifact_id=child.artifact_id, principal="service:owner")
        assert child.status is models.TemporalArtifactStatus.DELETED


async def test_same_direct_request_retry_reuses_frozen_closure_after_child_seals(
    tmp_path,
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child, leaf = [
            await seed(session, store, name) for name in [b"root", b"child", b"leaf"]
        ]
        api = service(session, store)
        original = [module.ArtifactDependency(child.artifact_id, child.sha256)]
        first = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id, dependencies=original, access=access
        )
        await api.record_verified_dependencies(
            root_artifact_id=child.artifact_id,
            dependencies=[module.ArtifactDependency(leaf.artifact_id, leaf.sha256)],
            access=access,
        )
        retry = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id, dependencies=original, access=access
        )
        assert retry == first
        with pytest.raises(module.TemporalArtifactStateError, match="differs"):
            await api.record_verified_dependencies(
                root_artifact_id=root.artifact_id, dependencies=[], access=access
            )


@pytest.mark.parametrize("known_digest", [False, True])
async def test_original_optional_identity_controls_later_availability(
    tmp_path, known_digest
):
    import hashlib

    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root = await seed(session, store, b"root")
        api = service(session, store)
        request = [
            module.ArtifactDependency(
                "art_later",
                hashlib.sha256(b"later").hexdigest() if known_digest else None,
                False,
            )
        ]
        original = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id, dependencies=request, access=access
        )
        assert original.unavailable_optional == ("art_later",)
        later = await seed(session, store, b"later")
        later.artifact_id = "art_later"
        await session.flush()
        observed = await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id, dependencies=request, access=access
        )
        assert observed.sha256 == original.sha256
        if known_digest:
            assert [item.artifact_id for item in observed.dependencies] == ["art_later"]
            assert observed.unavailable_optional == ()
        else:
            assert observed.dependencies == ()
            assert observed.unavailable_optional == ("art_later",)


async def test_dependency_proof_requires_root_authority_and_never_grants_children(
    tmp_path,
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    owner = module.ArtifactAccessContext.for_service("service:owner")
    foreign = module.ArtifactAccessContext.for_service("service:foreign")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, child.sha256)],
            access=owner,
        )
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await api.dependency_proof(
                root_artifact_id=root.artifact_id, access=foreign
            )
        proof = await api.dependency_proof(
            root_artifact_id=root.artifact_id, access=owner
        )
        assert [item.artifact_id for item in proof] == [child.artifact_id]
        assert await api._repository.list_links(child.artifact_id) == []
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await api.read(artifact_id=child.artifact_id, access=foreign)


async def test_consumer_can_use_required_subset_without_optional_evidence_grant(
    tmp_path,
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    owner = module.ArtifactAccessContext.for_service("service:owner")
    consumer = module.ArtifactAccessContext.for_execution("consumer")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, required, optional = [
            await seed(session, store, name)
            for name in [b"root", b"required", b"optional"]
        ]
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[
                module.ArtifactDependency(required.artifact_id, required.sha256),
                module.ArtifactDependency(optional.artifact_id, optional.sha256, False),
            ],
            access=owner,
        )
        for item in (root, required):
            await api.link_artifact(
                artifact_id=item.artifact_id,
                principal="service:owner",
                execution_ref=module.ExecutionRef(
                    namespace="default",
                    workflow_id="consumer",
                    run_id="run",
                    link_type="output",
                ),
            )
        result = await api.verified_dependencies(
            root_artifact_id=root.artifact_id, access=consumer
        )
        assert [item.artifact_id for item in result.dependencies] == [
            required.artifact_id
        ]
        assert result.unavailable_optional == (optional.artifact_id,)
        assert await api._repository.list_links(optional.artifact_id) == []
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await api.read(artifact_id=optional.artifact_id, access=consumer)


@pytest.mark.parametrize("required", [False, True])
async def test_quarantined_optional_is_unavailable_without_reading_bytes(
    tmp_path, monkeypatch, required
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[
                module.ArtifactDependency(child.artifact_id, child.sha256, required)
            ],
            access=access,
        )
        child.metadata_json = {"quarantine": True}
        actual_matches = store.object_matches

        def deny_child_bytes(key, **kwargs):
            assert key != child.storage_key
            return actual_matches(key, **kwargs)

        monkeypatch.setattr(store, "object_matches", deny_child_bytes)
        if required:
            with pytest.raises(module.TemporalArtifactStateError, match="QUARANTINED"):
                await api.verified_dependencies(
                    root_artifact_id=root.artifact_id, access=access
                )
        else:
            result = await api.verified_dependencies(
                root_artifact_id=root.artifact_id, access=access
            )
            assert result.dependencies == ()
            assert result.unavailable_optional == (child.artifact_id,)


async def test_unsealed_root_observation_still_requires_root_authority(tmp_path):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root = await seed(session, store, b"root")
        with pytest.raises(module.TemporalArtifactAuthorizationError):
            await service(session, store).verified_dependencies(
                root_artifact_id=root.artifact_id,
                access=module.ArtifactAccessContext.for_service("service:foreign"),
            )


async def test_legacy_root_without_original_owner_is_observable_not_fabricated(
    tmp_path, caplog
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root = await seed(
            session, store, b'{"untrusted": "not decoded without authority"}'
        )
        root.content_type = (
            "application/vnd.moonmind.saved-work-manifest+json;version=1"
        )
        await session.commit()
        standalone = module.TemporalArtifactService(
            module.TemporalArtifactRepository(session), store=store
        )
        result = await standalone.sweep_lifecycle(
            principal="service:lifecycle", limit=1
        )
        assert not result.drained
        assert root.dependency_closure_version is None
        assert any(
            "producer execution" in record.getMessage() for record in caplog.records
        )


@pytest.mark.parametrize(
    "kind", ["admitted", "raw", "ordinary", "wrong_namespace", "historical_run"]
)
@pytest.mark.parametrize(
    "state",
    [models.MoonMindWorkflowState.INITIALIZING, models.MoonMindWorkflowState.EXECUTING],
)
async def test_active_workflow_admission_lifetime_excludes_untrusted_or_historical_links(
    tmp_path, kind, state
):
    store = module.LocalTemporalArtifactStore(tmp_path / "bytes")
    access = module.ArtifactAccessContext.for_service("service:owner")
    async with temporal_db(tmp_path) as maker, maker() as session:
        root, child = await seed(session, store, b"root"), await seed(
            session, store, b"child"
        )
        api = service(session, store)
        await api.record_verified_dependencies(
            root_artifact_id=root.artifact_id,
            dependencies=[module.ArtifactDependency(child.artifact_id, child.sha256)],
            access=access,
        )
        execution = models.TemporalExecutionCanonicalRecord(
            namespace="default",
            workflow_id="active",
            run_id="live-run",
            workflow_type=models.TemporalWorkflowType.USER_WORKFLOW,
            owner_type=models.TemporalExecutionOwnerType.USER,
            state=state,
            entry="run",
            search_attributes={},
            memo={},
            artifact_refs=[],
            parameters={},
        )
        session.add(execution)
        if kind in {"admitted", "raw", "wrong_namespace"}:
            if kind == "raw":
                root.redaction_level = models.TemporalArtifactRedactionLevel.RESTRICTED
            await api.delegate_read_access(
                artifact_ids=[root.artifact_id],
                workflow_id="active",
                namespace="foreign" if kind == "wrong_namespace" else "default",
                access=access,
            )
        else:
            await api.link_artifact(
                artifact_id=root.artifact_id,
                principal="service:owner",
                execution_ref=module.ExecutionRef(
                    namespace="default",
                    workflow_id="active",
                    run_id="old-run" if kind == "historical_run" else "",
                    link_type="output",
                ),
            )
        root.expires_at = child.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.flush()
        protected = kind in {"admitted", "raw"}
        assert (
            await api._repository.has_saved_definition_reference(root.artifact_id)
            is protected
        )
        assert (
            await api._repository.has_saved_definition_reference(child.artifact_id)
            is protected
        )
        if protected:
            with pytest.raises(module.TemporalArtifactStateError, match="retained"):
                await api.soft_delete(
                    artifact_id=child.artifact_id, principal="service:owner"
                )
            execution.state = models.MoonMindWorkflowState.COMPLETED
            execution.close_status = models.TemporalExecutionCloseStatus.COMPLETED
            await session.flush()
        await api.soft_delete(artifact_id=child.artifact_id, principal="service:owner")
        assert child.status is models.TemporalArtifactStatus.DELETED
