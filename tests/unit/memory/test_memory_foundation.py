import pytest

from moonmind.config.settings import MemorySettings
from moonmind.memory.models import (
    ContextPackBudget,
    FixPattern,
    LongTermMemory,
    MemoryProvenance,
    RunRef,
)
from moonmind.memory.services import (
    InMemoryLongTermMemoryService,
    InMemoryPlanningAdapter,
    InMemoryTaskHistoryStore,
    Mem0LongTermMemoryService,
    RetrievalGateway,
    TaskHistoryService,
    planning_candidate,
)


def test_task_history_builds_run_digest_and_fix_pattern_with_provenance():
    service = TaskHistoryService()
    provenance = MemoryProvenance(
        workflow_id="wf-mm-761",
        artifact_refs=["artifact://logs/test-output"],
        commits=["abc1234"],
    )
    run_ref = RunRef(kind="workflow", id="wf-mm-761")
    digest = service.build_run_digest(
        namespace_id="default",
        repo="moonmind/repo",
        run_ref=run_ref,
        intent="implement memory run digests",
        outcome="succeeded",
        provenance=provenance,
        key_changes=["added task history summaries"],
        decisions=["store only compact derived memory"],
        gotchas=["raw logs stay in artifacts"],
        next_steps=["index the digest"],
    )
    signature = service.extract_error_signature(
        "ValueError in /tmp/run/abc.py at line 391 for id 123e4567-e89b-12d3-a456-426614174000",
        evidence=provenance,
        family="python",
    )
    pattern = FixPattern(
        namespace_id="default",
        repo="moonmind/repo",
        signature=signature,
        summary="Validate the incoming payload before activity dispatch.",
        successful_run_refs=[run_ref],
        provenance=provenance,
    )

    service.upsert_digest_and_fix_patterns(digest, [pattern])

    assert service.store.digests == [digest]
    assert service.store.fix_patterns == [pattern]
    assert "<uuid>" in signature.value
    assert "<num>" in signature.value
    assert "/tmp/run" not in signature.value
    assert digest.as_candidate().provenance.workflow_id == "wf-mm-761"


def test_retrieval_gateway_combines_planning_history_and_approved_long_term_memory():
    provenance = MemoryProvenance(workflow_id="wf-mm-761")
    history_service = TaskHistoryService()
    history_service.upsert_digest_and_fix_patterns(
        history_service.build_run_digest(
            namespace_id="default",
            repo="moonmind/repo",
            run_ref=RunRef(id="wf-mm-761"),
            intent="fix repeated Temporal timeout",
            outcome="succeeded",
            provenance=provenance,
            gotchas=["Temporal retries can hide deterministic failures"],
        )
    )
    long_term = InMemoryLongTermMemoryService()
    long_term.add_or_update(
        LongTermMemory(
            namespace_id="default",
            repo="moonmind/repo",
            text="Prefer workflow-boundary tests for Temporal contract changes.",
            review_state="approved",
            provenance=provenance,
        )
    )
    long_term.add_or_update(
        LongTermMemory(
            namespace_id="default",
            repo="moonmind/repo",
            text="Draft memories are not injected by default.",
            review_state="draft",
            provenance=provenance,
        )
    )
    planning = InMemoryPlanningAdapter(
        items={
            "beads:MM-761": [
                planning_candidate(
                    "MM-761 is blocked by no remaining Beads dependency.",
                    source_ref="beads:MM-761",
                )
            ]
        }
    )
    gateway = RetrievalGateway(
        settings=MemorySettings(
            enabled=True,
            planning="beads",
            history="digest",
            long_term="mem0",
            context_budget_tokens=500,
        ),
        planning=planning,
        history=history_service.store,
        long_term=long_term,
    )

    pack = gateway.retrieve_context_pack(
        "Temporal timeout memory",
        namespace_id="default",
        repo="moonmind/repo",
        planning_ref="beads:MM-761",
    )

    assert {candidate.source for candidate in pack.included} == {
        "planning",
        "history",
        "long_term",
    }
    assert all(candidate.provenance for candidate in pack.included)
    assert "Draft memories are not injected" not in "\n".join(
        candidate.text for candidate in pack.included
    )


def test_retrieval_gateway_enforces_token_budget_and_records_skipped_items():
    provenance = MemoryProvenance(workflow_id="wf-mm-761")
    store = InMemoryTaskHistoryStore()
    for index in range(3):
        store.upsert_digest(
            TaskHistoryService().build_run_digest(
                namespace_id="default",
                repo="moonmind/repo",
                run_ref=RunRef(id=f"wf-{index}"),
                intent="memory budget " + ("x" * 80),
                outcome="succeeded",
                provenance=provenance,
            )
        )
    gateway = RetrievalGateway(
        settings=MemorySettings(history="digest", context_budget_tokens=35),
        history=store,
    )

    pack = gateway.retrieve_context_pack(
        "memory",
        namespace_id="default",
        repo="moonmind/repo",
        budget=ContextPackBudget(max_tokens=35),
    )

    assert pack.token_cost <= 35
    assert pack.included
    assert pack.skipped


def test_memory_feature_flags_disable_or_fail_open_components():
    class BrokenPlanning:
        def prefetch(self, planning_ref):
            raise RuntimeError("planning unavailable")

    disabled = RetrievalGateway(settings=MemorySettings(enabled=False))
    disabled_pack = disabled.retrieve_context_pack(
        "anything",
        namespace_id="default",
        repo="moonmind/repo",
    )

    assert disabled_pack.included == []
    assert disabled_pack.degraded_components == ["memory_disabled"]

    fail_open = RetrievalGateway(
        settings=MemorySettings(enabled=True, planning="beads", fail_open=True),
        planning=BrokenPlanning(),
    )
    pack = fail_open.retrieve_context_pack(
        "anything",
        namespace_id="default",
        repo="moonmind/repo",
        planning_ref="beads:MM-761",
    )

    assert pack.included == []
    assert pack.degraded_components == ["planning"]

    fail_closed = RetrievalGateway(
        settings=MemorySettings(enabled=True, planning="beads", fail_open=False),
        planning=BrokenPlanning(),
    )
    with pytest.raises(RuntimeError, match="planning unavailable"):
        fail_closed.retrieve_context_pack(
            "anything",
            namespace_id="default",
            repo="moonmind/repo",
            planning_ref="beads:MM-761",
        )


def test_mem0_adapter_preserves_review_state_and_provenance_metadata():
    class FakeMem0Client:
        def __init__(self):
            self.added = []

        def add(self, text, *, metadata):
            self.added.append((text, metadata))

        def search(self, query, *, metadata):
            assert metadata["review_state"] == "approved"
            return [
                {
                    "memory": "Use compact memories with evidence links.",
                    "metadata": {
                        "namespace_id": "default",
                        "repo": "moonmind/repo",
                        "review_state": "approved",
                        "workflow_id": "wf-mm-761",
                    },
                }
            ]

    client = FakeMem0Client()
    service = Mem0LongTermMemoryService(client)
    memory = LongTermMemory(
        namespace_id="default",
        repo="moonmind/repo",
        text="Only approved Mem0 memories are injected by default.",
        review_state="approved",
        provenance=MemoryProvenance(workflow_id="wf-mm-761"),
    )

    service.add_or_update(memory)
    results = service.search("compact evidence", namespace_id="default", repo="moonmind/repo")

    assert client.added[0][1]["workflow_id"] == "wf-mm-761"
    assert results[0].review_state == "approved"
    assert results[0].provenance.workflow_id == "wf-mm-761"
