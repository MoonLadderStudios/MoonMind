"""Real Temporal journeys for Publish Saved Work (MoonLadderStudios/MoonMind#4018).

The saved-work branch of ``MoonMind.PublicationRecoveryV1`` runs on a
time-skipping Temporal server. Workers poll the production task queues with
the production bindings of the ``publication_recovery`` Activities only, so no
agent or model Activity could complete. The destination is a local bare Git
remote and the pull-request provider a recording fixture. The workflow worker
keeps no cache, so every workflow task replays the recorded history.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import CancelledError as TemporalCancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    ARTIFACTS_FLEET,
    INTEGRATIONS_FLEET,
    INTEGRATIONS_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalIntegrationActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.publication_recovery import (
    SavedWorkPublicationContract,
    saved_work_publication_workflow_id,
)
from moonmind.workflows.temporal.workflows.publication_recovery import (
    MoonMindPublicationRecoveryWorkflow,
)
from tests.artifact_access import access_for_test
from tests.support.saved_work_capture import git
from tests.unit.publish.test_saved_work_publication_journey import (  # noqa: F401
    Journey,
    _enforced_access,
    _write_destination,
    journey,
)

pytestmark = [pytest.mark.asyncio]

Hook = Callable[[int], Awaitable[None]]
_SCHEDULED = EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED


def _with_hooks(
    name: str, handler: Any, *, before: Hook | None, after: Hook | None, transform=None
) -> Any:
    """Wrap one binding; hooks see its attempt number across every retry."""

    attempts = 0

    @activity.defn(name=name)
    async def wrapped(payload: Any = None) -> Any:
        nonlocal attempts
        attempts += 1
        attempt = attempts
        if before is not None:
            await before(attempt)
        result = await handler(transform(payload) if transform is not None else payload)
        if after is not None:
            await after(attempt)
        return result

    return wrapped


def _queues(
    state: Journey,
    after: dict[str, Hook] | None = None,
    *,
    before: dict[str, Hook] | None = None,
    transforms: dict[str, Callable] | None = None,
) -> dict[str, list]:
    """Production ``publication_recovery`` bindings grouped by task queue."""

    catalog = build_default_activity_catalog()
    bindings = build_activity_bindings(
        TemporalActivityCatalog(
            activities=tuple(
                item
                for item in catalog.activities
                if item.activity_type.startswith("publication_recovery.")
            ),
            fleets=catalog.fleets,
        ),
        artifact_activities=TemporalArtifactActivities(state.service),
        integration_activities=TemporalIntegrationActivities(),
        agent_runtime_activities=state.runtime,
        fleets=[AGENT_RUNTIME_FLEET, INTEGRATIONS_FLEET, ARTIFACTS_FLEET],
    )
    queues: dict[str, list] = {}
    for binding in bindings:
        handler = binding.handler
        name = binding.activity_type
        if (
            name in (after or {})
            or name in (before or {})
            or name in (transforms or {})
        ):
            handler = _with_hooks(
                name,
                handler,
                before=(before or {}).get(name),
                after=(after or {}).get(name),
                transform=(transforms or {}).get(name),
            )
        queues.setdefault(binding.task_queue, []).append(handler)
    return queues


@asynccontextmanager
async def _workers(
    client: Client, queues: dict[str, list], *, without: set[str] = frozenset()
):
    """One worker generation: the workflow worker plus the reachable Activity queues."""

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(
            Worker(
                client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[MoonMindPublicationRecoveryWorkflow],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            )
        )
        for queue, handlers in queues.items():
            if queue not in without:
                await stack.enter_async_context(
                    Worker(client, task_queue=queue, activities=handlers)
                )
        yield


async def _scheduled(handle: WorkflowHandle) -> list[str]:
    history = await handle.fetch_history()
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.event_type == _SCHEDULED
    ]


async def _wait_until_scheduled(handle: WorkflowHandle, name: str) -> None:
    for _ in range(600):
        if name in await _scheduled(handle):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{name} was never scheduled")


async def _contract(state: Journey) -> tuple[dict[str, Any], str]:
    contract = state.contract(
        objective="pr", baseBranch="main", strategy="additive_import"
    )
    workflow_id = saved_work_publication_workflow_id(
        SavedWorkPublicationContract.model_validate(contract)
    )
    await state.admit(contract, workflow_id)
    return contract, workflow_id


async def _persisted(state: Journey, handle: WorkflowHandle) -> dict[str, Any]:
    principal = f"workflow:{handle.id}"
    run_id = (await handle.describe()).run_id
    (artifact,) = [
        artifact
        for artifact in await state.service.list_for_execution(
            namespace=state.service._default_namespace,
            workflow_id=handle.id,
            run_id=run_id,
            link_type="result",
            access=access_for_test(principal),
        )
        if (artifact.metadata_json or {}).get("name")
        == "saved-work-publication-result.json"
    ]
    _meta, payload = await state.service.read(
        artifact_id=artifact.artifact_id, access=access_for_test(principal)
    )
    return json.loads(payload)


async def test_worker_restart_between_push_and_pull_request_recovers_only_the_pr(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        before = state.saved_bytes()
        contract, workflow_id = await _contract(state)

        async def lose_first_acknowledgment(attempt: int) -> None:
            if attempt == 1:
                raise RuntimeError("worker lost after the push landed")

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            # The first worker generation cannot reach the pull-request queue.
            async with _workers(
                env.client,
                _queues(
                    state,
                    {"publication_recovery.saved_work_push": lose_first_acknowledgment},
                ),
                without={INTEGRATIONS_TASK_QUEUE},
            ):
                handle = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await _wait_until_scheduled(
                    handle, "publication_recovery.saved_work_pull_request"
                )
            head = git(state.remote, "rev-parse", "refs/heads/saved/work")
            assert len(state.pushes()) == 1 and state.provider.creates == []

            # A fresh generation replays the history and completes only the PR.
            async with _workers(env.client, _queues(state)):
                result = await handle.result()
            scheduled = await _scheduled(handle)

        assert result["outcome"] == "published"
        assert result["push"]["status"] == "reconciled"
        assert result["candidate"]["headSha"] == head
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        # The workflow schedules each push attempt; the retry after the lost
        # acknowledgment reconciled instead of pushing again.
        assert scheduled == [
            "publication_recovery.saved_work_prepare",
            "publication_recovery.saved_work_push",
            "publication_recovery.saved_work_push",
            "publication_recovery.saved_work_pull_request",
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        assert state.saved_objects_unchanged(before)
        assert await state.use_claims() == []


async def test_cancellation_during_push_records_the_landed_push_and_stops(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        contract, workflow_id = await _contract(state)
        landed, release = asyncio.Event(), asyncio.Event()

        async def hold_acknowledgment(attempt: int) -> None:
            landed.set()
            await release.wait()

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(
                env.client,
                _queues(
                    state, {"publication_recovery.saved_work_push": hold_acknowledgment}
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await asyncio.wait_for(landed.wait(), timeout=60)
                await handle.cancel()
                # Let the workflow observe the cancellation before the push reports.
                await asyncio.sleep(1)
                release.set()
                with pytest.raises(WorkflowFailureError) as failure:
                    await handle.result()
            persisted = await _persisted(state, handle)
            scheduled = await _scheduled(handle)

        assert isinstance(failure.value.cause, TemporalCancelledError)
        assert persisted["outcome"] == "cancelled"
        assert persisted["push"]["status"] == "pushed"
        assert persisted["push"]["remoteHeadSha"] == git(
            state.remote, "rev-parse", "refs/heads/saved/work"
        )
        assert "publication_recovery.saved_work_pull_request" not in scheduled
        assert scheduled[-2:] == [
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        assert state.provider.creates == []
        assert await state.use_claims() == []


async def test_a_terminated_run_after_its_push_is_completed_by_a_resubmission(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        before = state.saved_bytes()
        contract, workflow_id = await _contract(state)

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            # The push lands; the operator terminates the run while its PR
            # waits, so the run never writes a terminal record.
            async with _workers(
                env.client, _queues(state), without={INTEGRATIONS_TASK_QUEUE}
            ):
                first = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await _wait_until_scheduled(
                    first, "publication_recovery.saved_work_pull_request"
                )
                await first.terminate("operator stopped the publication")
            head = git(state.remote, "rev-parse", "refs/heads/saved/work")
            _write_destination(
                tmp_path, state.remote, {"later.txt": "base advanced\n"}
            )

            # The same request under the same workflow id completes only the PR.
            async with _workers(env.client, _queues(state)):
                second = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                result = await second.result()

        assert second.result_run_id != first.result_run_id
        assert result["outcome"] == "published"
        assert result["push"]["status"] == "reconciled"
        assert result["candidate"]["headSha"] == head
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        assert state.saved_objects_unchanged(before)
        assert await state.use_claims() == []


async def test_cancellation_after_a_failed_push_attempt_starts_no_new_attempt(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        contract, workflow_id = await _contract(state)
        failing, release = asyncio.Event(), asyncio.Event()

        async def fail_first_attempt_before_pushing(attempt: int) -> None:
            if attempt == 1:
                failing.set()
                await release.wait()
                raise RuntimeError("push attempt failed before pushing")

        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(
                env.client,
                _queues(
                    state,
                    before={
                        "publication_recovery.saved_work_push": (
                            fail_first_attempt_before_pushing
                        )
                    },
                ),
            ):
                handle = await env.client.start_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                )
                await asyncio.wait_for(failing.wait(), timeout=60)
                await handle.cancel()
                # Let the workflow observe the cancellation before the attempt fails.
                await asyncio.sleep(1)
                release.set()
                with pytest.raises(WorkflowFailureError) as failure:
                    await handle.result()
            persisted = await _persisted(state, handle)
            scheduled = await _scheduled(handle)

        assert isinstance(failure.value.cause, TemporalCancelledError)
        assert persisted["outcome"] == "cancelled"
        # The failed attempt stays diagnosable; no retry replaced it.
        assert persisted["push"] == {
            "status": "unconfirmed",
            "reasonCode": "publication_cancelled",
            "lastAttemptReasonCode": "RuntimeError",
        }
        assert scheduled.count("publication_recovery.saved_work_push") == 1
        assert "publication_recovery.saved_work_pull_request" not in scheduled
        assert state.pushes() == []
        assert "refs/heads/saved/work" not in git(state.remote, "for-each-ref")
        assert state.provider.creates == []
        assert await state.use_claims() == []


async def test_recorded_v1_admission_recovers_protected_inputs_without_operator_role(
    tmp_path, monkeypatch
):
    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.security.operator_admission import OperatorAdmission
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
    from tests.unit.publish.test_saved_work_publication_journey import (
        _publication_closure,
    )

    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        closure = await _publication_closure(state)
        repository = state.service._repository
        protected = [
            artifact_id
            for artifact_id in closure
            if (await repository.get_artifact(artifact_id)).content_type
            == "application/vnd.moonmind.worktree-archive"
        ][0]
        artifact = await repository.get_artifact(protected)
        artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
        artifact.operator_recovery_granted = True
        await repository.commit()
        provenance = (
            artifact.artifact_id,
            artifact.created_by_principal,
            artifact.sha256,
        )
        request = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        parsed = await state.admit(
            request,
            "unused-before-start",
            install_grants=False,
            access=ArtifactAccessContext.for_operator(
                OperatorAdmission(via="fixture-old-api"), recovery=True
            ),
        )
        contract = parsed.model_dump(mode="json", by_alias=True)
        contract["schemaVersion"] = "saved-work-publication-v1"
        workflow_id = saved_work_publication_workflow_id(parsed)
        assert not any(
            link.workflow_id == workflow_id
            for link in await repository.list_links(protected)
        )
        memo = {
            "publication_semantic_context": "saved_work",
            "source_workflow_id": parsed.source_workflow_id,
            "source_run_id": parsed.source_run_id,
            "publication_idempotency_key": parsed.publication_idempotency_key,
            "saved_work_digest": parsed.saved_work_digest,
        }
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(env.client, _queues(state)):
                result = await env.client.execute_workflow(
                    MoonMindPublicationRecoveryWorkflow.run,
                    contract,
                    id=workflow_id,
                    task_queue=WORKFLOW_TASK_QUEUE,
                    memo=memo,
                )
        assert result["outcome"] == "published"
        assert (
            artifact.artifact_id,
            artifact.created_by_principal,
            artifact.sha256,
        ) == provenance
        reader = ArtifactAccessContext.for_execution(workflow_id)
        assert not reader.is_operator and not reader.operator_recovery
        assert (await state.service.read(artifact_id=protected, access=reader))[1]
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactAuthorizationError,
        )

        with pytest.raises(TemporalArtifactAuthorizationError):
            await state.service.read(
                artifact_id=protected,
                access=ArtifactAccessContext.for_execution("unrelated"),
            )


@pytest.mark.parametrize(
    "fault",
    [
        "missing_memo",
        "source_mismatch",
        "payload_principal",
        "quarantined",
        "revoked_grants",
    ],
)
async def test_recorded_v1_missing_or_conflicting_admission_stays_denied(
    tmp_path, monkeypatch, fault
):
    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "x\n"},
        emulate_temporal=False,
    ) as state:
        parsed = await state.admit(
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            ),
            "unused",
            install_grants=False,
        )
        contract = parsed.model_dump(mode="json", by_alias=True)
        contract["schemaVersion"] = "saved-work-publication-v1"
        workflow_id = saved_work_publication_workflow_id(parsed)
        memo = {
            "publication_semantic_context": "saved_work",
            "source_workflow_id": parsed.source_workflow_id,
            "source_run_id": parsed.source_run_id,
            "publication_idempotency_key": parsed.publication_idempotency_key,
            "saved_work_digest": parsed.saved_work_digest,
        }
        if fault == "missing_memo":
            memo = {}
        elif fault == "source_mismatch":
            memo["source_workflow_id"] = "unrelated"
        elif fault == "revoked_grants":
            memo["artifact_input_admission"] = "execution-links-v1"
        elif fault == "payload_principal":

            def changed(payload):
                return {
                    **payload,
                    "contract": {
                        **payload["contract"],
                        "admittedPrincipal": "operator",
                    },
                }

        else:
            from tests.unit.publish.test_saved_work_publication_journey import (
                _publication_closure,
            )

            for artifact_id in await _publication_closure(state):
                row = await state.service._repository.get_artifact(artifact_id)
                row.metadata_json = {**(row.metadata_json or {}), "quarantine": True}
            await state.service._repository.commit()
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with _workers(
                env.client,
                _queues(
                    state,
                    transforms=(
                        {"publication_recovery.saved_work_prepare": changed}
                        if fault == "payload_principal"
                        else None
                    ),
                ),
            ):
                with pytest.raises(WorkflowFailureError):
                    await env.client.execute_workflow(
                        MoonMindPublicationRecoveryWorkflow.run,
                        contract,
                        id=workflow_id,
                        task_queue=WORKFLOW_TASK_QUEUE,
                        memo=memo,
                    )
        assert state.pushes() == [] and state.provider.creates == []
        for link in await state.service._repository.list_links(
            parsed.saved_work_ref.removeprefix("artifact://")
        ):
            assert link.workflow_id != workflow_id or link.link_type not in {
                "input.artifact.admitted",
                "input.artifact.raw",
            }


@workflow.defn(name="MoonMind.PublicationRecoveryV1")
class _CheckpointPublicationProbe:
    @workflow.run
    async def run(self, contract):
        from datetime import timedelta

        from temporalio.common import RetryPolicy

        from moonmind.workflows.temporal.activity_catalog import (
            AGENT_RUNTIME_TASK_QUEUE,
        )

        return await workflow.execute_activity(
            "publication_recovery.restore_candidate",
            {
                "contract": contract,
                "destinationWorkflowId": workflow.info().workflow_id,
                "destinationRunId": workflow.info().run_id,
                "idempotencyKey": contract["continuation"]["publicationIdempotencyKey"],
            },
            task_queue=AGENT_RUNTIME_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@pytest.mark.parametrize("fault", ["none", "source_scope", "receipt", "quarantined"])
async def test_checkpoint_publication_uses_only_verified_source_admission(
    tmp_path, monkeypatch, fault
):
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )
    from moonmind.workflows.temporal.artifacts import ExecutionRef
    from moonmind.workflows.temporal.publication_recovery import (
        PublicationRecoveryContract,
        publication_recovery_workflow_id,
    )
    from moonmind.workflows.temporal.runtime import checkpoint_restore
    from moonmind.workflows.temporal.runtime.checkpoint_restore import (
        ManagedCheckpointRestoreService,
    )
    from tests.support.saved_work_capture import capture_saved_work, commit_all
    from tests.unit.publish.test_saved_work_publication_journey import _artifact_service
    from tests.unit.workflows.temporal.test_publication_recovery import (
        _contract_payload,
    )

    def mutate(repo):
        (repo / "tracked.txt").write_text("saved candidate\n")
        commit_all(repo, "candidate")

    async def no_source_read(*_, **__):
        raise AssertionError("cold publication cannot require the deleted source host")

    monkeypatch.setattr(checkpoint_restore, "resolve_admitted_git_read", no_source_read)
    async with _artifact_service(tmp_path) as service:
        saved = await capture_saved_work(
            tmp_path,
            {"tracked.txt": "baseline\n"},
            mutate,
            artifact_service=service,
            workflow_id="mm:source",
            run_id="run-source",
        )
        contract = _contract_payload()
        identity = {
            "headSha": saved.workspace_evidence["baseCommit"],
            "treeDigest": "sha256:" + "b" * 64,
            "diffDigest": "sha256:" + "c" * 64,
        }
        contract["continuation"].update(
            expectedHeadSha=identity["headSha"],
            expectedTreeDigest=identity["treeDigest"],
            expectedDiffDigest=identity["diffDigest"],
        )
        root, _ = await service.create(
            principal="system:agent_runtime",
            content_type="application/vnd.moonmind.step-execution-checkpoint+json;version=1",
            link=ExecutionRef(
                namespace=service._default_namespace,
                workflow_id="unrelated" if fault == "source_scope" else "mm:source",
                run_id="run-source",
                link_type="evidence.recovery",
            ),
        )
        await service.write_complete(
            artifact_id=root.artifact_id,
            principal="system:agent_runtime",
            payload=json.dumps(
                {
                    "contentType": "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
                    "source": {
                        "workflowId": "mm:source",
                        "runId": "run-source",
                        "logicalStepId": "implement",
                        "executionOrdinal": 1,
                    },
                    "boundary": "before_publication",
                    "workspace": saved.workspace_evidence,
                    "publicationCandidateIdentity": identity,
                }
            ).encode(),
        )
        contract["continuation"]["beforePublicationCheckpointRef"] = (
            "artifact://" + root.artifact_id
        )
        parsed = PublicationRecoveryContract.model_validate(contract)
        contract = parsed.model_dump(mode="json", by_alias=True)
        workflow_id = publication_recovery_workflow_id(parsed)
        if fault == "quarantined":
            root.metadata_json = {"quarantine": True}
            await service._repository.commit()
        saved.remove_source()
        runtime = TemporalAgentRuntimeActivities(
            artifact_service=service,
            client_adapter=object(),
            workspace_root=tmp_path / "worker",
        )
        runtime._checkpoint_restore = ManagedCheckpointRestoreService(
            authority_root=tmp_path / "cold", artifact_service=service
        )
        catalog = build_default_activity_catalog()
        bindings = build_activity_bindings(
            TemporalActivityCatalog(
                activities=tuple(
                    a
                    for a in catalog.activities
                    if a.activity_type == "publication_recovery.restore_candidate"
                ),
                fleets=catalog.fleets,
            ),
            agent_runtime_activities=runtime,
            fleets=[AGENT_RUNTIME_FLEET],
        )
        (binding,) = bindings
        memo = {
            "publication_semantic_context": parsed.target.semantic_context,
            "source_workflow_id": parsed.source_workflow_id,
            "source_run_id": "wrong" if fault == "receipt" else parsed.source_run_id,
            "publication_idempotency_key": parsed.continuation.publication_idempotency_key,
        }
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env:
            async with Worker(
                env.client,
                task_queue=WORKFLOW_TASK_QUEUE,
                workflows=[_CheckpointPublicationProbe],
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_cached_workflows=0,
            ), Worker(
                env.client, task_queue=binding.task_queue, activities=[binding.handler]
            ):
                if fault == "none":
                    result = await env.client.execute_workflow(
                        _CheckpointPublicationProbe.run,
                        contract,
                        id=workflow_id,
                        task_queue=WORKFLOW_TASK_QUEUE,
                        memo=memo,
                    )
                else:
                    with pytest.raises(WorkflowFailureError):
                        await env.client.execute_workflow(
                            _CheckpointPublicationProbe.run,
                            contract,
                            id=workflow_id,
                            task_queue=WORKFLOW_TASK_QUEUE,
                            memo=memo,
                        )
                    assert not list((tmp_path / "cold").glob("publication-recovery-*"))
                    return
        locator = result["destinationWorkspaceLocator"]
        restored = tmp_path / "cold" / locator["agentRunId"] / "repo"
        assert (restored / "tracked.txt").read_text() == "saved candidate\n"
        assert git(restored, "rev-parse", "HEAD") == identity["headSha"]
        assert root.created_by_principal == "system:agent_runtime"
