"""Unit tests for orchestrator Celery task helpers."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from api_service.db import models as db_models

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


def test_enqueue_action_plan_links_rollback_for_all_steps(monkeypatch):
    """Each orchestrator step should trigger rollback on failure."""

    run_id = uuid4()

    class StubSignature:
        def __init__(self, step_name: str):
            self.step_name = step_name
            self.error_links: list[StubSignature] = []
            self.options: dict[str, object] = {}

        def link_error(self, signature: "StubSignature") -> "StubSignature":
            self.error_links.append(signature)
            return self

        def clone(self) -> "StubSignature":
            clone = StubSignature(self.step_name)
            clone.options = dict(self.options)
            return clone

        def set(self, **kwargs: object) -> "StubSignature":
            self.options.update(kwargs)
            return self

    def stub_signature_factory(run_id_str: str, step_name: str) -> StubSignature:
        signature = StubSignature(step_name)
        return signature

    monkeypatch.setattr(tasks.execute_plan_step, "si", stub_signature_factory)

    captured: dict[str, object] = {}

    class StubWorkflow:
        def apply_async(self, **kwargs: object) -> str:
            captured["apply_async_kwargs"] = kwargs
            return "sent"

    def stub_chain(*args: object) -> StubWorkflow:
        captured["chain_args"] = args
        return StubWorkflow()

    monkeypatch.setattr(tasks, "chain", stub_chain)

    result = tasks.enqueue_action_plan(
        run_id,
        [
            db_models.OrchestratorPlanStep.ANALYZE,
            db_models.OrchestratorPlanStep.PATCH,
            db_models.OrchestratorPlanStep.ROLLBACK,
        ],
        include_rollback=True,
    )

    assert result == "sent"
    chain_args = captured["chain_args"]
    assert len(chain_args) == 2
    for signature in chain_args:
        assert signature.error_links, "rollback should be linked to every step"
        for linked in signature.error_links:
            assert linked.options.get("queue") == tasks._DEFAULT_QUEUE
    kwargs = captured["apply_async_kwargs"]
    assert kwargs["queue"] == tasks._DEFAULT_QUEUE
