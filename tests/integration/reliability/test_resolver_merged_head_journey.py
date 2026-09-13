"""Real Temporal child completion, remote PR reads and exact-head validation.

The resolver mutates a local HTTP GitHub fixture, never a real repository. The
finalization boundary uses the production issue-handoff validator; status-label
mutation itself is outside this test's scope.
"""

import asyncio
import json
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from temporalio import activity, workflow
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.adapters.github_service import GitHubService
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.story_output_tools import (
    _validate_post_merge_issue_handoff,
)
from moonmind.workflows.temporal.workflows import merge_automation as module
from tests.integration.reliability.test_resolver_verification_capability_journey import (
    resolver_test_client,
)
from tests.unit.workflows.temporal.workflows.test_merge_automation_temporal import (
    _payload,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn(name="MoonMind.UserWorkflow")
class ResolverFixture:
    @workflow.run
    async def run(self, payload: dict) -> dict:
        return await workflow.execute_activity(
            "qualification.merge_fixture",
            {},
            task_queue=workflow.info().task_queue,
            start_to_close_timeout=timedelta(seconds=15),
        )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("disposition", ["merged", "already_merged"])
async def test_resolver_merged_revision_crosses_parent_and_issue_boundary(
    monkeypatch, legacy, disposition
):
    fixture = json.loads(
        Path("tests/fixtures/reliability/resolver-merged-head-handoff.json").read_text()
    )
    repo = fixture["repository"]
    pr_number = fixture["prNumber"]
    state = {"merged": False}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path == f"/repos/{repo}/pulls/{pr_number}":
                body = {
                    "number": pr_number,
                    "state": "closed" if state["merged"] else "open",
                    "merged": state["merged"],
                    "merge_commit_sha": (
                        fixture["mergeCommit"] if state["merged"] else None
                    ),
                    "head": {
                        "sha": (
                            fixture["mergedHead"]
                            if state["merged"]
                            else fixture["publishedHead"]
                        ),
                        "ref": "feature",
                    },
                    "base": {"ref": "main", "repo": {"full_name": repo}},
                    "title": f"Resolve #{fixture['issueNumber']}",
                    "body": "",
                }
            elif self.path.startswith(f"/repos/{repo}/commits/"):
                body = {
                    "sha": fixture["mergeCommit"],
                    "commit": {"tree": {"sha": "fixture-tree"}},
                }
            else:
                self.send_error(404)
                return
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_client = httpx.AsyncClient

    class LocalGithubTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            assert request.url.host == "api.github.com"
            request.url = request.url.copy_with(
                scheme="http", host="127.0.0.1", port=server.server_port
            )
            return await super().handle_async_request(request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=LocalGithubTransport(), **kwargs),
    )
    monkeypatch.setattr(
        GitHubService,
        "resolve_github_token",
        AsyncMock(return_value=("fixture-only", None)),
    )
    original_patched = module.workflow.patched
    if legacy:
        monkeypatch.setattr(
            module.workflow,
            "patched",
            lambda name: (
                False
                if name == module.MERGE_AUTOMATION_RESOLVER_MERGE_CONFIRMATION_PATCH
                else original_patched(name)
            ),
        )
    queue = "resolver-merge-" + uuid4().hex
    client = await resolver_test_client()
    monkeypatch.setattr(module, "INTEGRATIONS_TASK_QUEUE", queue)
    monkeypatch.setattr(module, "ARTIFACTS_TASK_QUEUE", queue)
    monkeypatch.setattr(
        module.settings,
        "temporal",
        module.settings.temporal.model_copy(
            update={"user_workflow_v2_task_queue": queue}
        ),
    )
    handoffs = []

    @activity.defn(name="merge_automation.evaluate_readiness")
    async def readiness(payload: dict) -> dict:
        return await TemporalIntegrationActivities.merge_automation_evaluate_readiness(
            object(), payload
        )

    @activity.defn(name="qualification.merge_fixture")
    async def merge_fixture(payload: dict) -> dict:
        state["merged"] = True
        return {**fixture["resolverResult"], "mergeAutomationDisposition": disposition}

    @activity.defn(name="merge_automation.complete_post_merge_github")
    async def finalize(payload: dict) -> dict:
        handoffs.append(payload["pullRequest"])
        reason = await _validate_post_merge_issue_handoff(
            GitHubService(),
            repository=repo,
            issue_ref=f"{repo}#{fixture['issueNumber']}",
            pull_request=payload["pullRequest"],
        )
        return {
            "status": "failed" if reason else "succeeded",
            "required": True,
            "summary": reason,
        }

    @activity.defn(name="artifact.create")
    async def create(payload: dict) -> list:
        return [{"artifact_id": "art-fixture-" + uuid4().hex}, {}]

    @activity.defn(name="artifact.write_complete")
    async def write(payload: dict) -> dict:
        return {}

    payload = _payload()
    payload.pop("jiraIssueKey", None)
    payload["pullRequest"].update(
        repo=repo,
        number=pr_number,
        url=f"https://github.com/{repo}/pull/{pr_number}",
        headSha=fixture["publishedHead"],
    )
    payload["mergeAutomationConfig"]["gate"]["github"] = {
        "checks": "optional",
        "automatedReview": "optional",
    }
    payload["mergeAutomationConfig"]["postMergeGithub"] = {
        "enabled": True,
        "required": True,
        "repository": repo,
        "issueNumber": fixture["issueNumber"],
    }
    try:
        async with Worker(
            client,
            task_queue=queue,
            workflows=[module.MoonMindMergeAutomationWorkflow, ResolverFixture],
            activities=[readiness, merge_fixture, finalize, create, write],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await client.start_workflow(
                module.MoonMindMergeAutomationWorkflow.run,
                payload,
                id=queue,
                task_queue=queue,
                execution_timeout=timedelta(seconds=45),
            )
            result = await asyncio.wait_for(handle.result(), timeout=50)
            history = await handle.fetch_history()
        assert result["status"] == ("failed" if legacy else disposition)
        assert len(handoffs) == 1
        assert (
            handoffs[0]["headSha"]
            == fixture["publishedHead" if legacy else "mergedHead"]
        )
        if legacy:
            assert result["summary"] == fixture["escapedFailure"]
        else:
            assert result["latestHeadSha"] == fixture["mergedHead"]
            assert any("/commits/" in path for path in requests)
        monkeypatch.setattr(module.workflow, "patched", original_patched)
        await Replayer(
            workflows=[module.MoonMindMergeAutomationWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        ).replay_workflow(history)
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
