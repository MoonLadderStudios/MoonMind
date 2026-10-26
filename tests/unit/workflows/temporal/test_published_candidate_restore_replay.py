"""Replay publication restore requests across the branch-backed handoff fix."""

import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_OMNIGENT_PUBLISHED_CANDIDATE_RESTORE_PATCH,
    RUN_OMNIGENT_REMEDIATION_CHECKPOINT_RESTORE_PATCH,
    MoonMindRunWorkflow,
)


@activity.defn
async def record_publication_restore(payload: dict) -> dict:
    return payload


async def _handoff(restore_ref: str | None) -> dict:
    payload = {"workspaceCheckpointRestoreRef": restore_ref} if restore_ref else {}
    # Activity inputs alone are not compared by Temporal replay. Binding the
    # Activity ID to the request makes a changed restore input observable.
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return await workflow.execute_activity(
        record_publication_restore,
        payload,
        activity_id=f"publication-restore-{digest}",
        start_to_close_timeout=timedelta(seconds=10),
    )


@workflow.defn(name="PublishedCandidateRestoreReplay")
class LegacyPublicationRestore:
    @workflow.run
    async def run(self, trusted: bool) -> dict:
        assert workflow.patched(RUN_OMNIGENT_REMEDIATION_CHECKPOINT_RESTORE_PATCH)
        return await _handoff("artifact://art_initial_archive")


@workflow.defn(name="PublishedCandidateRestoreReplay")
class CurrentPublicationRestore:
    @workflow.run
    async def run(self, trusted: bool) -> dict:
        parent = MoonMindRunWorkflow()
        parent._remediation_loop_runtime = {"mode": "omnigent"}
        head = {"branch": "candidate", "headSha": "a" * 40, "baseBranch": "main"}
        if trusted:
            parent._publish_context["acceptedPublishedHead"] = head
        else:
            # Raw agent metadata cannot displace the checkpoint's authority.
            parent._publish_context.update(head, pushStatus="pushed")
        parent._step_ledger_rows = [
            {"logicalStepId": "implementation", "status": "completed"},
            {"logicalStepId": "publish-pr", "status": "executing"},
        ]
        parent._step_checkpoint_workspace_evidence_by_boundary = {
            "implementation": {
                "before_publication": {
                    "checkpointRef": "art_initial_checkpoint",
                    "workspaceArchiveRef": "art_initial_archive",
                    "workspaceKind": "worktree_archive",
                    "workspaceDigest": "sha256:" + "a" * 64,
                    "workspaceIdentityDigest": "sha256:" + "b" * 64,
                    "checkpointManifestRef": "art_initial_manifest",
                }
            }
        }
        node = {
            "id": "publish-pr",
            "tool": {"type": "agent_runtime", "name": "omnigent"},
            "annotations": {"issueImplementRole": "pull-request-handoff"},
            "inputs": {"runtime": {"mode": "omnigent"}},
        }
        return await _handoff(
            parent._omnigent_publication_checkpoint_restore_ref(
                node=node, node_inputs=node["inputs"]
            )
        )


def _patch_ids(history) -> set[str]:
    return {
        json.loads(payload.data)["id"]
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
        and event.marker_recorded_event_attributes.marker_name == "core_patch"
        for payload in event.marker_recorded_event_attributes.details[
            "patch-data"
        ].payloads
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [True, False])
async def test_publication_restore_preserves_retained_requests_and_new_authority(
    trusted,
):
    histories = []
    async with await WorkflowEnvironment.start_time_skipping() as env:
        for kind, implementation in (
            ("legacy", LegacyPublicationRestore),
            ("current", CurrentPublicationRestore),
        ):
            queue = f"publication-restore-{kind}-{trusted}"
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[implementation],
                activities=[record_publication_restore],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    implementation.run, trusted, id=queue, task_queue=queue
                )
                expected = (
                    {}
                    if kind == "current" and trusted
                    else {
                        "workspaceCheckpointRestoreRef": "artifact://art_initial_archive"
                    }
                )
                assert await handle.result() == expected
                history = await handle.fetch_history()
                histories.append(history)
                assert (
                    RUN_OMNIGENT_PUBLISHED_CANDIDATE_RESTORE_PATCH
                    in _patch_ids(history)
                ) == (kind == "current" and trusted)
    replayer = Replayer(
        workflows=[CurrentPublicationRestore],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)
