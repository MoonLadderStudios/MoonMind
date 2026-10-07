"""Recovery inherits artifact inputs under its new execution authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalArtifactLink,
    TemporalArtifactStatus,
    TemporalExecutionCanonicalRecord,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.unit.workflows.temporal.test_temporal_service import (
    _create_temporal_artifact,
    _valid_failed_run_recovery_manifest_payload,
    _valid_recovery_checkpoint_payload,
    temporal_db,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("link_failure", [False, True], ids=["normal", "retry"])
async def test_failed_step_recovery_inherits_only_admitted_artifact_family_before_start(
    tmp_path, monkeypatch, link_failure
):
    from moonmind.workflows.temporal.artifacts import (
        ExecutionRef,
        TemporalArtifactRepository,
    )

    mock_client_adapter = MagicMock()
    mock_client_adapter.start_workflow = AsyncMock(
        return_value=SimpleNamespace(run_id="source-run")
    )
    mock_client_adapter.describe_workflow = AsyncMock(return_value=None)
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        source = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Recovery attachment authority",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"instructions": "Original objective"}},
            idempotency_key=None,
        )
        source.state = MoonMindWorkflowState.FAILED
        source.memo = {
            **source.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        source_id, source_run = source.workflow_id, source.run_id
        await session.commit()
        repository = TemporalArtifactRepository(session)
        for artifact_id, namespace, workflow_id in (
            ("art_objective", source.namespace, source_id),
            ("art_child_input", source.namespace, source_id + ":repair"),
            ("art_foreign", source.namespace, "mm:foreign"),
            ("art_forged_prefix", source.namespace, source_id + ":"),
            ("art_other_namespace", "foreign", source_id),
        ):
            await _create_temporal_artifact(
                session, artifact_id=artifact_id, status=TemporalArtifactStatus.COMPLETE
            )
            await repository.add_link(
                artifact_id=artifact_id,
                execution=ExecutionRef(
                    namespace=namespace,
                    workflow_id=workflow_id,
                    run_id=source_run,
                    link_type="input.primary",
                ),
            )
        # Multiple source links must not produce duplicate destination grants.
        await repository.add_link(
            artifact_id="art_objective",
            execution=ExecutionRef(
                namespace=source.namespace,
                workflow_id=source_id,
                run_id="earlier-source-run",
                link_type="input.primary",
            ),
        )
        await session.commit()
        mock_client_adapter.start_workflow.reset_mock()

        async def start_recovery(**kwargs):
            links = (
                (
                    await session.execute(
                        select(TemporalArtifactLink).where(
                            TemporalArtifactLink.workflow_id == kwargs["workflow_id"]
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {link.artifact_id for link in links} == {
                "art_objective",
                "art_child_input",
            }
            assert len(links) == 2
            return SimpleNamespace(run_id="recovered-run")

        mock_client_adapter.start_workflow.side_effect = start_recovery

        async def recover():
            return await service.create_failed_step_recovery_execution(
                source,
                recovery_checkpoint_ref=None,
                idempotency_key="attachment-recovery",
                checkpoint_payload=_valid_recovery_checkpoint_payload(
                    workflow_id=source_id,
                    run_id=source_run,
                    snapshot_ref="artifact://snapshot/source",
                ),
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=source_id,
                    run_id=source_run,
                ),
            )

        if link_failure:
            with monkeypatch.context() as patcher:
                patcher.setattr(
                    TemporalArtifactRepository,
                    "add_link",
                    AsyncMock(side_effect=RuntimeError("controlled linkage outage")),
                )
                with pytest.raises(RuntimeError, match="controlled linkage outage"):
                    await recover()
            mock_client_adapter.start_workflow.assert_not_awaited()
            records = (
                (await session.execute(select(TemporalExecutionCanonicalRecord)))
                .scalars()
                .all()
            )
            assert [record.workflow_id for record in records] == [source_id]
            await session.refresh(source)

        first = await recover()
        second = await recover()
        assert second["execution"] == first["execution"]
        mock_client_adapter.start_workflow.assert_awaited_once()
        links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id
                        == first["execution"]["workflowId"]
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 2
        assert {link.artifact_id for link in links} == {
            "art_objective",
            "art_child_input",
        }
        assert {link.run_id for link in links} == {first["execution"]["runId"]}
        assert {link.link_type for link in links} == {"input.recovery"}
