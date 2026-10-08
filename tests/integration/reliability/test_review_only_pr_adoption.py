"""Native PR adoption consumes frozen review authority before a gate starts."""

from dataclasses import replace

import httpx
import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.config.settings import settings
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
from moonmind.workflows.skills.tool_registry import (
    create_registry_snapshot,
    parse_tool_registry,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalSkillActivities,
    _default_registry_skill_payload,
)
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)
from tests.integration.reliability.test_repository_access_consumers_4676 import (
    _REPOSITORY,
    _WORKFLOW,
    _ready_opencode_image_pair,  # noqa: F401
    repository_consumers as _repository_consumers,
)

repository_consumers = _repository_consumers

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("selector", ["350", "feature"])
@pytest.mark.parametrize("ambient_token", [None, "wrong-ambient-credential"])
async def test_native_target_dispatch_uses_selected_connection(
    repository_consumers, monkeypatch, selector, ambient_token
):
    compiled = await repository_consumers.compile(
        profile_tools=("gh",),
        workflow={
            "instructions": "Request a fresh review only.",
            "publish": {
                "mode": "none",
                "mergeAutomation": {
                    "enabled": True,
                    "finishMode": "review_only",
                    "reviewLoop": {"enabled": True, "provider": "codex"},
                },
            },
        },
    )
    for key in (
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "WORKFLOW_GITHUB_TOKEN",
        "GITHUB_TOKEN_SECRET_REF",
        "WORKFLOW_GITHUB_TOKEN_SECRET_REF",
        "MOONMIND_GITHUB_TOKEN_REF",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(settings.github, "github_token_secret_ref", "")
    if ambient_token:
        monkeypatch.setenv("GITHUB_TOKEN", ambient_token)
    original_client = httpx.AsyncClient
    requests = []
    pr = {
        "number": 350,
        "state": "open",
        "merged": False,
        "draft": False,
        "html_url": f"https://github.com/{_REPOSITORY}/pull/350",
        "head": {"sha": "a" * 40, "ref": "feature", "repo": {"full_name": _REPOSITORY}},
        "base": {"ref": "main"},
    }

    def read_pr(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer selected-credential-canary"
        assert request.method == "GET"
        return httpx.Response(
            200, json=[pr] if request.url.path.endswith("/pulls") else pr
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            **kwargs, transport=httpx.MockTransport(read_pr), trust_env=False
        ),
    )
    name = "github.resolve_pull_request_target"
    snapshot = create_registry_snapshot(
        skills=parse_tool_registry(
            {"tools": [_default_registry_skill_payload(name=name)]}
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    dispatcher = ToolActivityDispatcher()
    register_story_output_tool_handlers(dispatcher)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=_WORKFLOW, workflow_run_id="run-1"
    )
    result = await environment.run(
        TemporalSkillActivities(dispatcher=dispatcher).mm_tool_execute,
        invocation_payload={
            "id": "resolve-target",
            "tool": {"type": "skill", "name": name},
            "inputs": {"repository": _REPOSITORY, "pullRequest": selector},
        },
        registry_snapshot=snapshot,
        principal=f"workflow:{_WORKFLOW}",
        context={
            "namespace": "default",
            "workflow_id": _WORKFLOW,
            "run_id": "run-1",
            "node_id": "resolve-target",
            "repositoryAuthority": {
                "executionOwner": _WORKFLOW,
                "parentExecutionPlan": compiled.binding.model_dump(by_alias=True),
            },
        },
    )
    assert result.status == "COMPLETED", result.outputs
    assert result.outputs["headSha"] == "a" * 40
    assert len(requests) == (2 if selector == "feature" else 1)
