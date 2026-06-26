"""Integration-style boundary tests for Claude context snapshots."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas.managed_session_models import (
    ClaudeContextEvent,
    ClaudeContextSegment,
    ClaudeContextSnapshot,
    compact_claude_context_snapshot,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def test_claude_context_boundary_indexes_context_and_compaction_epochs() -> None:
    startup_segments = [
        ClaudeContextSegment(
            segmentId="segment-system",
            kind="system_prompt",
            sourceRef="runtime://system-prompt",
            loadedAt="startup",
            reinjectionPolicy="always",
            guidanceRole="neutral",
        ),
        ClaudeContextSegment(
            segmentId="segment-managed-claude",
            kind="managed_claude_md",
            sourceRef="artifact://managed-claude-md",
            loadedAt="startup",
            reinjectionPolicy="always",
            guidanceRole="guidance",
        ),
        ClaudeContextSegment(
            segmentId="segment-mcp",
            kind="mcp_tool_manifest",
            sourceRef="artifact://mcp-manifest",
            loadedAt="startup",
            reinjectionPolicy="startup_refresh",
            guidanceRole="neutral",
        ),
        ClaudeContextSegment(
            segmentId="segment-skill-description",
            kind="skill_description",
            sourceRef="artifact://skill-description",
            loadedAt="startup",
            reinjectionPolicy="startup_refresh",
            guidanceRole="guidance",
        ),
    ]
    on_demand_segments = [
        ClaudeContextSegment(
            segmentId="segment-file-read",
            kind="file_read",
            sourceRef="artifact://file-read-pointer",
            loadedAt="on_demand",
            reinjectionPolicy="never",
            guidanceRole="neutral",
        ),
        ClaudeContextSegment(
            segmentId="segment-nested-rule",
            kind="nested_claude_md",
            sourceRef="artifact://nested-claude-md",
            loadedAt="on_demand",
            reinjectionPolicy="on_demand",
            guidanceRole="guidance",
        ),
        ClaudeContextSegment(
            segmentId="segment-invoked-skill",
            kind="invoked_skill_body",
            sourceRef="artifact://invoked-skill-pointer",
            loadedAt="on_demand",
            reinjectionPolicy="budgeted",
            guidanceRole="guidance",
        ),
    ]
    snapshot = ClaudeContextSnapshot(
        snapshotId="snapshot-epoch-0",
        sessionId="claude-session-1",
        turnId="turn-1",
        compactionEpoch=0,
        segments=[*startup_segments, *on_demand_segments],
        createdAt=NOW,
        metadata={"indexRef": "artifact://context-index"},
    )
    load_event = ClaudeContextEvent(
        eventId="event-context-loaded-1",
        sessionId="claude-session-1",
        turnId="turn-1",
        snapshotId=snapshot.snapshot_id,
        eventName="work.context.loaded",
        occurredAt=NOW,
        metadata={"segmentCount": len(snapshot.segments)},
    )

    result = compact_claude_context_snapshot(
        snapshot=snapshot,
        snapshot_id="snapshot-epoch-1",
        work_item_id="work-compaction-1",
        created_at=NOW,
    )

    assert load_event.event_name == "work.context.loaded"
    assert snapshot.compaction_epoch == 0
    assert result.snapshot.compaction_epoch == 1
    assert result.work_item.item_id == "work-compaction-1"
    assert result.work_item.kind == "compaction"
    assert result.work_item.payload["previousSnapshotId"] == "snapshot-epoch-0"
    assert result.work_item.payload["nextSnapshotId"] == "snapshot-epoch-1"
    assert tuple(event.event_name for event in result.events) == (
        "work.compaction.started",
        "work.compaction.completed",
    )
    assert all(
        "content" not in segment.metadata
        for segment in result.snapshot.segments
    )
    assert {
        segment.segment_id for segment in result.snapshot.segments
    } == {
        "segment-system",
        "segment-managed-claude",
        "segment-mcp",
        "segment-skill-description",
        "segment-invoked-skill",
    }
    assert "segment-file-read" not in {
        segment.segment_id for segment in result.snapshot.segments
    }
    assert all(
        segment.guidance_role != "enforcement"
        for segment in result.snapshot.segments
        if segment.kind
        in {
            "managed_claude_md",
            "nested_claude_md",
            "invoked_skill_body",
            "skill_description",
        }
    )
    for record in (
        snapshot,
        load_event,
        result.snapshot,
        result.work_item,
        *result.events,
    ):
        wire = record.model_dump(by_alias=True)
        assert "threadId" not in wire
        assert "childThread" not in wire
