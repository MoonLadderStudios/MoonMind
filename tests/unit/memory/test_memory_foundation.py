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


def test_fix_pattern_upserts_are_scoped_by_namespace_and_repo():
    provenance = MemoryProvenance(workflow_id="wf-mm-761")
    signature = TaskHistoryService().extract_error_signature(
        "ValueError in /tmp/run/task.py at line 42",
        evidence=provenance,
        family="python",
    )
    store = InMemoryTaskHistoryStore()

    for namespace_id, repo, summary in (
        ("default", "moonmind/repo", "first repo fix"),
        ("other", "moonmind/repo", "other namespace fix"),
        ("default", "moonmind/other", "other repo fix"),
    ):
        store.upsert_fix_pattern(
            FixPattern(
                namespace_id=namespace_id,
                repo=repo,
                signature=signature,
                summary=summary,
                provenance=provenance,
            )
        )

    assert {pattern.summary for pattern in store.fix_patterns} == {
        "first repo fix",
        "other namespace fix",
        "other repo fix",
    }


def test_error_signature_collapses_paths_before_volatile_ids():
    provenance = MemoryProvenance(workflow_id="wf-mm-761")
    signature = TaskHistoryService().extract_error_signature(
        "Traceback in /tmp/run/123e4567-e89b-12d3-a456-426614174000/out.py line 99",
        evidence=provenance,
        family="python",
    )

    assert "<path>" in signature.value
    assert "<path>/<uuid>" not in signature.value


def test_retrieval_gateway_combines_planning_and_history_without_long_term():
    """#4109: hosted Mem0 is retired; the gateway assembles planning+history."""

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
            context_budget_tokens=500,
        ),
        planning=planning,
        history=history_service.store,
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
    }
    assert all(candidate.provenance for candidate in pack.included)


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

    missing_fail_open = RetrievalGateway(
        settings=MemorySettings(enabled=True, planning="beads", fail_open=True),
    )
    missing_pack = missing_fail_open.retrieve_context_pack(
        "anything",
        namespace_id="default",
        repo="moonmind/repo",
        planning_ref="beads:MM-761",
    )

    assert missing_pack.included == []
    assert missing_pack.degraded_components == ["planning"]

    missing_fail_closed = RetrievalGateway(
        settings=MemorySettings(enabled=True, planning="beads", fail_open=False),
    )
    with pytest.raises(AttributeError):
        missing_fail_closed.retrieve_context_pack(
            "anything",
            namespace_id="default",
            repo="moonmind/repo",
            planning_ref="beads:MM-761",
        )


def test_in_memory_long_term_memory_injects_only_approved_scoped_memories():
    """#4109: the retained exact-reference store filters review state and scope."""

    service = InMemoryLongTermMemoryService()
    provenance = MemoryProvenance(workflow_id="wf-mm-761")
    service.add_or_update(
        LongTermMemory(
            namespace_id="default",
            repo="moonmind/repo",
            text="Only approved memories are injected by default.",
            review_state="approved",
            provenance=provenance,
        )
    )
    service.add_or_update(
        LongTermMemory(
            namespace_id="default",
            repo="moonmind/repo",
            text="Draft memories are not injected by default.",
            review_state="draft",
            provenance=provenance,
        )
    )
    service.add_or_update(
        LongTermMemory(
            namespace_id="other",
            repo="moonmind/repo",
            text="Other namespaces stay out of scope.",
            review_state="approved",
            provenance=provenance,
        )
    )

    results = service.search(
        "injected memories", namespace_id="default", repo="moonmind/repo"
    )

    assert [memory.text for memory in results] == [
        "Only approved memories are injected by default."
    ]
    assert results[0].provenance.workflow_id == "wf-mm-761"


def test_hosted_mem0_service_is_retired():
    """#4109: the Mem0 adapter has no import path and no gateway consumer."""

    import pytest as pytest_module

    from moonmind.memory import services as services_module

    assert not hasattr(services_module, "Mem0LongTermMemoryService")
    with pytest_module.raises(ImportError):
        from moonmind.memory.services import (  # noqa: F401
            Mem0LongTermMemoryService,
        )
