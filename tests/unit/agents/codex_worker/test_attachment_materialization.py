"""Tests for prepare-time task input attachment materialization."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from moonmind.agents.codex_worker.handlers import WorkerExecutionResult
from moonmind.agents.codex_worker.worker import (
    CodexWorker,
    CodexWorkerConfig,
    QueueSystemStatus,
)


class _FakeQueueClient:
    def __init__(self, downloads: dict[str, bytes] | None = None) -> None:
        self.downloads = dict(downloads or {})
        self.events: list[dict[str, object]] = []
        self.live_session_reports: list[dict[str, object]] = []
        self.live_session_state: dict[str, object] | None = None
        self.live_session_heartbeats: list[str] = []

    async def download_artifact(self, *, artifact_id: str) -> bytes:
        try:
            return self.downloads[artifact_id]
        except KeyError as exc:
            raise RuntimeError(f"missing artifact: {artifact_id}") from exc

    async def append_event(self, *, job_id, worker_id, level, message, payload=None):
        self.events.append(
            {
                "job_id": str(job_id),
                "worker_id": worker_id,
                "level": level,
                "message": message,
                "payload": payload or {},
            }
        )

    async def report_live_session(self, **payload):
        self.live_session_reports.append(dict(payload))
        status = str(payload.get("status") or "").strip().lower()
        if status:
            self.live_session_state = {"session": {"status": status}}
        return self.live_session_state or {}

    async def heartbeat_live_session(self, *, job_id, worker_id):
        self.live_session_heartbeats.append(str(job_id))
        return self.live_session_state or {}


class _FakeHandler:
    async def handle(
        self, *, job_id, payload, cancel_event=None, output_chunk_callback=None
    ):
        return WorkerExecutionResult(succeeded=True, summary="ok", error_message=None)


def _worker(tmp_path: Path, queue: _FakeQueueClient | None = None) -> CodexWorker:
    return CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:5000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=100,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue or _FakeQueueClient(),  # type: ignore[arg-type]
        codex_exec_handler=_FakeHandler(),  # type: ignore[arg-type]
    )


def _canonical_payload() -> dict[str, object]:
    return {
        "repository": "https://example.test/repo.git",
        "targetRuntime": "codex",
        "task": {
            "inputAttachments": [
                {
                    "artifactId": "art_objective",
                    "filename": "../Objective Diagram.png",
                    "contentType": "image/png",
                    "sizeBytes": 3,
                }
            ],
            "steps": [
                {
                    "id": "review-step",
                    "inputAttachments": [
                        {
                            "artifactId": "art_step",
                            "filename": "screen/shot.png",
                            "contentType": "image/png",
                            "sizeBytes": 4,
                        }
                    ],
                },
                {
                    "instructions": "No id",
                    "inputAttachments": [
                        {
                            "artifactId": "art_no_id",
                            "filename": "same.png",
                            "contentType": "image/png",
                            "sizeBytes": 5,
                        }
                    ],
                },
            ],
        },
    }


def test_collect_attachment_targets_preserves_canonical_target_fields(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)

    targets = worker._collect_input_attachment_targets(_canonical_payload())

    assert [target.target_kind for target in targets] == ["objective", "step", "step"]
    assert targets[0].artifact_id == "art_objective"
    assert targets[0].step_ref is None
    assert targets[1].step_ref == "review-step"
    assert targets[1].step_ordinal == 0
    assert targets[2].step_ref == "step-2"
    assert targets[2].step_ordinal == 1


def test_attachment_workspace_paths_are_target_aware_and_sanitized(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    targets = worker._collect_input_attachment_targets(_canonical_payload())

    paths = [
        worker._attachment_workspace_relative_path(target).as_posix()
        for target in targets
    ]

    assert paths == [
        ".moonmind/inputs/objective/art_objective-Objective_Diagram.png",
        ".moonmind/inputs/steps/review-step/art_step-shot.png",
        ".moonmind/inputs/steps/step-2/art_no_id-same.png",
    ]


def test_attachment_workspace_paths_do_not_depend_on_unrelated_target_order(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    original = _canonical_payload()
    reordered = _canonical_payload()
    task = reordered["task"]
    assert isinstance(task, dict)
    task["inputAttachments"] = [
        {
            "artifactId": "art_other",
            "filename": "other.png",
            "contentType": "image/png",
            "sizeBytes": 1,
        },
        *task["inputAttachments"],
    ]

    original_paths = {
        target.artifact_id: worker._attachment_workspace_relative_path(
            target
        ).as_posix()
        for target in worker._collect_input_attachment_targets(original)
    }
    reordered_paths = {
        target.artifact_id: worker._attachment_workspace_relative_path(
            target
        ).as_posix()
        for target in worker._collect_input_attachment_targets(reordered)
    }

    assert reordered_paths["art_objective"] == original_paths["art_objective"]
    assert reordered_paths["art_step"] == original_paths["art_step"]
    assert reordered_paths["art_no_id"] == original_paths["art_no_id"]


def test_collect_attachment_targets_rejects_malformed_refs(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    payload = _canonical_payload()
    task = payload["task"]
    assert isinstance(task, dict)
    task["inputAttachments"] = [{"artifactId": "art_missing_filename"}]

    with pytest.raises(ValueError, match="filename is required"):
        worker._collect_input_attachment_targets(payload)


@pytest.mark.asyncio
async def test_materialize_input_attachments_writes_files_and_manifest(
    tmp_path: Path,
) -> None:
    queue = _FakeQueueClient(
        {
            "art_objective": b"one",
            "art_step": b"step",
            "art_no_id": b"no-id",
        }
    )
    worker = _worker(tmp_path, queue)
    repo_dir = tmp_path / "job" / "repo"
    repo_dir.mkdir(parents=True)

    manifest_path = await worker._materialize_input_attachments(
        job_id=uuid4(),
        canonical_payload=_canonical_payload(),
        repo_dir=repo_dir,
        prepare_log_path=tmp_path / "prepare.log",
    )

    assert manifest_path == repo_dir / ".moonmind" / "attachments_manifest.json"
    assert (
        repo_dir / ".moonmind/inputs/objective/art_objective-Objective_Diagram.png"
    ).read_bytes() == b"one"
    assert (
        repo_dir / ".moonmind/inputs/steps/review-step/art_step-shot.png"
    ).read_bytes() == b"step"
    assert (
        repo_dir / ".moonmind/inputs/steps/step-2/art_no_id-same.png"
    ).read_bytes() == b"no-id"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["attachments"] == [
        {
            "artifactId": "art_objective",
            "filename": "../Objective Diagram.png",
            "contentType": "image/png",
            "sizeBytes": 3,
            "targetKind": "objective",
            "workspacePath": (
                ".moonmind/inputs/objective/"
                "art_objective-Objective_Diagram.png"
            ),
        },
        {
            "artifactId": "art_step",
            "filename": "screen/shot.png",
            "contentType": "image/png",
            "sizeBytes": 4,
            "targetKind": "step",
            "stepRef": "review-step",
            "stepOrdinal": 0,
            "workspacePath": ".moonmind/inputs/steps/review-step/art_step-shot.png",
        },
        {
            "artifactId": "art_no_id",
            "filename": "same.png",
            "contentType": "image/png",
            "sizeBytes": 5,
            "targetKind": "step",
            "stepRef": "step-2",
            "stepOrdinal": 1,
            "workspacePath": ".moonmind/inputs/steps/step-2/art_no_id-same.png",
        },
    ]


@pytest.mark.asyncio
async def test_materialize_input_attachments_fails_on_download_error(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path, _FakeQueueClient({}))
    repo_dir = tmp_path / "job" / "repo"
    repo_dir.mkdir(parents=True)

    with pytest.raises(
        RuntimeError, match="failed to materialize input attachment art_objective"
    ):
        await worker._materialize_input_attachments(
            job_id=uuid4(),
            canonical_payload=_canonical_payload(),
            repo_dir=repo_dir,
            prepare_log_path=tmp_path / "prepare.log",
        )


@pytest.mark.asyncio
async def test_prepare_stage_materializes_attachments_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _FakeQueueClient(
        {
            "art_objective": b"one",
            "art_step": b"step",
            "art_no_id": b"no-id",
        }
    )
    worker = _worker(tmp_path, queue)

    async def _noop_git_identity(**kwargs):
        return None

    monkeypatch.setattr(
        worker, "_run_prepare_git_identity_preflight", _noop_git_identity
    )

    prepared = await worker._run_prepare_stage(
        job_id=uuid4(),
        canonical_payload=_canonical_payload(),
        source_payload={"workdirMode": "existing"},
        selected_skills=[],
        job_type="task",
        skill_meta={},
    )

    manifest_path = prepared.repo_dir / ".moonmind" / "attachments_manifest.json"
    assert manifest_path.exists()
    assert (
        prepared.repo_dir
        / ".moonmind/inputs/objective/art_objective-Objective_Diagram.png"
    ).exists()
    task_context = json.loads(prepared.task_context_path.read_text(encoding="utf-8"))
    assert task_context["attachments"]["manifestPath"] == str(manifest_path)
    assert task_context["attachments"]["count"] == 3
