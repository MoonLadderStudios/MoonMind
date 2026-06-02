from __future__ import annotations

import pytest

from moonmind.memory.procedural import (
    EvidenceRun,
    FileFixPatternStore,
    FixPattern,
    extract_error_signature,
    fix_patterns_to_memory_proposals,
)


def test_error_signature_normalizes_dynamic_log_details() -> None:
    first = extract_error_signature(
        "Traceback (most recent call last):\n"
        '  File "/work/agent_jobs/run-a/repo/app.py", line 42, in <module>\n'
        "RuntimeError: provider returned 500 after request 123456"
    )
    second = extract_error_signature(
        "Traceback (most recent call last):\n"
        '  File "/tmp/other/repo/app.py", line 77, in <module>\n'
        "RuntimeError: provider returned 500 after request 999999"
    )

    assert first is not None
    assert second is not None
    assert first.normalized_text == (
        "runtimeerror: provider returned 500 after request <n>"
    )
    assert first.signature_id == second.signature_id


def test_extract_error_signature_ignores_non_error_text() -> None:
    assert extract_error_signature("ordinary progress log\nall good") is None


def test_file_fix_pattern_store_retains_compact_evidence(tmp_path) -> None:
    signature = extract_error_signature(
        "ModuleNotFoundError: No module named 'qdrant_client'",
        source_ref="artifact://logs/failure",
    )
    assert signature is not None
    pattern = FixPattern.from_successful_run(
        signature=signature,
        summary="Install missing Python dependency before rerunning.",
        steps=[
            "Add qdrant-client to the project dependencies.",
            "Run the focused import test.",
        ],
        evidence=EvidenceRun(
            workflowId="workflow-1",
            artifactRefs=["artifact://logs/failure", "artifact://patch/fix"],
            outcome="succeeded",
        ),
    )
    store = FileFixPatternStore(tmp_path / "fix-patterns.jsonl")

    stored = store.upsert(pattern)
    reloaded = FileFixPatternStore(tmp_path / "fix-patterns.jsonl")
    matches = reloaded.find_matches(signature)

    assert stored.success_count == 1
    assert len(matches) == 1
    assert matches[0].pattern_ref == pattern.pattern_ref
    assert "ModuleNotFoundError" not in reloaded.path.read_text(encoding="utf-8")
    assert "artifact://logs/failure" in reloaded.path.read_text(encoding="utf-8")


def test_store_merges_evidence_for_existing_signature(tmp_path) -> None:
    signature = extract_error_signature("ValueError: invalid status from provider 1234")
    assert signature is not None
    store = FileFixPatternStore(tmp_path / "fix-patterns.jsonl")
    first = FixPattern.from_successful_run(
        signature=signature,
        summary="Normalize unknown provider status values.",
        steps=["Map provider status before workflow projection."],
        evidence=EvidenceRun(workflowId="workflow-1", outcome="succeeded"),
    )
    second = FixPattern.from_successful_run(
        signature=signature,
        summary="Normalize unknown provider status values.",
        steps=["Keep a regression test for unknown status values."],
        evidence=EvidenceRun(workflowId="workflow-2", outcome="succeeded"),
    )

    store.upsert(first)
    merged = store.upsert(second)

    assert merged.success_count == 2
    assert len(merged.evidence) == 2
    assert merged.steps == [
        "Keep a regression test for unknown status values.",
        "Map provider status before workflow projection.",
    ]


def test_fix_patterns_project_to_memory_proposals() -> None:
    signature = extract_error_signature("RuntimeError: docker socket unavailable")
    assert signature is not None
    pattern = FixPattern.from_successful_run(
        signature=signature,
        summary="Use managed-agent local test mode.",
        steps=["Set MOONMIND_FORCE_LOCAL_TESTS=1 for unit tests."],
        evidence=EvidenceRun(workflowId="workflow-1", outcome="succeeded"),
    )

    proposals = fix_patterns_to_memory_proposals([pattern])

    assert proposals == [
        {
            "proposalRef": pattern.pattern_ref,
            "state": "accepted_for_run_context",
            "summary": (
                "Use managed-agent local test mode. Steps: "
                "Set MOONMIND_FORCE_LOCAL_TESTS=1 for unit tests."
            ),
        }
    ]


def test_procedural_memory_rejects_secretish_values() -> None:
    signature = extract_error_signature("RuntimeError: token leaked")
    assert signature is not None

    with pytest.raises(ValueError, match="raw secret material"):
        FixPattern.from_successful_run(
            signature=signature,
            summary="Do not store ghp_unsafe in memory.",
            steps=["Redact the raw token."],
            evidence=EvidenceRun(workflowId="workflow-1", outcome="succeeded"),
        )
