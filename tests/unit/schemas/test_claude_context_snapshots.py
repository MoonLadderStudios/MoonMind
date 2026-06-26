"""Unit tests for Claude context snapshot contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.managed_session_models import (
    CLAUDE_CONTEXT_ON_DEMAND_KINDS,
    CLAUDE_CONTEXT_STARTUP_KINDS,
    ClaudeContextSegment,
    ClaudeContextSnapshot,
    claude_default_reinjection_policy,
    compact_claude_context_snapshot,
)

NOW = datetime(2026, 4, 16, tzinfo=UTC)

@pytest.mark.parametrize(
    "kind",
    [
        "system_prompt",
        "output_style",
        "managed_claude_md",
        "project_claude_md",
        "local_claude_md",
        "auto_memory",
        "mcp_tool_manifest",
        "skill_description",
        "hook_injected_context",
    ],
)
def test_startup_context_source_kinds_are_documented_and_valid(kind: str) -> None:
    assert kind in CLAUDE_CONTEXT_STARTUP_KINDS

    segment = ClaudeContextSegment(
        segmentId=f"segment-{kind}",
        kind=kind,
        sourceRef=f"runtime://{kind}",
        loadedAt="startup",
        reinjectionPolicy=claude_default_reinjection_policy(kind),
        guidanceRole="guidance"
        if kind
        in {
            "managed_claude_md",
            "project_claude_md",
            "local_claude_md",
            "auto_memory",
            "skill_description",
        }
        else "neutral",
    )

    assert segment.kind == kind
    assert segment.loaded_at == "startup"

@pytest.mark.parametrize(
    "kind",
    [
        "file_read",
        "nested_claude_md",
        "path_rule",
        "invoked_skill_body",
        "runtime_summary",
    ],
)
def test_on_demand_context_source_kinds_are_documented_and_valid(kind: str) -> None:
    assert kind in CLAUDE_CONTEXT_ON_DEMAND_KINDS

    segment = ClaudeContextSegment(
        segmentId=f"segment-{kind}",
        kind=kind,
        sourceRef=f"runtime://{kind}",
        loadedAt="on_demand",
        reinjectionPolicy=claude_default_reinjection_policy(kind),
        guidanceRole="guidance"
        if kind in {"nested_claude_md", "path_rule", "invoked_skill_body"}
        else "neutral",
    )

    assert segment.kind == kind
    assert segment.loaded_at == "on_demand"

def test_unknown_context_source_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ClaudeContextSegment(
            segmentId="segment-unknown",
            kind="unknown_context",
            sourceRef="runtime://unknown",
            loadedAt="startup",
            reinjectionPolicy="always",
            guidanceRole="neutral",
        )

def test_reinjection_policy_is_required_and_defaults_match_design() -> None:
    assert claude_default_reinjection_policy("system_prompt") == "always"
    assert claude_default_reinjection_policy("file_read") == "never"
    assert claude_default_reinjection_policy("nested_claude_md") == "on_demand"
    assert claude_default_reinjection_policy("runtime_summary") == "on_demand"
    assert claude_default_reinjection_policy("skill_description") == "startup_refresh"
    assert claude_default_reinjection_policy("invoked_skill_body") == "budgeted"
    assert claude_default_reinjection_policy("hook_injected_context") == "configurable"

    with pytest.raises(ValidationError):
        ClaudeContextSegment(
            segmentId="segment-missing-policy",
            kind="system_prompt",
            sourceRef="runtime://system",
            loadedAt="startup",
            guidanceRole="neutral",
        )

@pytest.mark.parametrize(
    "kind",
    [
        "managed_claude_md",
        "project_claude_md",
        "local_claude_md",
        "auto_memory",
        "nested_claude_md",
        "path_rule",
        "invoked_skill_body",
    ],
)
def test_guidance_and_memory_sources_cannot_be_enforcement(kind: str) -> None:
    with pytest.raises(ValidationError, match="guidance"):
        ClaudeContextSegment(
            segmentId=f"segment-{kind}",
            kind=kind,
            sourceRef=f"runtime://{kind}",
            loadedAt="startup" if kind != "nested_claude_md" else "on_demand",
            reinjectionPolicy=claude_default_reinjection_policy(kind),
            guidanceRole="enforcement",
        )

def test_context_segment_rejects_large_payload_metadata() -> None:
    with pytest.raises(ValidationError):
        ClaudeContextSegment(
            segmentId="segment-large",
            kind="file_read",
            sourceRef="runtime://file",
            loadedAt="on_demand",
            reinjectionPolicy="never",
            guidanceRole="neutral",
            metadata={"content": "x" * 100_000},
        )

def test_compaction_creates_new_epoch_without_mutating_original_snapshot() -> None:
    original = ClaudeContextSnapshot(
        snapshotId="snapshot-epoch-0",
        sessionId="claude-session-1",
        turnId="turn-1",
        compactionEpoch=0,
        segments=[
            ClaudeContextSegment(
                segmentId="segment-system",
                kind="system_prompt",
                sourceRef="runtime://system",
                loadedAt="startup",
                reinjectionPolicy="always",
                guidanceRole="neutral",
            ),
            ClaudeContextSegment(
                segmentId="segment-file",
                kind="file_read",
                sourceRef="runtime://file",
                loadedAt="on_demand",
                reinjectionPolicy="never",
                guidanceRole="neutral",
            ),
            ClaudeContextSegment(
                segmentId="segment-skill",
                kind="invoked_skill_body",
                sourceRef="runtime://skill",
                loadedAt="on_demand",
                reinjectionPolicy="budgeted",
                guidanceRole="guidance",
            ),
        ],
        createdAt=NOW,
    )

    result = compact_claude_context_snapshot(
        snapshot=original,
        snapshot_id="snapshot-epoch-1",
        work_item_id="work-compaction-1",
        created_at=NOW,
    )

    assert original.compaction_epoch == 0
    assert tuple(segment.segment_id for segment in original.segments) == (
        "segment-system",
        "segment-file",
        "segment-skill",
    )
    assert result.snapshot.compaction_epoch == 1
    assert result.snapshot.snapshot_id == "snapshot-epoch-1"
    assert tuple(segment.segment_id for segment in result.snapshot.segments) == (
        "segment-system",
        "segment-skill",
    )
    assert all(
        segment.loaded_at == "post_compaction"
        for segment in result.snapshot.segments
    )
    assert result.work_item.kind == "compaction"
    assert tuple(event.event_name for event in result.events) == (
        "work.compaction.started",
        "work.compaction.completed",
    )

def test_compaction_deep_copies_retained_segment_metadata() -> None:
    original = ClaudeContextSnapshot(
        snapshotId="snapshot-epoch-0",
        sessionId="claude-session-1",
        turnId="turn-1",
        compactionEpoch=0,
        segments=[
            ClaudeContextSegment(
                segmentId="segment-system",
                kind="system_prompt",
                sourceRef="runtime://system",
                loadedAt="startup",
                reinjectionPolicy="always",
                guidanceRole="neutral",
                metadata={"annotations": {"source": "startup"}},
            ),
        ],
        createdAt=NOW,
    )

    result = compact_claude_context_snapshot(
        snapshot=original,
        snapshot_id="snapshot-epoch-1",
        work_item_id="work-compaction-1",
        created_at=NOW,
    )

    result.snapshot.segments[0].metadata["annotations"]["source"] = "compacted"

    assert original.segments[0].metadata == {
        "annotations": {"source": "startup"}
    }
