"""Product admission seals the exact immutable Skill inputs it publishes."""

import hashlib

import pytest

from api_service.services.omnigent_execution_plan_service import (
    _resolve_and_persist_skills,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactTransactionOwnership,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal.test_artifacts import temporal_db


@pytest.mark.asyncio
async def test_resolved_skill_producer_seals_exact_content_before_commit(tmp_path):
    store = LocalTemporalArtifactStore(tmp_path / "blobs")
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            repository = TemporalArtifactRepository(session)
            artifacts = TemporalArtifactService(
                repository,
                store=store,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
            resolved, manifest_id, _, content_refs = await _resolve_and_persist_skills(
                session_factory=sessions,
                db_session=session,
                access=access_for_test("operator"),
                artifact_service=artifacts,
                principal="operator",
                workflow_id="mm:skill-closure",
                task_input_snapshot_digest="sha256:" + "1" * 64,
                initial_parameters={"task": {"skill": {"id": "moonspec-verify"}}},
            )
            dependencies = await repository.get_verified_dependencies(manifest_id)
            assert dependencies is not None
            assert {item.artifact_id for item in dependencies} == set(content_refs)
            assert len(resolved.skills) == len(content_refs)
            for dependency in dependencies:
                artifact = await repository.get_artifact(dependency.artifact_id)
                assert dependency.required
                assert (
                    dependency.sha256.removeprefix("sha256:")
                    == hashlib.sha256(
                        store.read_bytes(artifact.storage_key)
                    ).hexdigest()
                )
            await session.commit()
        async with sessions() as observer:
            recorded = await TemporalArtifactRepository(
                observer
            ).get_verified_dependencies(manifest_id)
            assert recorded == dependencies


from tests.unit.omnigent import test_admitted_input_authority as authority_fixtures

artifacts = authority_fixtures.artifacts
auth_mode = authority_fixtures.auth_mode


@pytest.mark.asyncio
async def test_saved_plan_admission_reuses_sealed_inputs_without_rediscovering_skills(
    artifacts, monkeypatch
):
    from moonmind.omnigent import bridge_artifacts
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from tests.unit.omnigent.test_scheduled_planning_input_authority import _schedule

    saved = await _schedule(artifacts)
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(
            repository, transaction_ownership=ArtifactTransactionOwnership.CALLER
        )
        plan = await SessionExecutionPlanStore(session).load(saved.binding.plan_ref)
        closure = await bridge_artifacts.ensure_verified_execution_plan_dependencies(
            artifacts=service,
            repository=repository,
            plan=plan,
            binding=saved.binding,
            access=access_for_test("operator"),
        )
        expected = {entry.artifact_id for entry in closure.dependencies}
        assert {
            saved.input_id,
            saved.plan_id,
            saved.snapshot_id,
            saved.skills.body_id,
        } <= expected
        await session.commit()

    async def no_discovery(*args, **kwargs):
        raise AssertionError("completed plan must not rediscover Skill dependencies")

    monkeypatch.setattr(bridge_artifacts, "_skill_content_digests", no_discovery)
    async with artifacts() as session:
        plan = await SessionExecutionPlanStore(session).load(saved.binding.plan_ref)
        linked = await bridge_artifacts.link_verified_execution_plan_inputs_in_session(
            session=session,
            plan=plan,
            binding=saved.binding,
            workflow_id="mm:sealed-child",
            run_id="run-child",
            access=access_for_test("operator"),
        )
        assert set(linked) == expected | {
            saved.binding.plan_artifact_ref.removeprefix("artifact:")
        }
        await session.commit()


@pytest.mark.asyncio
async def test_full_plan_compiler_seals_frozen_snapshot_and_nested_skill_bytes(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from api_service.services import omnigent_execution_plan_service as plans
    from tests.unit.services import test_omnigent_execution_plan_service as fixtures

    fixtures._configure_ready_host_image_pair(monkeypatch)
    from unittest.mock import AsyncMock

    # Deployment discovery is provider-free; compilation, artifact writes,
    # immutable plan storage and dependency verification remain real.
    monkeypatch.setattr(
        plans, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setenv(
        "OMNIGENT_OPENCODE_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "7" * 64,
    )

    async def policy(**kwargs):
        return fixtures._policy_snapshot(
            harness="opencode-native", policy="omnigent-on-demand@1"
        )

    monkeypatch.setattr(plans, "_resolve_runtime_policy_snapshot", policy)
    monkeypatch.setattr(
        plans,
        "resolve_execution_evidence",
        lambda payload, **kwargs: (
            fixtures._protected_support_evidence(payload),
            "supported",
        ),
    )
    store = LocalTemporalArtifactStore(tmp_path / "full-blobs")
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            repository = TemporalArtifactRepository(session)
            service = TemporalArtifactService(
                repository,
                store=store,
                transaction_ownership=ArtifactTransactionOwnership.CALLER,
            )
            snapshot_id, snapshot_digest = await plans.persist_json_artifact(
                artifact_service=service,
                principal="operator",
                artifact_class="task.input",
                payload={"source": {"kind": "api"}, "draft": {}, "attachmentRefs": []},
            )
            compiled = await plans.compile_and_persist_execution_plan(
                session_factory=sessions,
                db_session=session,
                artifact_service=service,
                principal="operator",
                workflow_id="mm:full-closure",
                agent_profile_snapshot=fixtures._snapshot(
                    harness="opencode-native",
                    policy="omnigent-on-demand@1",
                    provider_id="provider-opencode",
                ),
                provider_profile=SimpleNamespace(
                    profile_id="provider-opencode",
                    runtime_id="opencode",
                    provider_id="opencode-go",
                ),
                initial_parameters={
                    "targetRuntime": "omnigent",
                    "publishMode": "none",
                    "model": "example/model",
                    "task": {"skill": {"id": "moonspec-verify"}},
                },
                authored_request_ref=snapshot_id,
                authored_request_digest=snapshot_digest,
                task_input_snapshot_ref=snapshot_id,
                task_input_snapshot_digest=snapshot_digest,
                access=access_for_test("operator"),
            )
            root = compiled.binding.plan_artifact_ref.removeprefix("artifact:")
            dependencies = await repository.get_verified_dependencies(root)
            manifest = await repository.get_verified_dependencies(
                compiled.resolved_skillset_ref
            )
            assert dependencies and manifest
            ids = {entry.artifact_id for entry in dependencies}
            assert snapshot_id in ids
            assert compiled.resolved_skillset_ref in ids
            assert {entry.artifact_id for entry in manifest} <= ids
            await session.commit()
        async with sessions() as observer:
            assert (
                await TemporalArtifactRepository(observer).get_verified_dependencies(
                    root
                )
                == dependencies
            )


@pytest.mark.asyncio
async def test_legacy_plan_backfill_cannot_replace_frozen_skill_digest(artifacts):
    from api_service.db.models import TemporalArtifact
    from moonmind.omnigent.bridge_artifacts import (
        ensure_verified_execution_plan_dependencies,
    )
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from moonmind.workflows.temporal.artifacts import TemporalArtifactStateError
    from tests.unit.omnigent.test_scheduled_planning_input_authority import _schedule

    saved = await _schedule(artifacts)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        content = await session.get(TemporalArtifact, saved.skills.body_id)
        changed = b"Different Skill bytes must not become historical authority"
        service._store.write_bytes(
            content.storage_key, changed, content_type=content.content_type
        )
        content.sha256 = hashlib.sha256(changed).hexdigest()
        content.size_bytes = len(changed)
        await session.commit()
    async with artifacts() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(
            repository, transaction_ownership=ArtifactTransactionOwnership.CALLER
        )
        plan = await SessionExecutionPlanStore(session).load(saved.binding.plan_ref)
        with pytest.raises(TemporalArtifactStateError, match="digest"):
            await ensure_verified_execution_plan_dependencies(
                artifacts=service,
                repository=repository,
                plan=plan,
                binding=saved.binding,
                access=access_for_test("operator"),
            )
        root = saved.binding.plan_artifact_ref.removeprefix("artifact:")
        assert await repository.get_verified_dependencies(root) is None
