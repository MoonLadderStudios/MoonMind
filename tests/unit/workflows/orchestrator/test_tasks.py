"""Unit tests for orchestrator Celery task helpers."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from moonmind.workflows.orchestrator import tasks


def test_build_storage_for_run_uses_persisted_directory(tmp_path):
    """When a run already defines ``artifact_root`` reuse its parent."""

    run_id = uuid4()
    base = tmp_path / "artifacts"
    stored_path = base / str(run_id)
    run = SimpleNamespace(id=run_id, artifact_root=str(stored_path))

    storage = tasks._build_storage_for_run(run)

    assert storage.base_path == base.resolve()
    assert run.artifact_root == str((base / str(run_id)).resolve())
    assert (base / str(run_id)).exists()


def test_build_storage_for_run_generates_directory_when_missing(tmp_path, monkeypatch):
    """Runs without an artifact root should default to the configured base path."""

    run_id = uuid4()
    base = tmp_path / "generated"
    run = SimpleNamespace(id=run_id, artifact_root=None)

    monkeypatch.setattr(tasks, "_artifact_root", lambda: base)

    storage = tasks._build_storage_for_run(run)

    assert storage.base_path == base.resolve()
    expected = base / str(run_id)
    assert run.artifact_root == str(expected.resolve())
    assert expected.exists()
