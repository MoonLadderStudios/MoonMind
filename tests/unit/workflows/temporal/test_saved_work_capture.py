"""Unit coverage for saved-work capture contracts (issue #4015).

Covers CONTRACT-012 / QUALITY-004 at the contract/helper boundary:
manifest + format profile (reqs 1-2), quiescent generation (req 3),
deterministic/bounded identities and size limits (req 4), retry binding +
evidence verification including the COMPLETE-reuse guard (req 5),
binary-safe deltas + scoped history (req 6), export-bound scan evidence
(req 7), and saved-result commit verification (req 8).
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from moonmind.schemas.saved_work_models import (
    SavedWorkFormatProfile,
    SavedWorkManifest,
    saved_work_manifest_digest,
)
from moonmind.workflows.temporal import saved_work
from moonmind.workflows.temporal.saved_work import (
    FileState,
    SavedWorkError,
    bind_scan_evidence,
    build_retry_identity,
    capture_generation_fingerprint,
    check_path_case_collisions,
    check_retry_reuse,
    check_thin_bundle_completeness,
    compute_binary_safe_deltas,
    compute_file_manifest_digest,
    ensure_export_within_limits,
    is_byte_identical_restorable,
    is_excluded_runtime_credential,
    is_source_credential_independent,
    quarantine_decision,
    report_only_exempt,
    separate_preview_failure,
    spool_bytes_bounded,
    stream_sha256_of_file,
    validate_history_export_request,
    validate_safe_symlink,
    verify_quiescent_capture,
    verify_returned_evidence,
    verify_saved_result_commit,
)


def _manifest_kwargs(**overrides):
    scan = {
        "disposition": "unsupported",
        "policyRef": saved_work.OUTBOUND_SCAN_POLICY_REF,
        "exportDigest": "sha256:" + "0" * 64,
        "scannedBytes": 10,
        "unscannedBytes": 5,
        "limitations": ["binary/history bytes outside text inspection"],
    }
    base = {
        "schemaDigest": saved_work.SAVED_WORK_SCHEMA_DIGEST,
        "captureId": "cap-1",
        "identity": {
            "workflowId": "wf-1",
            "runId": "run-1",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
        },
        "sourceKind": "managed-workspace",
        "sourceIdentityDigest": "sha256:" + "1" * 64,
        "contentDigest": "sha256:" + "2" * 64,
        "fileManifestDigest": "sha256:" + "3" * 64,
        "capturePolicy": "managed-code-workspace",
        "capturePolicyVersion": "v1",
        "captureGeneration": "sha256:" + "4" * 64,
        "formats": {
            "formats": [
                {"kind": "full_snapshot", "state": "self-contained", "required": True}
            ]
        },
        "exclusions": [".env"],
        "scan": scan,
        "artifactDependencies": [],
        "ownerScope": "execution:run-1",
        "retentionClass": "standard",
    }
    base.update(overrides)
    return base


def test_saved_work_manifest_validates_and_digests_deterministically() -> None:
    first = SavedWorkManifest.model_validate(_manifest_kwargs())
    second = SavedWorkManifest.model_validate(_manifest_kwargs())
    assert first.capture_id == "cap-1"
    assert first.formats.is_savable()
    assert saved_work_manifest_digest(
        first.model_dump(by_alias=True, mode="json")
    ) == saved_work_manifest_digest(second.model_dump(by_alias=True, mode="json"))
    # ACL/retention resolve through ownership references, not copied policy.
    assert first.owner_scope == "execution:run-1"
    # Optional Git refs stay optional: report-only tasks never synthesize them.
    assert first.git is None


def test_format_profile_blocks_required_incomplete_and_thin_bundle_claim() -> None:
    profile = SavedWorkFormatProfile.model_validate(
        {
            "formats": [
                {
                    "kind": "selected_history",
                    "state": "requires-dependencies",
                    "required": True,
                    "dependencies": [],
                },
                {"kind": "report", "state": "inapplicable", "required": False},
            ]
        }
    )
    assert not profile.is_savable()
    assert profile.required_blockers() == ["selected_history"]
    entry = profile.entry_for("selected_history")
    assert entry is not None
    assert not is_source_credential_independent(entry)
    with pytest.raises(SavedWorkError, match="THIN_BUNDLE_INCOMPLETE"):
        check_thin_bundle_completeness(
            has_bundle_without_baseline=True, download_succeeded=True
        )
    assert report_only_exempt(
        ["full_snapshot", "selected_history", "report"],
        is_report_only=True,
        is_git=False,
    ) == ["full_snapshot", "report"]


def test_quiescence_generation_detects_mutation() -> None:
    pre = capture_generation_fingerprint(
        head_commit="abc", status_digest="sha256:status-1"
    )
    assert verify_quiescent_capture(pre_generation=pre, post_generation=pre) == pre
    post = capture_generation_fingerprint(
        head_commit="abc", status_digest="sha256:status-2"
    )
    with pytest.raises(SavedWorkError, match="WORKSPACE_MUTATED"):
        verify_quiescent_capture(pre_generation=pre, post_generation=post)


def test_deterministic_manifest_digest_and_bounded_spool(tmp_path) -> None:
    entries = [{"path": "b.txt", "sha256": "x"}, {"path": "a.txt", "sha256": "y"}]
    assert compute_file_manifest_digest(entries) == compute_file_manifest_digest(
        list(reversed(entries))
    )
    target = tmp_path / "payload.bin"
    target.write_bytes(b"\x00\xff" * 100)
    assert stream_sha256_of_file(target) == (
        "sha256:" + hashlib.sha256(b"\x00\xff" * 100).hexdigest()
    )
    digest, total = spool_bytes_bounded([b"ab", b"cd"], max_bytes=4)
    assert (digest, total) == ("sha256:" + hashlib.sha256(b"abcd").hexdigest(), 4)
    with pytest.raises(SavedWorkError, match="SIZE_EXCEEDED"):
        spool_bytes_bounded([b"abc", b"de"], max_bytes=4)
    with pytest.raises(SavedWorkError, match="SIZE_EXCEEDED"):
        ensure_export_within_limits(
            format_kind="baseline_delta",
            size_bytes=saved_work.SAVED_WORK_FORMAT_SIZE_LIMITS["baseline_delta"] + 1,
            direct_upload_max_bytes=10**12,
        )
    # A fixed lower-level direct-upload limit cannot be silently bypassed.
    with pytest.raises(SavedWorkError, match="DIRECT_UPLOAD_LIMIT"):
        ensure_export_within_limits(
            format_kind="full_snapshot", size_bytes=100, direct_upload_max_bytes=10
        )


def test_retry_binding_reuse_conflict_and_evidence_verification() -> None:
    identity = build_retry_identity(
        owner_scope="execution:run-1",
        capture_generation="sha256:gen",
        capture_policy="managed-code-workspace/v1",
        content_identity="sha256:content",
    )
    assert check_retry_reuse(
        retry_identity=identity,
        committed_identity=identity,
        candidate_identity=identity,
        committed=True,
    ) == "reuse"
    with pytest.raises(SavedWorkError, match="RETRY_CONFLICT"):
        check_retry_reuse(
            retry_identity=identity,
            committed_identity=identity,
            candidate_identity="sha256:other",
            committed=True,
        )
    verify_returned_evidence(
        expected_digest="sha256:abc",
        expected_size=3,
        expected_status="COMPLETE",
        returned_digest="sha256:abc",
        returned_size=3,
        returned_status="COMPLETE",
    )
    with pytest.raises(SavedWorkError, match="DIGEST_MISMATCH"):
        verify_returned_evidence(
            expected_digest="sha256:abc",
            expected_size=3,
            expected_status="COMPLETE",
            returned_digest="sha256:other",
            returned_size=3,
            returned_status="COMPLETE",
        )
    with pytest.raises(SavedWorkError, match="SIZE_MISMATCH"):
        verify_returned_evidence(
            expected_digest="sha256:abc",
            expected_size=3,
            expected_status="COMPLETE",
            returned_digest="sha256:abc",
            returned_size=4,
            returned_status="COMPLETE",
        )


def test_binary_safe_deltas_never_delete_excluded_paths() -> None:
    baseline = {
        "kept.txt": FileState(sha256="a", mode="000644", size=1),
        "changed.bin": FileState(sha256="b", mode="000644", size=2),
        "gone.txt": FileState(sha256="c", mode="000644", size=3),
        "old-name.txt": FileState(sha256="d", mode="000644", size=4),
        "script.sh": FileState(sha256="e", mode="000644", size=5),
        "secret.env": FileState(sha256="f", mode="000644", size=6),
    }
    captured = {
        "kept.txt": FileState(sha256="a", mode="000644", size=1),
        "changed.bin": FileState(sha256="b2", mode="000644", size=2),
        "new-name.txt": FileState(sha256="d", mode="000644", size=4),
        "script.sh": FileState(sha256="e", mode="000755", size=5),
        "added.txt": FileState(sha256="g", mode="000644", size=7),
    }
    deltas = compute_binary_safe_deltas(
        baseline=baseline, captured=captured, excluded={"secret.env"}
    )
    by_path = {(entry.path, entry.change): entry for entry in deltas}
    assert ("changed.bin", "modify") in by_path
    assert ("gone.txt", "delete") in by_path
    assert ("script.sh", "mode") in by_path
    assert ("added.txt", "add") in by_path
    renamed = by_path[("new-name.txt", "rename")]
    assert renamed.previous_path == "old-name.txt"
    # Excluded paths are absent, never inferred as deletions.
    assert not [entry for entry in deltas if entry.path == "secret.env"]
    with pytest.raises(SavedWorkError, match="PATH_COLLISION"):
        check_path_case_collisions(["README.md", "readme.md"])
    with pytest.raises(SavedWorkError, match="SYMLINK_ESCAPE"):
        validate_safe_symlink(
            link_path="evil", target="../outside", workspace="/tmp/ws"
        )


def test_history_export_request_rejects_all_and_hooks() -> None:
    assert validate_history_export_request(
        refs=["refs/heads/work"],
        baseline_commit="abc123",
    ) == ["refs/heads/work"]
    with pytest.raises(SavedWorkError, match="--all"):
        validate_history_export_request(
            refs=["--all"], baseline_commit="abc123", allow_all=True
        )
    with pytest.raises(SavedWorkError, match="NO_BASELINE"):
        validate_history_export_request(refs=["refs/heads/work"], baseline_commit=None)
    with pytest.raises(SavedWorkError, match="HOOKS_FORBIDDEN"):
        validate_history_export_request(
            refs=["refs/heads/work"], baseline_commit="abc123", run_hooks=True
        )


def test_export_scan_evidence_preview_and_quarantine() -> None:
    evidence = bind_scan_evidence(
        export_digest="sha256:" + "9" * 64,
        scanned_bytes=100,
        unscanned_bytes=50,
        blocked=False,
        limitations=["reachable Git history not inspected"],
    )
    # Unsupported binary/history inspection is explicit, not a clean scan.
    assert evidence.disposition == "unsupported"
    assert "binary/history bytes outside text inspection" in evidence.limitations
    assert not is_byte_identical_restorable(is_redacted_preview=True)
    assert is_byte_identical_restorable(is_redacted_preview=False)
    assert (
        quarantine_decision(blocked=True, has_recoverable_content=True)
        == "quarantine-restricted"
    )
    assert is_excluded_runtime_credential(".env")
    assert is_excluded_runtime_credential("managed_runs/job/repo/file.txt")
    assert not is_excluded_runtime_credential("src/main.py")


def test_saved_result_commit_requires_manifest_and_separates_preview() -> None:
    verify_saved_result_commit(
        format_states={"full_snapshot": "self-contained", "report": "inapplicable"},
        required_kinds=["full_snapshot"],
        manifest_committed=True,
        dependency_metadata_present=True,
    )
    # A successful upload without a committed usable manifest is incomplete.
    with pytest.raises(SavedWorkError, match="NO_MANIFEST"):
        verify_saved_result_commit(
            format_states={"full_snapshot": "self-contained"},
            required_kinds=["full_snapshot"],
            manifest_committed=False,
            dependency_metadata_present=True,
        )
    assert (
        separate_preview_failure(preview_failed=True, content_verified=True)
        == "content-saved-preview-failed"
    )
    assert separate_preview_failure(preview_failed=False, content_verified=True) == "ok"


@pytest.mark.asyncio
async def test_capture_records_generation_and_scan_disposition(tmp_path) -> None:
    """Reqs 3+7: committed manifest carries generation + export-bound scan."""
    import json
    import subprocess
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.schemas.managed_checkpoint_models import (
        ManagedWorkspaceCheckpointCaptureInput,
    )
    from moonmind.workflows.executions.runtime_capabilities import (
        resolve_runtime_execution_capabilities,
    )
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "kept.txt").write_text("kept\n")
    subprocess.run(["git", "add", "kept.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=now,
            finishedAt=now, workspacePath=str(repo),
        )
    )
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )
    stored: dict[str, bytes] = {}

    async def put(payload: bytes, _content_type: str, kind: str) -> str:
        ref = "artifact://" + hashlib.sha256(payload).hexdigest()
        stored[kind] = payload
        return ref

    activities._put_managed_checkpoint_artifact = put
    request = {
        "schemaVersion": "v1",
        "identity": {"workflowId": "wf-1", "runId": "run-1", "logicalStepId": "implement", "executionOrdinal": 1},
        "boundary": "after_execution",
        "checkpointKind": "worktree_archive",
        "workspaceLocator": {"kind": "managed_runtime", "runtimeId": "codex_cli", "agentRunId": "agent-run-1", "relativePath": "repo"},
        "expectedRuntimeId": "codex_cli",
        "capabilitySetVersion": "runtime-execution-capabilities-v1",
        "capabilityDigest": resolve_runtime_execution_capabilities("codex_cli").capability_digest,
        "artifactNamespace": "step-checkpoints/implement",
        "idempotencyKey": "saved-work-gen:capture",
        "capturePolicy": {"includeTracked": True, "includeUntracked": True, "includeIgnored": False, "redactionProfile": "managed-code-workspace-v1"},
    }
    result = await activities.agent_runtime_capture_workspace_checkpoint(request)
    assert result["status"] == "captured"
    manifest = json.loads(stored["checkpoint_manifest"].decode("utf-8"))
    assert manifest["captureGeneration"].startswith("sha256:")
    scan = manifest["savedWorkScan"]
    assert scan["exportDigest"] == manifest["archive"]["sha256"]
    assert scan["scannedBytes"] > 0
    assert scan["unscannedBytes"] == manifest["archive"]["size"]
    assert "reachable Git history not inspected" in scan["limitations"]


@pytest.mark.asyncio
async def test_capture_blocks_on_mutation_during_capture(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Req 3: HEAD changing mid-capture retries/blocks explicitly."""
    import subprocess
    from datetime import UTC, datetime

    from moonmind.schemas.agent_runtime_models import ManagedRunRecord
    from moonmind.workflows.executions.runtime_capabilities import (
        resolve_runtime_execution_capabilities,
    )
    from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore

    repo = tmp_path / "agent-run-1" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "kept.txt").write_text("kept\n")
    subprocess.run(["git", "add", "kept.txt"], cwd=repo, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=test", "-c",
            "user.email=test@example.invalid", "commit", "-qm", "base",
        ],
        cwd=repo,
        check=True,
    )
    now = datetime.now(UTC)
    store = ManagedRunStore(tmp_path / "managed_runs")
    store.save(
        ManagedRunRecord(
            runId="agent-run-1", workflowId="wf-1", agentId="codex_cli",
            ownerRunId="run-1", logicalStepId="implement", executionOrdinal=1,
            runtimeId="codex_cli", status="completed", startedAt=now,
            finishedAt=now, workspacePath=str(repo),
        )
    )
    real_run_command = activity_runtime_module._run_command
    rev_parse_calls = 0

    async def flipping_head(command, **kwargs):
        nonlocal rev_parse_calls
        result = await real_run_command(command, **kwargs)
        parts = [str(part) for part in command]
        if parts[-2:] == ["rev-parse", "HEAD"]:
            rev_parse_calls += 1
            if rev_parse_calls > 1:
                # Simulate a writer landing a commit mid-capture.
                return type(result)(b"b" * 40 + b"\n")
        return result

    monkeypatch.setattr(activity_runtime_module, "_run_command", flipping_head)
    activities = TemporalAgentRuntimeActivities(
        run_store=store, artifact_service=object(), client_adapter=object()
    )

    async def put(payload: bytes, _content_type: str, _kind: str) -> str:
        return "artifact://" + hashlib.sha256(payload).hexdigest()

    activities._put_managed_checkpoint_artifact = put
    request = {
        "schemaVersion": "v1",
        "identity": {"workflowId": "wf-1", "runId": "run-1", "logicalStepId": "implement", "executionOrdinal": 1},
        "boundary": "after_execution",
        "checkpointKind": "worktree_archive",
        "workspaceLocator": {"kind": "managed_runtime", "runtimeId": "codex_cli", "agentRunId": "agent-run-1", "relativePath": "repo"},
        "expectedRuntimeId": "codex_cli",
        "capabilitySetVersion": "runtime-execution-capabilities-v1",
        "capabilityDigest": resolve_runtime_execution_capabilities("codex_cli").capability_digest,
        "artifactNamespace": "step-checkpoints/implement",
        "idempotencyKey": "saved-work-mutated:capture",
        "capturePolicy": {"includeTracked": True, "includeUntracked": True, "includeIgnored": False, "redactionProfile": "managed-code-workspace-v1"},
    }
    with pytest.raises(Exception) as exc_info:
        await activities.agent_runtime_capture_workspace_checkpoint(request)
    assert getattr(exc_info.value, "type", "") == "CHECKPOINT_CAPTURE_MUTATED"


def test_complete_reuse_guard_rejects_conflicting_candidate_bytes() -> None:
    """Req 5: a reused COMPLETE artifact must match the supplied payload."""
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactService,
        TemporalArtifactValidationError,
    )

    service = TemporalArtifactService.__new__(TemporalArtifactService)
    stored = b"stored-bytes"
    stored_digest = hashlib.sha256(stored).hexdigest()
    artifact = SimpleNamespace(sha256=stored_digest, size_bytes=len(stored))
    # Same bytes reuse cleanly.
    service._validate_complete_reuse_matches_candidate(
        artifact, digest=stored_digest, size_bytes=len(stored)
    )
    # Conflicting bytes must not be associated with the claimed manifest.
    with pytest.raises(TemporalArtifactValidationError, match="different bytes"):
        service._validate_complete_reuse_matches_candidate(
            artifact,
            digest=hashlib.sha256(b"conflicting-bytes").hexdigest(),
            size_bytes=len(b"conflicting-bytes"),
        )
