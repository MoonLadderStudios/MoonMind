import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.mcp.skills_on_demand_registry import (
    SkillsOnDemandToolExecutionContext,
    SkillsOnDemandToolRegistry,
)
from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
)

pytestmark = [pytest.mark.asyncio]


def _entry(name: str) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        content_ref="hidden-content-ref",
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.BUILT_IN),
    )


def _snapshot(*, skills: list[ResolvedSkillEntry] | None = None) -> ResolvedSkillSet:
    return ResolvedSkillSet(
        snapshot_id="skillset-active",
        manifest_ref="manifest-active",
        resolved_at=datetime.now(UTC),
        skills=skills or [_entry("jira-issue-updater")],
    )


class _StaticArtifactService:
    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool,
    ) -> tuple[object, bytes]:
        del principal, allow_restricted_raw
        assert artifact_id in {"manifest-active", "skillset-active"}
        return object(), json.dumps(_snapshot().model_dump(mode="json")).encode("utf-8")


async def test_skills_on_demand_commands_hidden_but_direct_call_denies_when_disabled() -> None:
    registry = SkillsOnDemandToolRegistry(expose_commands=False)

    assert registry.list_tools() == []

    result = await registry.call_tool(
        tool="moonmind.skills.query",
        arguments={"query": "jira"},
        context=SkillsOnDemandToolExecutionContext(
            enabled=False,
            workspace_root="/tmp/repo",
        ),
    )

    assert result["status"] == "denied"
    assert result["code"] == "feature_disabled"
    assert result["results"] == []


async def test_enabled_query_returns_metadata_without_skill_body_refs() -> None:
    registry = SkillsOnDemandToolRegistry(expose_commands=True)

    with patch(
        "moonmind.mcp.skills_on_demand_registry.AgentSkillResolver.query_catalog",
        new=AsyncMock(return_value=[_entry("jira-issue-updater")]),
    ):
        result = await registry.call_tool(
            tool="moonmind.skills.query",
            arguments={
                "query": "jira",
                "current_snapshot_ref": "skillset-active",
                "active_snapshot": _snapshot().model_dump(mode="json"),
            },
            context=SkillsOnDemandToolExecutionContext(
                enabled=True,
                workspace_root="/tmp/repo",
            ),
        )

    assert result["status"] == "ok"
    assert result["results"] == [
        {
            "name": "jira-issue-updater",
            "title": None,
            "description": None,
            "source_kind": "built_in",
            "supported_runtimes": [],
            "required_capabilities": [],
            "eligible": True,
            "in_current_snapshot": True,
            "eligibility_summary": "Eligible for this runtime and deployment policy.",
        }
    ]
    assert "hidden-content-ref" not in str(result)
    assert result["audit_events"][0]["event_type"] == "skills_on_demand.query"
    assert result["audit_events"][0]["query_hash"]


async def test_enabled_request_already_active_returns_no_change() -> None:
    registry = SkillsOnDemandToolRegistry(expose_commands=True)

    result = await registry.call_tool(
        tool="moonmind.skills.request",
        arguments={
            "current_snapshot_ref": "skillset-active",
            "requested_skills": [{"name": "jira-issue-updater"}],
            "active_snapshot": _snapshot().model_dump(mode="json"),
        },
        context=SkillsOnDemandToolExecutionContext(
            enabled=True,
            workspace_root="/tmp/repo",
            artifact_service=_StaticArtifactService(),
        ),
    )

    assert result["status"] == "no_change"
    assert result["code"] == "already_active"
    assert result["active_snapshot_id"] == "skillset-active"
    assert result["snapshot_id"] is None


async def test_enabled_request_loads_active_snapshot_from_manifest_ref() -> None:
    registry = SkillsOnDemandToolRegistry(expose_commands=True)

    result = await registry.call_tool(
        tool="moonmind.skills.request",
        arguments={
            "current_snapshot_ref": "manifest-active",
            "requested_skills": [{"name": "jira-issue-updater"}],
        },
        context=SkillsOnDemandToolExecutionContext(
            enabled=True,
            workspace_root="/tmp/repo",
            artifact_service=_StaticArtifactService(),
        ),
    )

    assert result["status"] == "no_change"
    assert result["active_snapshot_id"] == "skillset-active"
    assert result["parent_snapshot_ref"] == "skillset-active"


async def test_enabled_request_rejects_caller_supplied_active_snapshot() -> None:
    registry = SkillsOnDemandToolRegistry(expose_commands=True)
    caller_snapshot = _snapshot(skills=[_entry("unsafe-client-skill")])

    with patch(
        "moonmind.mcp.skills_on_demand_registry.AgentSkillResolver.resolve",
        new=AsyncMock(side_effect=ValueError("requested Skill was not found")),
    ):
        result = await registry.call_tool(
            tool="moonmind.skills.request",
            arguments={
                "current_snapshot_ref": "skillset-active",
                "requested_skills": [{"name": "unsafe-client-skill"}],
                "active_snapshot": caller_snapshot.model_dump(mode="json"),
            },
            context=SkillsOnDemandToolExecutionContext(
                enabled=True,
                workspace_root="/tmp/repo",
                artifact_service=_StaticArtifactService(),
            ),
        )

    assert result["status"] == "denied"
    assert result["code"] == "skill_not_found"
    assert result["active_snapshot_id"] == "skillset-active"
