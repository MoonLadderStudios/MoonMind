"""Replay the old literal comparison and the repaired status handoff."""

import asyncio
import gzip
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "fixtures/temporal/acceptance_repository_identity"
)


def _gate():
    subject = {
        "repository": "https://github.com/org/repo.git",
        "revision": "a" * 40,
        "contentDigest": "git-tree:" + "b" * 40,
    }
    return {
        "verdict": "FULLY_IMPLEMENTED",
        "recommendedNextAction": "advance",
        "validatedRefs": {
            "acceptance": {
                "schemaVersion": "acceptance/v1",
                "subject": subject,
                "scope": {
                    "sourceRef": "org/repo#1",
                    "sourceDigest": "sha256:" + "c" * 64,
                    "complete": True,
                    "requirementIds": ["AC-1"],
                },
                "completionTarget": {
                    "ref": "refs/heads/main",
                    "revision": subject["revision"],
                    "contentDigest": subject["contentDigest"],
                },
                "evidence": [
                    {
                        "requirementId": "AC-1",
                        "unverified": {
                            "reason": "No render surface in this fixture.",
                            "verifiableBy": "render CI",
                        },
                    }
                ],
                "freshness": {"policy": "content"},
            }
        },
    }


@activity.defn(name="acceptance_identity.capture")
async def _capture(value: dict) -> dict:
    return value


@workflow.defn(name="AcceptanceRepositoryIdentityFixture")
class _Handoff:
    @workflow.run
    async def run(self) -> dict:
        owner = MoonMindRunWorkflow()
        owner._repo = "org/repo"
        owner._canonical_no_commit_outcome_enabled = True
        owner._assessment_context = {"assessmentVerdict": "PARTIALLY_IMPLEMENTED"}
        owner._publish_context["moonSpecGate"] = _gate()
        parameters = {
            "publishMode": "pr",
            "workflow": {
                "appliedStepTemplates": [
                    {"slug": "github-issue-implement", "version": "1.0.0"},
                ]
            },
        }
        owner._record_publish_result(
            parameters=parameters,
            execution_result={"outputs": {"push_status": "no_commits"}},
        )
        try:
            await owner._ensure_issue_implement_pr_before_status(
                node={"annotations": {"issueImplementRole": "code-review-handoff"}},
                parameters=parameters,
            )
            disposition = "completion_target"
        except ValueError as error:
            assert "remotely verified branch" in str(error)
            disposition = "branch_required"
        return await workflow.execute_activity(
            "acceptance_identity.capture",
            {"disposition": disposition, "publication": owner._publish_status},
            start_to_close_timeout=timedelta(seconds=10),
        )


async def _record():
    async with await WorkflowEnvironment.start_time_skipping(
        identity="acceptance-fixture"
    ) as env:
        async with Worker(
            env.client,
            task_queue="acceptance-identity-fixture",
            workflows=[_Handoff],
            activities=[_capture],
            identity="acceptance-fixture",
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                _Handoff.run,
                id="acceptance-identity-fixture",
                task_queue="acceptance-identity-fixture",
            )
            result = await asyncio.wait_for(handle.result(), timeout=30)
            return result, await handle.fetch_history()


@pytest.mark.asyncio
async def test_repository_identity_pre_and_post_patch_histories_replay():
    old = WorkflowHistory.from_json(
        "acceptance-identity-fixture",
        gzip.decompress(
            (FIXTURES / "literal.json.gz").read_bytes(),
        ).decode(),
    )
    replayer = Replayer(
        workflows=[_Handoff], workflow_runner=UnsandboxedWorkflowRunner()
    )
    await replayer.replay_workflow(old)
    result, current = await _record()
    assert result == {"disposition": "completion_target", "publication": "not_required"}
    await replayer.replay_workflow(current)


async def _record_legacy():
    destination = FIXTURES / "literal.json.gz"
    if destination.exists():
        raise FileExistsError("The retained pre-change fixture already exists")
    result, history = await _record()
    assert result["disposition"] == "branch_required"
    raw = gzip.compress(history.to_json().encode(), mtime=0)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    root = Path(__file__).resolve().parents[4]
    source = "moonmind/workflows/temporal/workflows/run.py"
    (FIXTURES / "provenance.json").write_text(
        json.dumps(
            {
                "sourceRevision": "9fe8bfa2c0edc253db233f5930bae3bf8d12fc35",
                "sourceFile": source,
                "sourceSha256": hashlib.sha256(
                    (root / source).read_bytes()
                ).hexdigest(),
                "historySha256": hashlib.sha256(raw).hexdigest(),
                "identity": "acceptance-fixture",
                "result": result,
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"result": result, "events": len(history.events)}))


if __name__ == "__main__":
    asyncio.run(_record_legacy())
