"""Unit tests for agent queue ORM enum configuration."""

from __future__ import annotations

from moonmind.workflows.agent_queue import models


def test_agent_job_status_enum_uses_lowercase_values() -> None:
    """PostgreSQL enum labels should match migration-defined lowercase values."""

    status_enum = models.AgentJob.__table__.c.status.type
    assert set(status_enum.enums) == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "dead_letter",
    }


def test_agent_job_event_level_enum_uses_lowercase_values() -> None:
    """Event-level enum labels should match migration-defined lowercase values."""

    level_enum = models.AgentJobEvent.__table__.c.level.type
    assert set(level_enum.enums) == {"info", "warn", "error"}
