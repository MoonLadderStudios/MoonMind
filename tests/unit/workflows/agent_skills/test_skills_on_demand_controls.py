from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
    RuntimeMaterializationMode,
    RuntimeSkillMaterialization,
    SkillCatalogSearchResult,
    SkillsOnDemandFailureDiagnostic,
    SkillsOnDemandQueryRequest,
    SkillsOnDemandRequest,
    SkillsOnDemandRequestResult,
    SkillsOnDemandRequestedSkill,
)
from moonmind.services.skills_on_demand import (
    SKILLS_ON_DEMAND_DISABLED_CODE,
    SKILLS_ON_DEMAND_DISABLED_MESSAGE,
    SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE,
    SkillsOnDemandService,
)
from moonmind.workflows.agent_skills.agent_skills_activities import AgentSkillsActivities

pytestmark = [pytest.mark.asyncio]


def _entry(
    name: str,
    *,
    source_kind: AgentSkillSourceKind = AgentSkillSourceKind.BUILT_IN,
    version: str | None = "1.0.0",
    content_ref: str | None = None,
    source_path: str | None = None,
) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version=version,
        content_ref=content_ref,
        content_digest="sha256:secret-digest" if content_ref else None,
        provenance=AgentSkillProvenance(
            source_kind=source_kind,
            source_path=source_path,
        ),
    )


async def test_disabled_query_returns_denied_without_catalog_results() -> None:
    result = await SkillsOnDemandService(enabled=False).query(
        SkillsOnDemandQueryRequest(query="jira")
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    assert result.message == SKILLS_ON_DEMAND_DISABLED_MESSAGE
    assert result.results == []


async def test_disabled_request_returns_denied_without_snapshot_mutation() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="moonspec-implement",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )

    result = await SkillsOnDemandService(enabled=False).request(
        SkillsOnDemandRequest(
            requested_skills=[
                SkillsOnDemandRequestedSkill(name="jira-issue-updater")
            ],
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    assert result.message == SKILLS_ON_DEMAND_DISABLED_MESSAGE
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None
    assert result.active_snapshot_id == "skillset-active"


async def test_enabled_query_returns_metadata_only_results() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[
            _entry(
                "jira-issue-updater",
                content_ref="artifact-body-ref",
                source_path="/hidden/SKILL.md",
            ),
            _entry("moonspec-plan"),
        ],
    ).query(
        SkillsOnDemandQueryRequest(query="jira", runtime_id="codex", max_results=5)
    )

    assert result.status == "ok"
    assert result.code is None
    assert result.metadata["result_count"] == 1
    assert result.results == [
        SkillCatalogSearchResult(
            name="jira-issue-updater",
            latest_version="1.0.0",
            source_kind=AgentSkillSourceKind.BUILT_IN,
            eligible=True,
            in_current_snapshot=False,
            eligibility_summary="Eligible for this runtime and deployment policy.",
        )
    ]
    serialized = result.model_dump(mode="json")
    assert "artifact-body-ref" not in str(serialized)
    assert "secret-digest" not in str(serialized)
    assert "/hidden/SKILL.md" not in str(serialized)


async def test_query_result_records_single_bounded_audit_event() -> None:
    query_text = "jira"

    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    ).query(
        SkillsOnDemandQueryRequest(
            query=query_text,
            runtime_id="codex",
            current_snapshot_ref="skillset-active",
            max_results=5,
        )
    )

    assert len(result.audit_events) == 1
    event = result.audit_events[0]
    assert event.event_type == "skills_on_demand.query"
    assert event.runtime_id == "codex"
    assert event.current_snapshot_id == "skillset-active"
    assert event.query_hash == result.metadata["query_hash"]
    assert event.result_count == 1
    assert event.denied is False
    serialized = event.model_dump(mode="json")
    assert query_text not in str(serialized)
    assert "jira-issue-updater" not in str(serialized)


async def test_enabled_query_validates_blank_query() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    ).query(SkillsOnDemandQueryRequest(query="   "))

    assert result.status == "denied"
    assert result.code == "invalid_request"
    assert result.results == []
    assert result.metadata["denied"] is True
    assert result.audit_events[0].event_type == "skills_on_demand.query"
    assert result.audit_events[0].denied is True
    assert result.audit_events[0].denial_code == "invalid_request"


async def test_enabled_query_validates_blank_context_values() -> None:
    service = SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    )

    runtime_result = await service.query(
        SkillsOnDemandQueryRequest(query="jira", runtime_id=" ")
    )
    snapshot_result = await service.query(
        SkillsOnDemandQueryRequest(query="jira", current_snapshot_ref=" ")
    )

    assert runtime_result.status == "denied"
    assert runtime_result.code == "invalid_request"
    assert snapshot_result.status == "denied"
    assert snapshot_result.code == "invalid_request"


async def test_denied_query_diagnostics_fall_back_to_active_snapshot() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )

    result = SkillsOnDemandService(enabled=True)._denied_result(
        SkillsOnDemandQueryRequest(
            query="jira",
            active_snapshot=active_snapshot,
        ),
        code="artifact_unavailable",
        message="Catalog artifact could not be loaded.",
    )

    assert result.diagnostics_ref == (
        "diagnostics://skills-on-demand/artifact_unavailable/skillset-active"
    )
    assert result.failure_diagnostic is not None
    assert result.failure_diagnostic.current_snapshot_ref == "skillset-active"
    assert result.failure_diagnostic.diagnostics_ref == result.diagnostics_ref
    assert result.audit_events[0].current_snapshot_id == "skillset-active"
    assert result.audit_events[0].diagnostics_ref == result.diagnostics_ref


async def test_enabled_query_marks_policy_ineligible_matches() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[
            _entry(
                "local-jira-helper",
                source_kind=AgentSkillSourceKind.LOCAL,
            )
        ],
        allow_local_skills=False,
    ).query(SkillsOnDemandQueryRequest(query="jira"))

    assert result.status == "ok"
    assert len(result.results) == 1
    assert result.results[0].eligible is False
    assert result.results[0].eligibility_summary == (
        "Blocked because local Skill sources are disabled for this query."
    )


async def test_enabled_query_marks_current_snapshot_membership() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater")],
    )

    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[_entry("jira-issue-updater")],
    ).query(
        SkillsOnDemandQueryRequest(
            query="jira",
            current_snapshot_ref="skillset-active",
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "ok"
    assert result.results[0].in_current_snapshot is True


async def test_enabled_query_respects_max_results() -> None:
    result = await SkillsOnDemandService(
        enabled=True,
        catalog_entries=[
            _entry("jira-a"),
            _entry("jira-b"),
            _entry("jira-c"),
        ],
    ).query(SkillsOnDemandQueryRequest(query="jira", max_results=2))

    assert result.status == "ok"
    assert [entry.name for entry in result.results] == ["jira-a", "jira-b"]
    assert result.metadata["result_count"] == 2


async def test_enabled_request_returns_structured_not_implemented_result() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )

    result = await SkillsOnDemandService(enabled=True).request(
        SkillsOnDemandRequest(
            current_snapshot_ref="skillset-active",
            requested_skills=[
                SkillsOnDemandRequestedSkill(name="jira-issue-updater")
            ],
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE
    assert result.active_snapshot_id == "skillset-active"
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None
    assert result.failure_diagnostic == SkillsOnDemandFailureDiagnostic(
        status="denied",
        code=SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE,
        message=result.message,
        current_snapshot_ref="skillset-active",
    )
    assert len(result.audit_events) == 1
    assert result.audit_events[0].event_type == "skills_on_demand.request"
    assert result.audit_events[0].result == "denied"
    assert result.audit_events[0].result_code == SKILLS_ON_DEMAND_ENABLED_NOT_IMPLEMENTED_CODE
    assert result.audit_events[0].parent_snapshot_id == "skillset-active"


async def test_enabled_request_validates_required_snapshot_and_requested_skills() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    service = SkillsOnDemandService(enabled=True)

    missing_snapshot = await service.request(
        SkillsOnDemandRequest(
            requested_skills=[SkillsOnDemandRequestedSkill(name="jira-issue-updater")],
            active_snapshot=active_snapshot,
        )
    )
    empty_requested = await service.request(
        SkillsOnDemandRequest(
            current_snapshot_ref="skillset-active",
            requested_skills=[],
            active_snapshot=active_snapshot,
        )
    )
    blank_name = await service.request(
        SkillsOnDemandRequest(
            current_snapshot_ref="skillset-active",
            requested_skills=[SkillsOnDemandRequestedSkill(name=" ")],
            active_snapshot=active_snapshot,
        )
    )

    assert missing_snapshot.status == "denied"
    assert missing_snapshot.code == "invalid_request"
    assert missing_snapshot.active_snapshot_id == "skillset-active"
    assert empty_requested.status == "denied"
    assert empty_requested.code == "invalid_request"
    assert blank_name.status == "denied"
    assert blank_name.code == "invalid_request"


async def test_enabled_request_returns_no_change_for_already_active_skills() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        manifest_ref="manifest-active",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater")],
    )

    result = await SkillsOnDemandService(enabled=True).request(
        SkillsOnDemandRequest(
            current_snapshot_ref="skillset-active",
            requested_skills=[SkillsOnDemandRequestedSkill(name="jira-issue-updater")],
            active_snapshot=active_snapshot,
        )
    )

    assert result.status == "no_change"
    assert result.code == "already_active"
    assert result.active_snapshot_id == "skillset-active"
    assert result.parent_snapshot_ref == "skillset-active"
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref == "manifest-active"
    assert result.metadata["activated_skills"] == []
    assert len(result.audit_events) == 1
    assert result.audit_events[0].event_type == "skills_on_demand.request"
    assert result.audit_events[0].result == "no_change"
    assert result.audit_events[0].result_code == "already_active"
    assert result.audit_events[0].requested_skills == ["jira-issue-updater"]


async def test_enabled_request_builds_activated_result_with_compact_lineage() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[_entry("moonspec-plan", content_ref="active-body-ref")],
    )
    derived_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-derived",
        manifest_ref="manifest-derived",
        resolved_at=datetime.now(UTC),
        skills=[
            _entry("jira-issue-updater", content_ref="requested-body-ref"),
            _entry("moonspec-plan", content_ref="active-body-ref"),
        ],
        source_trace={
            "skillsOnDemandLineage": {
                "parentSnapshotId": "skillset-active",
                "createdBy": "skills_on_demand",
                "requestedSkills": ["jira-issue-updater"],
            }
        },
    )
    materialization = RuntimeSkillMaterialization(
        runtime_id="codex",
        materialization_mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        workspace_paths=["/workspace/.agents/skills"],
        metadata={
            "visiblePath": "/workspace/.agents/skills",
            "manifestPath": "/workspace/runtime/skills_active/skillset-derived/_manifest.json",
            "activationTiming": "atomic",
            "materializationVerified": True,
        },
    )

    result = await SkillsOnDemandService(enabled=True).request(
        SkillsOnDemandRequest(
            current_snapshot_ref="skillset-active",
            requested_skills=[
                SkillsOnDemandRequestedSkill(name="jira-issue-updater")
            ],
            reason="Need Jira workflow",
            runtime_id="codex",
            step_id="step-1",
            active_snapshot=active_snapshot,
        ),
        resolved_skillset=derived_snapshot,
        materialization=materialization,
    )

    assert result.status == "activated"
    assert result.code is None
    assert result.active_snapshot_id == "skillset-active"
    assert result.parent_snapshot_ref == "skillset-active"
    assert result.snapshot_id == "skillset-derived"
    assert result.resolved_skillset_ref == "manifest-derived"
    assert result.materialization is not None
    assert result.materialization.mode == RuntimeMaterializationMode.WORKSPACE_MOUNTED
    assert result.metadata["requested_skills"] == ["jira-issue-updater"]
    assert result.metadata["activated_skills"] == ["jira-issue-updater"]
    assert result.metadata["activation_timing"] == "atomic"
    assert result.metadata["materialization_verified"] is True
    serialized = result.model_dump(mode="json")
    assert "requested-body-ref" not in str(serialized)
    assert "active-body-ref" not in str(serialized)
    assert len(result.audit_events) == 1
    event = result.audit_events[0]
    assert event.event_type == "skills_on_demand.request"
    assert event.result == "activated"
    assert event.parent_snapshot_id == "skillset-active"
    assert event.derived_snapshot_id == "skillset-derived"
    assert event.manifest_ref == "manifest-derived"
    assert event.requested_skills == ["jira-issue-updater"]
    assert "requested-body-ref" not in str(event.model_dump(mode="json"))
    assert "active-body-ref" not in str(event.model_dump(mode="json"))


async def test_request_result_accepts_reserved_requires_approval_event_value() -> None:
    result = SkillsOnDemandRequestResult(
        status="requires_approval",
        message="Approval is required before activation.",
    )

    assert result.status == "requires_approval"


async def test_enabled_request_denial_preserves_active_snapshot() -> None:
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )

    result = SkillsOnDemandService(enabled=True).denied_request_result(
        SkillsOnDemandRequest(
            current_snapshot_ref="skillset-active",
            requested_skills=[SkillsOnDemandRequestedSkill(name="missing-skill")],
            active_snapshot=active_snapshot,
        ),
        code="skill_not_found",
        message="Requested Skill could not be resolved.",
    )

    assert result.status == "denied"
    assert result.code == "skill_not_found"
    assert result.active_snapshot_id == "skillset-active"
    assert result.parent_snapshot_ref == "skillset-active"
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None
    assert result.failure_diagnostic is not None
    assert result.failure_diagnostic.code == "skill_not_found"
    assert result.failure_diagnostic.current_snapshot_ref == "skillset-active"
    assert result.audit_events[0].result == "denied"
    assert result.audit_events[0].diagnostics_ref is None


async def test_runtime_failure_code_classifies_checksum_as_materialization_failure() -> None:
    activities = AgentSkillsActivities()

    assert (
        activities._skills_on_demand_runtime_code("checksum mismatch for active")
        == "materialization_failed"
    )
    assert (
        activities._skills_on_demand_runtime_code("runtime refresh update failed")
        == "runtime_refresh_failed"
    )


async def test_enabled_activity_request_reports_runtime_refresh_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.repo_root",
        str(tmp_path),
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater", content_ref="requested-body-ref")],
    )
    materialization = RuntimeSkillMaterialization(
        runtime_id="codex",
        materialization_mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        workspace_paths=[str(tmp_path / ".agents" / "skills")],
        metadata={
            "runtimeRefreshFailed": True,
            "runtimeRefreshMessage": "runtime refresh update failed",
        },
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with (
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
            new=AsyncMock(return_value=resolved_additions),
        ),
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize",
            new=AsyncMock(return_value=materialization),
        ),
    ):
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                current_snapshot_ref="skillset-active",
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "denied"
    assert result.code == "runtime_refresh_failed"
    assert result.active_snapshot_id == "skillset-active"
    assert result.parent_snapshot_ref == "skillset-active"
    assert result.snapshot_id is None
    assert result.resolved_skillset_ref is None


async def test_disabled_activity_query_does_not_use_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        False,
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve"
    ) as mock_resolve:
        result = await env.run(
            activities.query_on_demand,
            SkillsOnDemandQueryRequest(query="jira"),
        )

    assert result.status == "denied"
    assert result.code == SKILLS_ON_DEMAND_DISABLED_CODE
    mock_resolve.assert_not_called()


async def test_disabled_activity_request_does_not_materialize_new_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        False,
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    with patch(
        "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize"
    ) as mock_materialize:
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "denied"
    assert result.active_snapshot_id == "skillset-active"
    mock_materialize.assert_not_called()


async def test_enabled_activity_query_returns_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    async def fake_query_catalog(*args, **kwargs):
        return [_entry("jira-issue-updater", content_ref="artifact-body-ref")]

    with (
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.query_catalog",
            side_effect=fake_query_catalog,
        ) as mock_query_catalog,
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize"
        ) as mock_materialize,
    ):
        result = await env.run(
            activities.query_on_demand,
            SkillsOnDemandQueryRequest(query="jira"),
        )

    assert result.status == "ok"
    assert result.results[0].name == "jira-issue-updater"
    assert result.results[0].eligible is True
    assert "artifact-body-ref" not in str(result.model_dump(mode="json"))
    mock_query_catalog.assert_awaited_once()
    mock_materialize.assert_not_called()


async def test_enabled_activity_request_resolves_against_active_snapshot_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.repo_root",
        str(tmp_path),
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[_entry("helper-skill", content_ref="helper-body-ref")],
    )
    primary_skill = _entry("primary-skill", content_ref="primary-body-ref").model_copy(
        update={"required_skills": ["helper-skill"]}
    )
    materialization = RuntimeSkillMaterialization(
        runtime_id="codex",
        materialization_mode=RuntimeMaterializationMode.WORKSPACE_MOUNTED,
        workspace_paths=[str(tmp_path / ".agents" / "skills")],
    )
    activities = AgentSkillsActivities()
    env = ActivityEnvironment()

    async def fake_load_candidates(*_args, **_kwargs):
        return {AgentSkillSourceKind.DEPLOYMENT: [primary_skill]}

    with (
        patch(
            "moonmind.services.skill_resolution.AgentSkillResolver._load_candidates",
            new=AsyncMock(side_effect=fake_load_candidates),
        ),
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize",
            new=AsyncMock(return_value=materialization),
        ) as mock_materialize,
    ):
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                current_snapshot_ref="skillset-active",
                requested_skills=[SkillsOnDemandRequestedSkill(name="primary-skill")],
                runtime_id="codex",
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "activated"
    assert result.metadata["activated_skills"] == ["primary-skill"]
    materialized_skillset = mock_materialize.await_args.args[0]
    by_name = {skill.skill_name: skill for skill in materialized_skillset.skills}
    assert set(by_name) == {"helper-skill", "primary-skill"}
    assert by_name["helper-skill"].content_ref == "helper-body-ref"
    assert by_name["primary-skill"].required_by == []


async def test_enabled_activity_request_does_not_persist_manifest_on_materialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.skills_on_demand_enabled",
        True,
    )
    monkeypatch.setattr(
        "moonmind.workflows.agent_skills.agent_skills_activities.settings.workflow.repo_root",
        str(tmp_path),
    )
    active_snapshot = ResolvedSkillSet(
        snapshot_id="skillset-active",
        resolved_at=datetime.now(UTC),
        skills=[],
    )
    resolved_additions = ResolvedSkillSet(
        snapshot_id="resolver-snapshot",
        resolved_at=datetime.now(UTC),
        skills=[_entry("jira-issue-updater", content_ref="requested-body-ref")],
    )

    class FakeArtifactService:
        def __init__(self) -> None:
            self.created: list[dict] = []

        async def create(self, **kwargs):
            self.created.append(kwargs)
            return SimpleNamespace(artifact_id=f"artifact-{len(self.created)}"), None

        async def write_complete(self, **_kwargs) -> None:
            return None

    artifact_service = FakeArtifactService()
    activities = AgentSkillsActivities(artifact_service=artifact_service)
    env = ActivityEnvironment()

    with (
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillResolver.resolve",
            new=AsyncMock(return_value=resolved_additions),
        ),
        patch(
            "moonmind.workflows.agent_skills.agent_skills_activities.AgentSkillMaterializer.materialize",
            new=AsyncMock(side_effect=RuntimeError("materialization exploded")),
        ),
    ):
        result = await env.run(
            activities.request_on_demand,
            SkillsOnDemandRequest(
                current_snapshot_ref="skillset-active",
                requested_skills=[
                    SkillsOnDemandRequestedSkill(name="jira-issue-updater")
                ],
                active_snapshot=active_snapshot,
            ),
        )

    assert result.status == "denied"
    assert result.code == "materialization_failed"
    assert artifact_service.created == []
