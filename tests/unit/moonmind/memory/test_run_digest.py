from __future__ import annotations

from types import SimpleNamespace

from moonmind.memory.run_digest import RUN_DIGEST_RECORD_KIND, TaskHistoryService


class _Embedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.1, 0.2, 0.3]


class _VectorIndex:
    collection = "moonmind-main"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def upsert_memory_vectors(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def _record(**overrides: object) -> SimpleNamespace:
    defaults = {
        "workflow_id": "mm:run:123",
        "run_id": "temporal-run-1",
        "namespace": "default",
        "workflow_type": "MoonMind.Run",
        "state": "completed",
        "close_status": "completed",
        "title": "Implement MM-762 run digests",
        "memo": {
            "summary": "Workflow completed successfully",
            "summary_artifact_ref": "art_summary",
        },
        "parameters": {
            "task": {"git": {"repository": "MoonLadderStudios/MoonMind"}},
            "publishMode": "pr",
        },
        "search_attributes": {"mm_task_run_id": "task-run-1"},
        "artifact_refs": ["art_summary", "art_patch"],
        "input_ref": "art_input",
        "plan_ref": "art_plan",
        "manifest_ref": "art_manifest",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_run_digest_uses_terminal_execution_evidence_without_raw_logs() -> None:
    service = TaskHistoryService(
        qdrant_client=_VectorIndex(),
        embedding_provider=_Embedder(),
    )

    digest = service.build_run_digest(_record())

    assert digest.record_kind == RUN_DIGEST_RECORD_KIND
    assert digest.intent == "Implement MM-762 run digests"
    assert digest.outcome == "Workflow completed successfully"
    assert digest.repo == "MoonLadderStudios/MoonMind"
    assert digest.security_scope == "repo:MoonLadderStudios/MoonMind"
    assert digest.evidence.workflow_id == "mm:run:123"
    assert digest.evidence.task_run_id == "task-run-1"
    assert digest.evidence.summary_artifact_ref == "art_summary"
    assert digest.evidence.artifact_refs == ("art_summary", "art_patch")
    assert "raw log" not in digest.to_context_text().lower()


def test_payload_for_digest_marks_retrieval_record_kind_and_provenance() -> None:
    service = TaskHistoryService(
        qdrant_client=_VectorIndex(),
        embedding_provider=_Embedder(),
    )
    digest = service.build_run_digest(_record())

    payload = service.payload_for_digest(digest)

    assert payload["record_kind"] == "run_digest"
    assert payload["source"] == "run_digest:mm:run:123"
    assert payload["trust_class"] == "derived"
    assert payload["run_ref.kind"] == "workflow"
    assert payload["run_ref.id"] == "mm:run:123"
    assert payload["taskRunId"] == "task-run-1"
    assert payload["workflow_id"] == "mm:run:123"
    assert payload["run_id"] == "temporal-run-1"
    assert "Workflow completed successfully" in payload["text"]


def test_upsert_run_digest_embeds_text_and_upserts_deterministic_payload() -> None:
    embedder = _Embedder()
    index = _VectorIndex()
    service = TaskHistoryService(qdrant_client=index, embedding_provider=embedder)
    digest = service.build_run_digest(_record())

    result = service.upsert_run_digest(digest)

    assert result == {
        "recordKind": "run_digest",
        "workflowId": "mm:run:123",
        "runId": "temporal-run-1",
        "source": "run_digest:mm:run:123",
        "collection": "moonmind-main",
    }
    assert embedder.calls == [index.calls[0]["payloads"][0]["text"]]
    assert index.calls[0]["vectors"] == [[0.1, 0.2, 0.3]]
    assert index.calls[0]["payloads"][0]["record_kind"] == "run_digest"
    assert index.calls[0]["ids"][0]
