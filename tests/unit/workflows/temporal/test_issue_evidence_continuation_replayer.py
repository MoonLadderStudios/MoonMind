"""Real Temporal histories for the production delayed-evidence owner.

HTTP/artifact-store integration is covered by test_github_issue_evidence. These
tests use real Activity/child commands, timers, worker eviction/restart and the
SDK Replayer to protect the workflow patch and continuation command sequence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.run import (
    RUN_ISSUE_EVIDENCE_CONTINUATION_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.test_remediation_loop import _spec, _state

pytestmark = pytest.mark.temporal_boundary

_HEAD = "a" * 40
_REPOSITORY = "example/repo"
_PR_URL = f"https://github.com/{_REPOSITORY}/pull/7"
_SUBJECT = {
    "repository": _REPOSITORY,
    "revision": _HEAD,
    "contentDigest": "git-tree:" + "c" * 40,
}
_NODES = [
    {
        "id": "verify",
        "annotations": {"issueImplementRole": "moonspec-verification-gate"},
        "tool": {"type": "agent_runtime", "name": "codex_cli"},
        "inputs": {
            "selectedSkill": "moonspec-verify",
            "instructions": "Verify the original criterion.",
            "runtime": {"mode": "codex_cli"},
        },
    }
]


def _digest(value):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )


def _activity_options():
    return {
        "task_queue": workflow.info().task_queue + "-activities",
        "start_to_close_timeout": timedelta(seconds=10),
    }


async def _run_owner(*, current: bool):
    published = await workflow.execute_activity(
        "issue-evidence.fixture.publish", {}, **_activity_options()
    )
    owner = MoonMindRunWorkflow()
    owner._repo = _REPOSITORY
    owner._assessment_context = {
        "assessmentArtifactRef": "artifact://initial-assessment",
        "briefArtifactRef": "artifact://original-source",
    }
    owner._moonspec_draft_publication_reason = "Required exact-head proof is pending"
    owner._moonspec_gate_verdict = "BLOCKED"
    owner._publish_context = {
        "headSha": published["headSha"],
        "branch": "issue-2740",
        "baseRef": "main",
        "pullRequestUrl": published["url"],
        "moonSpecDraftPublication": {"policy": "draft_pr_on_environment_blocked"},
        "moonSpecGate": {"verdict": "BLOCKED", "recommendedNextAction": "blocked"},
    }
    owner._remediation_loop_spec, owner._remediation_loop_state = _spec(), _state()
    owner._principal = lambda: "workflow:" + workflow.info().workflow_id
    owner._update_memo = lambda: None
    owner._update_search_attributes = lambda: None
    owner._workflow_child_task_queue = lambda: workflow.info().task_queue + "-children"
    owner._execute_kwargs_for_route = lambda _route: _activity_options()

    async def persist(*, name, payload):
        return await workflow.execute_activity(
            "issue-evidence.fixture.persist",
            {"name": name, "payload": payload},
            **_activity_options(),
        )

    async def skillset(**_kwargs):
        return "artifact://resolved-verifier-skillset"

    owner._write_json_artifact = persist
    owner._resolve_agent_node_skillset_ref = skillset
    if current:
        # This is the production patch boundary and owner, not a test replica.
        await owner._resume_published_issue_evidence(
            parameters={"publishMode": "pr"}, ordered_nodes=_NODES
        )
    return {
        "url": owner._publish_context["pullRequestUrl"],
        "headSha": owner._publish_context["headSha"],
        "draftPreserved": owner._moonspec_draft_publication_reason is not None,
        "continuation": owner._publish_context.get("issueEvidenceContinuation"),
        "implementationAttempts": owner._remediation_loop_state.consumed_budgets.attempts,
        "evidenceRetries": owner._remediation_loop_state.consumed_budgets.evidence_retries,
    }


@workflow.defn(name="IssueEvidenceReplayFixture")
class _LegacyOwner:
    @workflow.run
    async def run(self) -> dict:
        return await _run_owner(current=False)


@workflow.defn(name="IssueEvidenceReplayFixture")
class _CurrentOwner:
    @workflow.run
    async def run(self) -> dict:
        return await _run_owner(current=True)


@workflow.defn(name="MoonMind.AgentRun")
class _VerifierChild:
    @workflow.run
    async def run(self, request: AgentExecutionRequest) -> dict:
        return await workflow.execute_activity(
            "issue-evidence.fixture.verify",
            request,
            task_queue=workflow.info().task_queue.removesuffix("-children")
            + "-activities",
            start_to_close_timeout=timedelta(seconds=10),
        )


def _activities():
    calls, saved, verified = [], [], []
    collections = 0

    @activity.defn(name="issue-evidence.fixture.publish")
    async def publish(_payload: dict) -> dict:
        return {"url": _PR_URL, "headSha": _HEAD}

    @activity.defn(name="issue-evidence.fixture.persist")
    async def persist(payload: dict) -> str:
        saved.append(payload)
        return "artifact://" + _digest(payload).replace(":", "-")

    @activity.defn(name="github_issue.collect_evidence")
    async def collect(payload: dict) -> dict:
        nonlocal collections
        calls.append(payload)
        assert (
            payload["repository"] == _REPOSITORY
            and payload["prNumber"] == 7
            and payload["headSha"] == _HEAD
        )
        if payload.get("action") in {"verify_candidate", "mark_ready"}:
            return {
                "status": "ready",
                "headSha": _HEAD,
                "observedHeadSha": _HEAD,
                "pullRequestOpen": True,
            }
        collections += 1
        # The repeated second collection represents new delivery IDs containing
        # the same actual bytes. It must not dispatch a duplicate verifier.
        complete = collections >= 3
        inventory = {
            "artifact://complete" if complete else "artifact://partial": "sha256:"
            + ("2" if complete else "1") * 64
        }
        return {
            "status": "ready",
            "headSha": _HEAD,
            "subject": _SUBJECT,
            "semanticDigest": "sha256:" + ("b" if complete else "a") * 64,
            "evidenceSet": inventory,
            "evidenceSetDigest": _digest(inventory),
            "artifactRef": f"artifact://manifest-delivery-{collections}",
            "evidenceRefs": list(inventory),
            "runIds": [101, 102],
            "collectionComplete": complete,
            "pendingRunIds": [] if complete else [102],
            "gaps": [],
        }

    @activity.defn(name="issue-evidence.fixture.verify")
    async def verify(request: AgentExecutionRequest) -> dict:
        verified.append(request)
        assert request.parameters["publishMode"] == "none"
        assert (
            request.workspace_spec["repositoryTarget"]["revision"]["commitSha"] == _HEAD
        )
        if "artifact://partial" in request.parameters["evidenceSet"]:
            gate = {
                "verdict": "NO_DETERMINATION",
                "recommendedNextAction": "blocked",
                "remainingWorkRef": "artifact://missing-proof",
            }
        else:
            scope = {
                "sourceRef": f"{_REPOSITORY}#2740",
                "sourceDigest": "sha256:original",
                "complete": True,
                "requirementIds": ["AC-1"],
            }
            plan = {
                "id": "AC-1",
                "sourceRef": scope["sourceRef"],
                "sourceDigest": scope["sourceDigest"],
                "sourceLocator": "acceptance[0]",
                "classification": "mandatory",
                "requiredBehavior": "Execute required boundary checks",
                "executableChecks": ["pytest boundary"],
                "proofMethods": ["automated_tests"],
                "owner": "existing CI",
            }
            gate = {
                "verdict": "FULLY_IMPLEMENTED",
                "recommendedNextAction": "advance",
                "validatedRefs": {
                    "acceptance": {
                        "schemaVersion": "acceptance/v1",
                        "subject": _SUBJECT,
                        "scope": scope,
                        "completionTarget": {
                            "ref": "refs/heads/main",
                            "revision": "base",
                            "contentDigest": "git-tree:base",
                        },
                        "freshness": {"policy": "content"},
                        "verificationMap": [plan],
                        "evidenceSet": request.parameters["evidenceSet"],
                        "evidenceSetDigest": request.parameters["evidenceSetDigest"],
                        "evidence": [
                            {
                                "requirementId": "AC-1",
                                "status": "VERIFIED",
                                "obligationDigest": _digest(plan),
                                "evidenceRefs": list(request.parameters["evidenceSet"]),
                                "proofMethod": "automated_tests",
                            }
                        ],
                    }
                },
            }
        ref = "artifact://gate-" + str(len(verified))
        return {
            "metadata": {
                "moonSpecVerify": {**gate, "gateResultRef": ref},
                "sourceMoonSpecVerifyArtifactRef": ref,
            }
        }

    return [publish, persist, collect, verify], calls, saved, verified


def _scheduled(history):
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


@pytest.mark.asyncio
async def test_old_completed_history_replays_without_new_evidence_commands():
    activities, calls, saved, verified = _activities()
    queue = "issue-evidence-legacy"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env,
        Worker(
            env.client,
            task_queue=queue,
            workflows=[_LegacyOwner],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
        Worker(env.client, task_queue=queue + "-activities", activities=activities),
    ):
        handle = await env.client.start_workflow(
            _LegacyOwner.run, id=queue, task_queue=queue
        )
        result = await handle.result()
        history = await handle.fetch_history()
    assert result["draftPreserved"] and result["continuation"] is None
    assert _scheduled(history) == ["issue-evidence.fixture.publish"]
    assert RUN_ISSUE_EVIDENCE_CONTINUATION_PATCH not in history.to_json()
    await Replayer(
        workflows=[_CurrentOwner],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    ).replay_workflow(history)
    assert not calls and not saved and not verified


@pytest.mark.asyncio
async def test_partial_inventory_wait_survives_worker_restart_and_real_replay():
    activities, calls, saved, verified = _activities()
    queue = "issue-evidence-current"
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env,
        Worker(env.client, task_queue=queue + "-activities", activities=activities),
        Worker(
            env.client,
            task_queue=queue + "-children",
            workflows=[_VerifierChild],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_CurrentOwner],
            max_cached_workflows=0,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _CurrentOwner.run, id=queue, task_queue=queue
            )

            async def wait_for_durable_timer():
                while True:
                    history = await handle.fetch_history()
                    if any(
                        event.HasField("timer_started_event_attributes")
                        for event in history.events
                    ):
                        return history
                    await asyncio.sleep(0.01)

            pending = await asyncio.wait_for(wait_for_durable_timer(), 30)
            assert len(verified) == 1
            assert any(row["payload"].get("status") == "waiting" for row in saved)
        # No old worker survives: the same recorded owner resumes the timer,
        # consumes new bytes, and emits exactly one additional verifier child.
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[_CurrentOwner],
            max_cached_workflows=0,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await asyncio.wait_for(handle.result(), 30)
            history = await handle.fetch_history()
    assert result["url"] == _PR_URL and result["headSha"] == _HEAD
    assert (
        not result["draftPreserved"] and result["continuation"]["status"] == "accepted"
    )
    assert result["implementationAttempts"] == 0 and result["evidenceRetries"] == 1
    assert len(verified) == 2
    assert [row.get("action", "collect") for row in calls] == [
        "collect",
        "verify_candidate",
        "collect",
        "collect",
        "verify_candidate",
        "mark_ready",
    ]
    children = [
        event.start_child_workflow_execution_initiated_event_attributes.workflow_id
        for event in history.events
        if event.HasField("start_child_workflow_execution_initiated_event_attributes")
    ]
    assert children == [queue + ":issue-evidence:1", queue + ":issue-evidence:2"]
    before = (len(calls), len(saved), len(verified))
    replayer = Replayer(
        workflows=[_CurrentOwner],
        workflow_runner=UnsandboxedWorkflowRunner(),
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    await replayer.replay_workflow(pending)
    await replayer.replay_workflow(history)
    assert (len(calls), len(saved), len(verified)) == before
