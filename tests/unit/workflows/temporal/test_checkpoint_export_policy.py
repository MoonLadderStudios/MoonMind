"""One configured outbound policy covers worktree, index and selected history."""

import json

import pytest

from moonmind.config.settings import SecuritySettings, settings
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.temporal import activity_runtime
from tests.unit.workflows.temporal.test_saved_work_capture import (
    _capture_harness,
    _commit_all,
    _git,
    _request,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["managed", "sandbox"])
@pytest.mark.parametrize("configured_mode", [None, "false", "true"])
@pytest.mark.parametrize(
    "surface", ["worktree", "index", "history", "excluded_history"]
)
async def test_capture_policy_covers_every_export_before_upload(
    tmp_path,
    monkeypatch,
    owner,
    configured_mode,
    surface,
):
    if configured_mode is None:
        monkeypatch.delenv("MOONMIND_HIGH_SECURITY_MODE", raising=False)
    else:
        monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", configured_mode)
    monkeypatch.setattr(settings, "security", SecuritySettings(_env_file=None))
    original_policy = activity_runtime.resolve_high_security_mode
    resolutions = []

    def resolve_once():
        resolutions.append(1)
        return original_policy()

    monkeypatch.setattr(activity_runtime, "resolve_high_security_mode", resolve_once)
    repo, managed, uploaded, digest = _capture_harness(
        tmp_path / "temporal_sandbox",
        {"candidate.txt": "safe base\n"},
    )
    fake_secret = "api_key = synthetic-fixture-value\n"
    if surface == "excluded_history":
        # Same blob as an innocuous baseline file, with no heuristic match.
        # Only the unpublished history retains this excluded path.
        (repo / ".env").write_text("safe base\n")
        _git(repo, "add", ".env")
        _commit_all(repo, "unpublished sensitive path")
        (repo / ".env").unlink()
    else:
        (repo / "candidate.txt").write_text(fake_secret)
        if surface != "worktree":
            _git(repo, "add", "candidate.txt")
            if surface == "history":
                _commit_all(repo, "unpublished fixture")
            (repo / "candidate.txt").write_text("safe final worktree\n")
    # Exclusions remain enforced even when heuristic scanning is disabled.
    (repo / ".codex").mkdir()
    (repo / ".codex/auth.json").write_text(fake_secret)
    _git(repo, "add", ".codex/auth.json")
    store = InMemoryArtifactStore()
    if owner == "managed":
        request = _request(digest=digest)
        capture = managed.agent_runtime_capture_workspace_checkpoint

        def read(ref):
            return next(
                payload
                for payload, _, stored_ref in uploaded.values()
                if stored_ref == ref
            )

    else:
        request = {
            "identity": {
                "workflowId": "wf-1",
                "runId": "run-1",
                "logicalStepId": "implement",
                "executionOrdinal": 1,
            },
            "boundary": "after_execution",
            "kind": "worktree_archive",
            "workspacePath": str(repo),
            "artifactNamespace": "checkpoint",
            "idempotencyKey": "sandbox-capture",
            "includeUntracked": True,
        }
        capture = activity_runtime.TemporalSandboxActivities(
            workspace_root=tmp_path,
            artifact_store=store,
        ).workspace_capture_checkpoint
        uploaded = store._data
        read = store.get_bytes

    blocked = configured_mode == "true" or surface == "excluded_history"
    failure = (
        "credential-sensitive path"
        if surface == "excluded_history"
        else "secret scanning"
    )
    before = _git(repo, "status", "--porcelain=v1", "-z")
    head = _git(repo, "rev-parse", "HEAD")
    try:
        result = await capture(request)
    except Exception as exc:
        assert blocked
        assert failure in str(exc)
        assert not uploaded
    else:
        if blocked:
            assert result["status"] == "invalid"
            assert failure in result["summary"]
            assert not uploaded
        else:
            assert result["status"] == "captured"
            manifest = json.loads(read(result["workspace"]["manifestRef"]))
            assert ".codex/auth.json" not in {
                entry["path"] for entry in manifest["entries"]
            }
            assert ".codex/auth.json" not in manifest["git"]["indexPatch"]["paths"]
    assert resolutions == [1]
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain=v1", "-z") == before
