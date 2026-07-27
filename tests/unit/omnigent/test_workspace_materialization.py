import hashlib
import json
import subprocess

import pytest

from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError


class _Artifacts:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.reads: list[tuple[str, str]] = []

    async def read(
        self, *, artifact_id: str, principal: str, allow_restricted_raw: bool
    ):
        self.reads.append((artifact_id, principal))
        if artifact_id not in self.payloads:
            raise KeyError(artifact_id)
        return {}, self.payloads[artifact_id]


def _git(*args: str, cwd) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_normal_workflow_materializes_owned_repository_once(tmp_path) -> None:
    jobs = tmp_path / "jobs"
    source = jobs / "sources" / "source"
    source.parent.mkdir(parents=True)
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    _git("config", "user.name", "MoonMind Test", cwd=source)
    (source / "README.md").write_text("source\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    source_commit = _git("rev-parse", "HEAD", cwd=source)

    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=jobs)
    artifacts = _Artifacts(
        {
            "attachment-1": b"attachment contents\n",
            "restore-1": b'{"checkpoint":"contents"}\n',
        }
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
            "repository": str(source),
            "startingBranch": "main",
            "targetBranch": "issue-3507",
        },
        input_refs=("artifact://attachment-1", "artifact://restore-1"),
        resolved_skillset_ref="artifact:skills-1",
        artifact_gateway=artifacts,
    )

    assert _git("branch", "--show-current", cwd=workspace) == "issue-3507"
    evidence = json.loads(
        (workspace.parent / ".moonmind-workspace.json").read_text(encoding="utf-8")
    )
    assert evidence["sourceCommit"] == source_commit
    assert evidence["inputRefs"] == [
        "artifact://attachment-1",
        "artifact://restore-1",
    ]
    materialized = evidence["materializedInputRefs"]
    assert [item["artifactRef"] for item in materialized] == evidence["inputRefs"]
    assert all(not item["localPath"].startswith("/") for item in materialized)
    assert (workspace / materialized[0]["localPath"]).read_bytes() == (
        b"attachment contents\n"
    )
    assert (workspace / materialized[1]["localPath"]).read_bytes() == (
        b'{"checkpoint":"contents"}\n'
    )
    assert all(item["sha256"].startswith("sha256:") for item in materialized)

    (workspace / "dirty.txt").write_text("preserve me", encoding="utf-8")
    retry = await runtime._prepare_workspace(
        workspace_locator={
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        workspace_spec={
            "repository": str(source),
            "startingBranch": "main",
            "targetBranch": "issue-3507",
        },
        input_refs=("artifact://attachment-1", "artifact://restore-1"),
        resolved_skillset_ref="artifact:skills-1",
        artifact_gateway=artifacts,
    )
    assert retry == workspace
    assert (retry / "dirty.txt").read_text(encoding="utf-8") == "preserve me"
    assert len(artifacts.reads) == 2


@pytest.mark.asyncio
async def test_input_materialization_fails_closed_before_host_mutation(tmp_path) -> None:
    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=tmp_path)

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator={
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            },
            current_workflow_id=workflow_id,
            current_step_execution_id=step_id,
            workspace_spec={},
            input_refs=("checkpoint:untrusted-path",),
            artifact_gateway=_Artifacts({}),
        )

    assert exc.value.code == "WORKSPACE_INPUT_REF_UNSUPPORTED"
    assert not (tmp_path / "repo").exists()


@pytest.mark.asyncio
async def test_retry_rejects_changed_workspace_intent_before_mutation(tmp_path) -> None:
    workflow_id = "workflow-3507"
    step_id = "workflow-3507:step:1"
    workspace_id = hashlib.sha256(f"{workflow_id}:{step_id}".encode()).hexdigest()[:24]
    runtime = OmnigentOAuthHostRuntime(client=None, workspace_root=tmp_path)
    locator = {
        "kind": "sandbox",
        "workspaceId": workspace_id,
        "relativePath": "repo",
    }
    await runtime._prepare_workspace(
        workspace_locator=locator,
        current_workflow_id=workflow_id,
        current_step_execution_id=step_id,
        workspace_spec={},
    )

    with pytest.raises(OmnigentOAuthHostError) as exc:
        await runtime._prepare_workspace(
            workspace_locator=locator,
            current_workflow_id=workflow_id,
            current_step_execution_id=step_id,
            workspace_spec={"targetBranch": "different"},
        )
    assert exc.value.code == "WORKSPACE_IDENTITY_MISMATCH"
