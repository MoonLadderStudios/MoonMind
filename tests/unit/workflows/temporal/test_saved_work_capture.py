"""Saved-work capture contracts for MoonLadderStudios/MoonMind#4015.

Covers the plan-slice-4 deliverables against the agreed
artifact/checkpoint/format contracts: the compact saved-work manifest,
format-profile portability claims, the single quiescent capture generation,
deterministic bounded identities, retry-to-content binding, binary-safe
deltas/selected-history rules, export-byte secret controls, and the immutable
manifest-commit step.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from moonmind.schemas.agent_runtime_models import ManagedRunRecord
from moonmind.schemas.managed_checkpoint_models import (
    ManagedWorkspaceCheckpointCaptureInput,
    ManagedWorkspaceCheckpointCaptureResult,
)
from moonmind.schemas.saved_work_models import (
    SAVED_WORK_MANIFEST_SCHEMA_VERSION,
    assert_complete_payload_matches,
    assert_single_capture_generation,
    build_saved_work_manifest,
    commit_saved_work_manifest,
    compute_saved_work_delta,
    describe_output_claim,
    parse_git_diff_raw_to_deltas,
    redacted_preview_is_restorable,
    resolve_saved_work_format_profile,
    scan_saved_work_export,
    scan_saved_work_export_stream,
    snapshot_capture_generation,
    thin_bundle_is_portable,
    verify_captured_artifact_evidence,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _request(*, digest: str, key: str = "saved-work-1:capture") -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "identity": {
            "workflowId": "wf-1",
            "runId": "run-1",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "boundary": "after_execution",
        "checkpointKind": "worktree_archive",
        "workspaceLocator": {
            "kind": "managed_runtime",
            "runtimeId": "codex_cli",
            "agentRunId": "agent-run-1",
            "relativePath": "repo",
        },
        "expectedRuntimeId": "codex_cli",
        "capabilitySetVersion": "runtime-execution-capabilities-v1",
        "capabilityDigest": digest,
        "artifactNamespace": "step-checkpoints/implement",
        "idempotencyKey": key,
        "capturePolicy": {
            "includeTracked": True,
            "includeUntracked": True,
            "includeIgnored": False,
            "redactionProfile": "managed-code-workspace-v1",
        },
    }


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit_all(repo, message: str = "base") -> None:
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        message,
    )


def _capture_harness(tmp_path, files: dict[str, bytes | str]):
    """Create a committed repo, run record, and stubbed capture activities."""
    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
    _git(repo, "add", "-A")
    _commit_all(repo)
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1",
            workflowId="wf-1",
            agentId="codex_cli",
            ownerRunId="run-1",
            logicalStepId="implement",
            executionOrdinal=1,
            runtimeId="codex_cli",
            status="completed",
            startedAt=now,
            finishedAt=now,
            workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    stored: dict[str, tuple[bytes, str, str]] = {}

    async def put(payload: bytes, content_type: str, kind: str) -> str:
        ref = "artifact://" + hashlib.sha256(payload).hexdigest()
        stored[kind] = (payload, content_type, ref)
        return ref

    activities._put_managed_checkpoint_artifact = put
    digest = resolve_runtime_execution_capabilities("codex_cli").capability_digest
    return repo, activities, stored, digest


def test_saved_work_manifest_records_provenance_without_copied_policy() -> None:
    manifest = build_saved_work_manifest(
        capture_id="capture-1",
        identity={"workflowId": "wf-1", "boundary": "after_execution"},
        source={"kind": "managed-code-workspace", "baselineCommit": "abc"},
        content_digest="sha256:" + "a" * 64,
        file_manifest_digest="sha256:" + "b" * 64,
        capture_policy={"includeUntracked": True},
        required_formats=["full_snapshot"],
        optional_formats=["exact_baseline_delta"],
        outputs=[
            describe_output_claim(
                fmt="full_snapshot",
                status="self_contained",
                ref="artifact://x",
                digest="sha256:" + "a" * 64,
                size_bytes=10,
            )
        ],
        exclusions=[{"path": ".env", "reason": "sensitive-filename-policy"}],
        scan={"disposition": "clean"},
        dependencies=["git-baseline:abc"],
    )
    assert manifest["schemaVersion"] == SAVED_WORK_MANIFEST_SCHEMA_VERSION
    assert manifest["manifestDigest"].startswith("sha256:")
    # ACL/retention resolve through ownership, never editable copied policy.
    assert "retentionPolicy" not in manifest
    assert "acl" not in manifest
    with pytest.raises(ValueError, match="dependencies"):
        describe_output_claim(fmt="exact_baseline_delta", status="requires_dependencies")


def test_format_profile_never_synthesizes_git_for_report_only() -> None:
    report = resolve_saved_work_format_profile(
        is_git_workspace=False, requested_result="report"
    )
    assert report["required"] == ["report_only"]
    assert "commit" not in json.dumps(report)
    portable = resolve_saved_work_format_profile(
        is_git_workspace=True, requested_result="portable"
    )
    assert portable["required"] == ["full_snapshot"]
    assert "exact_baseline_delta" in portable["optional"]
    # A thin bundle without baseline objects is not portable merely
    # because its download succeeded.
    assert thin_bundle_is_portable(has_baseline_objects=False) is False
    assert thin_bundle_is_portable(has_baseline_objects=True) is True


def test_single_capture_generation_rejects_mid_capture_mutation() -> None:
    before = snapshot_capture_generation(
        git_head="abc",
        status_digest="sha256:x",
        file_fingerprints={"a.txt": "1:10"},
    )
    assert_single_capture_generation(before, dict(before))
    with pytest.raises(ValueError, match="CONCURRENT_MUTATION"):
        assert_single_capture_generation(
            before, {**before, "gitHead": "def"}
        )
    with pytest.raises(ValueError, match="CONCURRENT_MUTATION"):
        assert_single_capture_generation(
            before,
            snapshot_capture_generation(
                git_head="abc",
                status_digest="sha256:x",
                file_fingerprints={"a.txt": "2:10"},
            ),
        )


def test_compute_saved_work_delta_is_binary_safe_and_honest() -> None:
    baseline = [
        {"path": "kept.txt", "type": "file", "mode": "100644", "size": 4, "sha256": "s1"},
        {"path": "gone.txt", "type": "file", "mode": "100644", "size": 4, "sha256": "s2"},
        {"path": "run.sh", "type": "file", "mode": "100644", "size": 4, "sha256": "s3"},
        {"path": "old.txt", "type": "file", "mode": "100644", "size": 4, "sha256": "s4"},
    ]
    captured = [
        {"path": "kept.txt", "type": "file", "mode": "100644", "size": 4, "sha256": "s1"},
        # Binary content changes identity without decoding.
        {"path": "run.sh", "type": "file", "mode": "100755", "size": 4, "sha256": "s3"},
        {"path": "new.txt", "type": "file", "mode": "100644", "size": 4, "sha256": "s4"},
    ]
    deltas = compute_saved_work_delta(
        baseline_entries=baseline, capture_entries=captured
    )
    by_path = {delta["path"]: delta["change"] for delta in deltas}
    assert by_path["gone.txt"] == "deleted"
    assert by_path["run.sh"] == "mode_changed"
    assert by_path["new.txt"] == "renamed"
    # Exclusion is never inferred as deletion.
    assert (
        compute_saved_work_delta(
            baseline_entries=baseline,
            capture_entries=captured,
            excluded_paths=["gone.txt"],
        )
        == [
            delta
            for delta in deltas
            if delta["path"] != "gone.txt"
        ]
    )


def test_parse_git_diff_raw_reports_rename_and_filters_exclusions(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("hello\n")
    (repo / "gone.txt").write_text("gone\n")
    _git(repo, "add", "-A")
    _commit_all(repo)
    (repo / "a.txt").rename(repo / "renamed.txt")
    (repo / "gone.txt").unlink()
    raw = _git(
        repo,
        "diff",
        "HEAD",
        "--raw",
        "-z",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
    )
    deltas = parse_git_diff_raw_to_deltas(raw, set())
    by_path = {delta["path"]: delta for delta in deltas}
    assert by_path["renamed.txt"]["change"] == "renamed"
    assert by_path["renamed.txt"]["oldPath"] == "a.txt"
    assert by_path["gone.txt"]["change"] == "deleted"
    assert parse_git_diff_raw_to_deltas(raw, {"gone.txt", "a.txt", "renamed.txt"}) == []


def test_export_scan_binds_digest_and_reports_limits_explicitly() -> None:
    clean = scan_saved_work_export(
        b"hello world", export_digest="sha256:abc", location="checkpoint.archive"
    )
    assert clean["disposition"] == "clean"
    assert clean["exportDigest"] == "sha256:abc"
    blocked = scan_saved_work_export(
        b"api_key = supersecretvalue123",
        export_digest="sha256:abc",
        location="checkpoint.archive",
    )
    assert blocked["disposition"] == "blocked"
    assert blocked["exportDigest"] == "sha256:abc"
    binary = scan_saved_work_export(
        b"\x00\xff\x01\x02binary",
        export_digest="sha256:abc",
        location="checkpoint.archive",
    )
    assert binary["disposition"] == "unsupported"
    assert binary["limitations"]
    # A chunk-spanning secret is still detected without full buffering.
    spanning = scan_saved_work_export_stream(
        [b"api_key = super", b"secretvalue123"],
        export_digest="sha256:abc",
        location="checkpoint.archive",
    )
    assert spanning["disposition"] == "blocked"
    # A redacted preview is never a byte-identical restorable source.
    assert redacted_preview_is_restorable() is False


def test_commit_requires_manifest_and_keeps_preview_failures_separate() -> None:
    manifest = build_saved_work_manifest(
        capture_id="capture-1",
        identity={"workflowId": "wf-1"},
        source={"kind": "managed-code-workspace"},
        content_digest="sha256:" + "a" * 64,
        file_manifest_digest="sha256:" + "b" * 64,
        capture_policy={},
        required_formats=["full_snapshot"],
        outputs=[
            describe_output_claim(
                fmt="full_snapshot",
                status="self_contained",
                ref="artifact://x",
                digest="sha256:" + "a" * 64,
                size_bytes=1,
            )
        ],
    )
    committed = commit_saved_work_manifest(
        manifest,
        required_refs_available={"checkpoint_archive": True},
        preview_failures=["preview-render-timeout"],
    )
    assert committed["status"] == "committed"
    assert committed["previewFailures"] == ["preview-render-timeout"]
    assert (
        commit_saved_work_manifest(
            manifest, required_refs_available={"checkpoint_archive": False}
        )["status"]
        == "incomplete"
    )
    assert (
        commit_saved_work_manifest(
            {**manifest, "scan": {"disposition": "blocked"}},
            required_refs_available={"checkpoint_archive": True},
        )["status"]
        == "incomplete"
    )
    failed_required = build_saved_work_manifest(
        capture_id="capture-1",
        identity={"workflowId": "wf-1"},
        source={"kind": "managed-code-workspace"},
        content_digest="sha256:" + "a" * 64,
        file_manifest_digest="sha256:" + "b" * 64,
        capture_policy={},
        required_formats=["full_snapshot"],
        outputs=[describe_output_claim(fmt="full_snapshot", status="failed")],
    )
    assert (
        commit_saved_work_manifest(
            failed_required, required_refs_available={}
        )["status"]
        == "incomplete"
    )


def test_retry_identity_rejects_conflicting_reused_complete() -> None:
    assert_complete_payload_matches(
        stored_digest="sha256:aa", stored_size=3, new_digest="aa", new_size=3
    )
    with pytest.raises(ValueError, match="RETRY_CONFLICT"):
        assert_complete_payload_matches(
            stored_digest="sha256:aa", stored_size=3, new_digest="sha256:bb", new_size=3
        )
    with pytest.raises(ValueError, match="RETRY_CONFLICT"):
        assert_complete_payload_matches(
            stored_digest="sha256:aa", stored_size=3, new_digest="sha256:aa", new_size=4
        )
    verify_captured_artifact_evidence(
        expected_digest="sha256:aa",
        expected_size_bytes=3,
        artifact=SimpleNamespace(sha256="aa", size_bytes=3),
    )
    with pytest.raises(ValueError, match="NOT_COMPLETE"):
        verify_captured_artifact_evidence(
            expected_digest="sha256:aa",
            expected_size_bytes=3,
            artifact=SimpleNamespace(
                status=SimpleNamespace(value="PENDING"),
                sha256="aa",
                size_bytes=3,
            ),
        )


@pytest.mark.asyncio
async def test_capture_commits_saved_work_with_truthful_outputs(tmp_path) -> None:
    repo, activities, stored, digest = _capture_harness(
        tmp_path, {"tracked.txt": "checkpoint evidence\n"}
    )
    result = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(digest=digest)
    )
    assert result["status"] == "captured"
    assert result["savedWorkRef"].startswith("artifact://")
    assert result["savedWorkDigest"].startswith("sha256:")
    saved_payload, _, _ = stored["saved_work_manifest"]
    saved_work = json.loads(saved_payload.decode("utf-8"))
    assert saved_work["captureId"] == "saved-work-1:capture"
    assert saved_work["contentDigest"] == result["workspace"]["archiveDigest"]
    outputs = {output["format"]: output for output in saved_work["outputs"]}
    assert outputs["full_snapshot"]["status"] == "self_contained"
    assert outputs["exact_baseline_delta"]["status"] == "requires_dependencies"
    assert outputs["exact_baseline_delta"]["dependencies"] == [
        f"git-baseline:{result['workspace']['baseCommit']}"
    ]
    assert outputs["selected_history"]["status"] == "inapplicable"
    assert saved_work["scan"]["exportScan"]["exportDigest"] == (
        result["workspace"]["archiveDigest"]
    )
    assert "retentionPolicy" not in saved_work
    delta_payload, _, _ = stored["checkpoint_delta"]
    assert json.loads(delta_payload.decode("utf-8"))["baselineCommit"] == (
        result["workspace"]["baseCommit"]
    )


@pytest.mark.asyncio
async def test_capture_delta_reports_rename_mode_delete_against_baseline(tmp_path) -> None:
    repo, activities, stored, digest = _capture_harness(
        tmp_path, {"a.txt": "hello\n", "gone.txt": "gone\n", "run.sh": "#!/bin/sh\n"}
    )
    (repo / "a.txt").rename(repo / "renamed.txt")
    (repo / "gone.txt").unlink()
    (repo / "run.sh").chmod(0o755)
    (repo / "added.txt").write_text("new\n")
    _git(repo, "add", "-A")
    result = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(digest=digest)
    )
    assert result["status"] == "captured"
    delta_payload, _, _ = stored["checkpoint_delta"]
    changes = {
        change["path"]: change["change"]
        for change in json.loads(delta_payload.decode("utf-8"))["changes"]
    }
    assert changes["renamed.txt"] == "renamed"
    assert changes["gone.txt"] == "deleted"
    assert changes["run.sh"] in {"modified", "mode_changed"}
    assert changes["added.txt"] == "added"


@pytest.mark.asyncio
async def test_capture_records_exclusions_without_claiming_them(tmp_path) -> None:
    repo, activities, stored, digest = _capture_harness(
        tmp_path, {"kept.txt": "kept\n"}
    )
    (repo / ".env").write_text("token = nope\n")
    _git(repo, "add", "-A")
    result = await activities.agent_runtime_capture_workspace_checkpoint(
        _request(digest=digest)
    )
    assert result["status"] == "captured"
    saved_work = json.loads(stored["saved_work_manifest"][0].decode("utf-8"))
    excluded = {entry["path"] for entry in saved_work["exclusions"]}
    assert ".env" in excluded
    delta_payload, _, _ = stored["checkpoint_delta"]
    assert ".env" not in {
        change["path"]
        for change in json.loads(delta_payload.decode("utf-8"))["changes"]
    }


@pytest.mark.asyncio
async def test_capture_detects_mid_capture_head_mutation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, activities, _stored, digest = _capture_harness(
        tmp_path, {"tracked.txt": "evidence\n"}
    )
    calls = {"rev_parse": 0}
    original_run = activity_runtime_module._run_command

    async def flaky_run(command, **kwargs):
        result = await original_run(command, **kwargs)
        operation = [str(part) for part in command]
        if "rev-parse" in operation:
            calls["rev_parse"] += 1
            if calls["rev_parse"] > 1:
                return SimpleNamespace(stdout="mutated-head\n")
        return result

    monkeypatch.setattr(activity_runtime_module, "_run_command", flaky_run)
    with pytest.raises(Exception, match="CONCURRENT_MUTATION"):
        await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=digest)
        )


@pytest.mark.asyncio
async def test_capture_fails_explicitly_on_unreadable_files(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, activities, _stored, digest = _capture_harness(
        tmp_path, {"tracked.txt": "evidence\n"}
    )

    monkeypatch.setattr(
        activity_runtime_module, "_sha256_file", lambda path: (_ for _ in ()).throw(
            PermissionError("denied")
        ),
    )
    with pytest.raises(Exception, match="unreadable file during capture"):
        await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=digest)
        )


@pytest.mark.asyncio
async def test_capture_blocks_secret_bearing_exports(tmp_path) -> None:
    _repo, activities, _stored, digest = _capture_harness(
        tmp_path, {"notes.txt": "api_key = supersecretvalue123\n"}
    )
    with pytest.raises(Exception, match="secret scanning"):
        await activities.agent_runtime_capture_workspace_checkpoint(
            _request(digest=digest)
        )


@pytest.mark.asyncio
async def test_put_managed_artifact_verifies_reused_evidence(tmp_path) -> None:
    _repo, activities, _stored, _digest = _capture_harness(
        tmp_path, {"tracked.txt": "evidence\n"}
    )
    payload = b"capture candidate"

    class ReusingService:
        async def put_content_addressed_payload_complete(
            self, *, payload: bytes, **_kwargs: object
        ) -> tuple[object, bool]:
            return (
                SimpleNamespace(
                    artifact_id="art-other",
                    sha256=hashlib.sha256(b"different bytes").hexdigest(),
                    size_bytes=len(b"different bytes"),
                    status=SimpleNamespace(value="COMPLETE"),
                ),
                True,
            )

    activities._artifact_service = ReusingService()
    with pytest.raises(Exception, match="RETRY_CONFLICT"):
        await activities._put_managed_checkpoint_artifact(
            payload, "application/vnd.moonmind.worktree-archive", "checkpoint_archive"
        )


def test_capture_result_carries_saved_work_refs() -> None:
    digest = resolve_runtime_execution_capabilities("codex_cli").capability_digest
    model = ManagedWorkspaceCheckpointCaptureInput.model_validate(
        _request(digest=digest)
    )
    assert model.capture_policy.include_untracked is True
    result = ManagedWorkspaceCheckpointCaptureResult.model_validate(
        {
            "status": "captured",
            "sourceWorkspaceLocator": model.workspace_locator.model_dump(
                by_alias=True, mode="json"
            ),
            "diagnosticRefs": [],
            "idempotencyKey": model.idempotency_key,
            "savedWorkRef": "artifact://saved",
            "savedWorkDigest": "sha256:" + "c" * 64,
        }
    )
    assert result.saved_work_ref == "artifact://saved"
