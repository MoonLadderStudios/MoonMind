"""Schedule producers persist one verified dependency root."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from tests.artifact_access import access_for_test
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
    _complete_artifact,
    _service,
    temporal_db,
)

pytestmark = pytest.mark.asyncio


async def test_create_disabled_schedule_records_verified_input_root(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with temporal_db(tmp_path) as maker, maker() as session:
        artifacts = _service(session, tmp_path)
        source = await _complete_artifact(
            artifacts,
            principal="system",
            payload=b'{"instructions":"saved input"}',
            content_type="application/json",
        )
        adapter = SimpleNamespace(
            create_schedule=AsyncMock(),
            resolve_workflow_task_queue=MagicMock(return_value="mm.workflow.user.v2"),
        )
        schedules = RecurringWorkflowsService(
            session, temporal_client_adapter=adapter, artifact_service=artifacts
        )
        definition = await schedules.create_definition(
            name="Frozen input",
            description=None,
            enabled=False,
            schedule_type="cron",
            cron="0 6 * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=None,
            target={
                "workflowType": "MoonMind.UserWorkflow",
                "inputArtifactRef": source.artifact_id,
                "initialParameters": {
                    "targetRuntime": "codex",
                    "instructions": "Use saved input",
                },
            },
            policy={},
            actor=OperatorAdmission(via="loopback"),
        )
        assert definition.input_artifact_root_id is not None
        closure = await artifacts.verified_dependencies(
            root_artifact_id=definition.input_artifact_root_id,
            access=access_for_test("operator"),
        )
        assert closure is not None
        assert {entry.artifact_id for entry in closure.dependencies} == {
            source.artifact_id
        }
        assert all(entry.required for entry in closure.dependencies)
        await artifacts.sweep_lifecycle(
            principal="service:sweeper", now=datetime.now(UTC) + timedelta(days=400)
        )
        assert (
            await artifacts.read(
                artifact_id=source.artifact_id, access=access_for_test("system")
            )
        )[1] == b'{"instructions":"saved input"}'


async def test_backfill_commits_completed_page_before_next_page_failure(
    tmp_path, monkeypatch
):
    from uuid import UUID

    from api_service.db.models import (
        RecurringWorkflowDefinition,
        RecurringWorkflowScheduleType,
        RecurringWorkflowScopeType,
    )

    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            artifacts = _service(session, tmp_path)
            source = await _complete_artifact(
                artifacts,
                principal="system",
                payload=b'{"instructions":"original"}',
                content_type="application/json",
            )
            ids = [UUID(int=1), UUID(int=2)]
            for ident in ids:
                session.add(
                    RecurringWorkflowDefinition(
                        id=ident,
                        name="legacy",
                        enabled=False,
                        schedule_type=RecurringWorkflowScheduleType.CRON,
                        cron="0 6 * * *",
                        timezone="UTC",
                        scope_type=RecurringWorkflowScopeType.PERSONAL,
                        target={
                            "workflowType": "MoonMind.UserWorkflow",
                            "inputArtifactRef": source.artifact_id,
                            "initialParameters": {"targetRuntime": "codex"},
                        },
                        policy={},
                        temporal_schedule_id=f"mm-schedule:{ident}",
                        version=1,
                    )
                )
            await session.commit()
            for ident in ids:
                await artifacts.link_artifact(
                    artifact_id=source.artifact_id,
                    principal="system",
                    execution_ref={
                        "namespace": "default",
                        "workflow_id": f"mm-schedule:{ident}",
                        "run_id": "original",
                        "link_type": "input.instructions",
                    },
                )
            schedules = RecurringWorkflowsService(session, artifact_service=artifacts)
            monkeypatch.setattr(
                schedules,
                "_refresh_managed_bootstrap_target",
                AsyncMock(return_value=False),
            )
            real_execute = session.execute
            pages = 0

            async def interrupted(statement, *args, **kwargs):
                nonlocal pages
                if str(statement).startswith(
                    "SELECT recurring_workflow_definitions.id "
                ):
                    pages += 1
                    if pages == 2:
                        raise RuntimeError("synthetic next-page interruption")
                return await real_execute(statement, *args, **kwargs)

            monkeypatch.setattr(session, "execute", interrupted)
            with pytest.raises(RuntimeError, match="next-page"):
                await schedules.refresh_managed_bootstrap_schedules(limit=1)
            await session.rollback()
        async with maker() as observer:
            first = await observer.get(RecurringWorkflowDefinition, ids[0])
            second = await observer.get(RecurringWorkflowDefinition, ids[1])
            assert first.input_artifact_root_id is not None
            assert first.version == second.version == 1
            assert second.input_artifact_root_id is None


async def test_legacy_nonplan_cannot_adopt_unlinked_system_artifact(tmp_path):
    from uuid import uuid4

    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowValidationError,
    )

    async with temporal_db(tmp_path) as maker, maker() as session:
        artifacts = _service(session, tmp_path)
        source = await _complete_artifact(
            artifacts, principal="system", payload=b"unrelated"
        )
        schedules = RecurringWorkflowsService(session, artifact_service=artifacts)
        with pytest.raises(
            RecurringWorkflowValidationError, match="cannot read artifact"
        ):
            await schedules._retain_target_artifacts(
                {"inputArtifactRef": source.artifact_id}, definition_id=uuid4()
            )


async def test_injected_standalone_service_does_not_commit_schedule_owner(tmp_path):
    from uuid import uuid4

    from api_service.db.models import TemporalArtifact
    from moonmind.security.operator_admission import OperatorAdmission
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

    async with temporal_db(tmp_path) as maker:
        async with maker() as session:
            artifacts = _service(session, tmp_path)
            source = await _complete_artifact(
                artifacts, principal="system", payload=b"original"
            )
            source_id = source.artifact_id
            original_type = source.content_type
            source.content_type = "application/x-pending-owner-change"
            schedules = RecurringWorkflowsService(session, artifact_service=artifacts)
            root_id = await schedules._retain_target_artifacts(
                {"inputArtifactRef": source_id},
                definition_id=uuid4(),
                access=ArtifactAccessContext.for_operator(
                    OperatorAdmission(via="loopback")
                ),
            )
            await session.rollback()
        async with maker() as observer:
            assert await observer.get(TemporalArtifact, root_id) is None
            assert (
                await observer.get(TemporalArtifact, source_id)
            ).content_type == original_type
