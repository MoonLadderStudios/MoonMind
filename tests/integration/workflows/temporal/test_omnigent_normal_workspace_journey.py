import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


class _Artifacts:
    async def read(self, *, artifact_id: str, **_kwargs):
        return {}, f"payload:{artifact_id}".encode()


def _git(*args: str, cwd) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_normal_workflow_intent_reaches_owned_omnigent_workspace(tmp_path) -> None:
    source = tmp_path / "sources" / "repository"
    source.mkdir(parents=True)
    _git("init", "-b", "main", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    _git("config", "user.name", "MoonMind Test", cwd=source)
    (source / "README.md").write_text("source\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "initial", cwd=source)

    planner = _build_runtime_planner()
    plan = planner(
        inputs={
            "task": {
                "instructions": "Update the repository",
                "title": "Normal Omnigent workflow",
                "repository": str(source),
                "git": {"branch": "main"},
                "runtime": {"mode": "omnigent"},
                "workspaceSpec": {"targetBranch": "issue-3507"},
                "publish": {
                    "mode": "pr",
                    "baseBranch": "main",
                    "commitMessage": "Complete #3507",
                },
            }
        },
        parameters={},
        snapshot=SimpleNamespace(
            digest="reg:sha256:test",
            artifact_ref="artifact:registry",
        ),
    )
    authored = plan["nodes"][0]["inputs"]
    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    runtime = OmnigentOAuthHostRuntime(
        client=None,
        workspace_root=tmp_path / "jobs",
    )

    workspace = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        workspace_spec={
            "repository": authored["repository"],
            "startingBranch": authored["startingBranch"],
            "targetBranch": authored["targetBranch"],
            "publishMode": authored["publishMode"],
            "publishBaseBranch": authored["publishBaseBranch"],
            "commitMessage": authored["commitMessage"],
            "repositoryMutationRequired": True,
            "requiredCapabilities": ["gh"],
            "githubCredentialRequired": True,
        },
        input_refs=("artifact:attachment-1", "checkpoint:restore-1"),
        resolved_skillset_ref="artifact:skills-1",
        artifact_gateway=_Artifacts(),
    )

    assert _git("branch", "--show-current", cwd=workspace) == "issue-3507"
    evidence = json.loads(
        (workspace.parent / ".moonmind-workspace.json").read_text(encoding="utf-8")
    )
    assert evidence["startingBranch"] == "main"
    assert evidence["targetBranch"] == "issue-3507"
    assert evidence["publishMode"] == "pr"
    assert evidence["publishBaseBranch"] == "main"
    assert evidence["commitMessage"] == "Complete #3507"
    assert evidence["resolvedSkillsetRef"] == "artifact:skills-1"
    assert evidence["githubCredentialRequired"] is True
    assert [item["artifactRef"] for item in evidence["materializedInputRefs"]] == [
        "artifact:attachment-1",
        "checkpoint:restore-1",
    ]
