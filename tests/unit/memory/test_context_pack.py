from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.memory.context_pack import build_memory_context_pack


def _candidate(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "text": "Use workflow evidence before proposing a retry.",
        "source": "run-digest://workflow-1/run-1",
        "plane": "history",
        "trustClass": "derived",
        "provenance": {
            "workflowId": "workflow-1",
            "runId": "run-1",
            "artifactRefs": ["artifact://digest-1"],
        },
        "recency": "2026-06-01T12:00:00Z",
        "tokenCost": 12,
        "score": 0.94,
    }
    payload.update(overrides)
    return payload


def test_memory_context_pack_requires_provenance() -> None:
    with pytest.raises(ValidationError, match="provenance pointers"):
        build_memory_context_pack([
            _candidate(provenance={}),
        ])


def test_memory_context_pack_normalizes_items_and_enforces_token_budget() -> None:
    pack = build_memory_context_pack(
        [
            _candidate(source="run-digest://one", tokenCost=8),
            _candidate(source="mem0://approved-playbook", plane="long_term", tokenCost=6),
            _candidate(source="docs://memory-architecture", plane="document", tokenCost=7),
        ],
        token_budget=14,
    )

    assert [item.source for item in pack.items] == [
        "run-digest://one",
        "mem0://approved-playbook",
    ]
    assert pack.budgets == {"tokens": 14}
    assert pack.usage == {"tokens": 14, "acceptedItems": 2, "skippedItems": 1}
    assert len(pack.skipped_refs) == 1
    assert pack.items[0].provenance["workflowId"] == "workflow-1"
    assert pack.items[0].token_cost == 8
    assert pack.items[0].item_ref.startswith("memory-context-item://sha256:")
    assert pack.memory_context_ref.startswith("memory-context-pack://sha256:")


def test_memory_context_pack_ref_is_deterministic_for_stable_inputs() -> None:
    first = build_memory_context_pack([_candidate()], token_budget=64)
    second = build_memory_context_pack([_candidate()], token_budget=64)
    changed_budget = build_memory_context_pack([_candidate()], token_budget=65)

    assert first.memory_context_ref == second.memory_context_ref
    assert first.memory_context_ref != changed_budget.memory_context_ref


def test_memory_context_pack_rejects_secretish_values() -> None:
    with pytest.raises(ValueError, match="raw secret material"):
        build_memory_context_pack([
            _candidate(text="never store ghp_unsafe in memory"),
        ])
