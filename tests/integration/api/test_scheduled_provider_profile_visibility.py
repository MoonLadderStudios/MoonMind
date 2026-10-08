"""Recurring admission crosses the real Temporal schedule/start/Visibility boundary."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import ScheduleOverlapPolicy
from temporalio.common import SearchAttributeKey
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base, ManagedAgentProviderProfile
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.workflows.executions.provider_profile_projection import (
    PROVIDER_PROFILE_MEMO_KEY,
    PROVIDER_PROFILE_SEARCH_ATTRIBUTE,
    provider_profile_id_token,
    provider_profile_state_token,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes
from tests.integration.api import (
    test_execution_list_provider_profile_visibility as profile_visibility,
)
from tests.integration.api.test_execution_list_provider_profile_visibility import (
    _GRANTED_PROFILE,
    _LAUNCH_TASK_QUEUE,
    _detect_optional_temporal_search_attributes,
    _GrantedProfileAgentRun,
    _production_query,
)

launch_resolution_stages = profile_visibility.launch_resolution_stages

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def _trigger(client, schedule_id: str):
    handle = client.get_schedule_handle(schedule_id)
    info = (await handle.describe()).info
    previous = info.num_actions
    # Temporal's schedule-generated workflow IDs include whole seconds. Give
    # separate test occurrences distinct schedule instants, avoiding an
    # intentional duplicate-trigger conflict rather than changing production IDs.
    if info.recent_actions:
        next_second = info.recent_actions[-1].scheduled_at.replace(
            microsecond=0
        ) + timedelta(seconds=1)
        await asyncio.sleep(
            max(0, (next_second - datetime.now(UTC)).total_seconds()) + 0.05
        )
    await handle.trigger(overlap=ScheduleOverlapPolicy.ALLOW_ALL)
    async with asyncio.timeout(30):
        while True:
            description = await handle.describe()
            if (
                description.info.num_actions > previous
                and description.info.recent_actions
            ):
                started = description.info.recent_actions[-1].action
                return client.get_workflow_handle(
                    started.workflow_id, run_id=started.first_execution_run_id
                )
            await asyncio.sleep(0.05)


@pytest.mark.usefixtures("launch_resolution_stages")
@pytest.mark.parametrize("selection", ["recorded", "pending", "not_applicable"])
async def test_schedule_admission_records_profile_and_preserves_history_4640(
    tmp_path, monkeypatch, selection: str
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schedules.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with await WorkflowEnvironment.start_local(
            dev_server_existing_path=shutil.which("temporal")
        ) as env, async_sessionmaker(engine, expire_on_commit=False)() as session:
            await register_deployment_search_attributes(env)
            client = env.client
            adapter = TemporalClientAdapter(client)
            # Route the real action to this isolated worker queue only.
            monkeypatch.setattr(
                adapter, "_get_task_queue", lambda *args, **kwargs: _LAUNCH_TASK_QUEUE
            )
            service = RecurringWorkflowsService(
                session, temporal_client_adapter=adapter
            )
            owner = uuid4()
            parameters: dict[str, Any] = {}
            if selection != "not_applicable":
                parameters["targetRuntime"] = "codex_cli"
            if selection == "recorded":
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id="scheduled-account",
                        runtime_id="codex_cli",
                        provider_id="openai",
                        account_label="Scheduled work",
                    )
                )
                await session.flush()
                parameters["profileId"] = "scheduled-account"
            definition = await service.create_definition(
                name="Recorded schedule",
                description=None,
                enabled=True,
                schedule_type="cron",
                cron="59 23 * * *",
                timezone="UTC",
                scope_type="personal",
                scope_ref=None,
                owner_user_id=owner,
                target={
                    "workflowType": "MoonMind.UserWorkflow",
                    "initialParameters": parameters,
                },
                policy={},
            )
            run = await _trigger(client, definition.temporal_schedule_id)
            description = await run.describe()
            summary = await description.memo_value(PROVIDER_PROFILE_MEMO_KEY)
            expected_profiles = (
                [{"id": "scheduled-account", "label": "Scheduled work"}]
                if selection == "recorded"
                else []
            )
            assert summary == {
                "selectionState": selection,
                "profiles": expected_profiles,
                "profileCount": len(expected_profiles),
            }
            expected_value = provider_profile_state_token(selection)
            if selection == "recorded":
                expected_value += " " + provider_profile_id_token("scheduled-account")
            key = SearchAttributeKey.for_text(PROVIDER_PROFILE_SEARCH_ATTRIBUTE)
            assert description.typed_search_attributes.get(key) == expected_value
            if selection != "pending":

                async def no_agent_launch(self, **kwargs):
                    return None

                monkeypatch.setattr(
                    MoonMindUserWorkflow, "_run_execution_stage", no_agent_launch
                )
            async with Worker(
                client,
                task_queue=_LAUNCH_TASK_QUEUE,
                workflows=[MoonMindUserWorkflow, _GrantedProfileAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                await run.result()
                history = await run.fetch_history()
            usable = await _detect_optional_temporal_search_attributes(client)
            filters = (
                [("providerProfileIn", "scheduled-account")]
                if selection == "recorded"
                else [("providerProfileStateIn", selection)]
            )
            if selection == "pending":
                filters = [("providerProfileIn", _GRANTED_PROFILE)]
            count_query, list_query = _production_query(
                filters, owner_id=str(owner), usable_search_attributes=usable
            )
            async with asyncio.timeout(30):
                while (await client.count_workflows(query=count_query)).count != 1:
                    await asyncio.sleep(0.1)
            assert [
                row.id async for row in client.list_workflows(query=list_query)
            ] == [run.id]

            if selection == "recorded":
                # Simulate a pre-upgrade action. Its already-started execution
                # stays historical; reconciliation repairs future starts only.
                workflow_type, workflow_input = service._workflow_bundle_for_definition(
                    definition
                )
                await adapter.update_schedule(
                    definition_id=definition.id,
                    workflow_type=workflow_type,
                    workflow_input=workflow_input,
                    memo={"definitionId": str(definition.id)},
                    search_attributes=service._owner_search_attributes(owner),
                )
                historical = await _trigger(client, definition.temporal_schedule_id)
                assert (
                    await (await historical.describe()).memo_value(
                        PROVIDER_PROFILE_MEMO_KEY, default=None
                    )
                    is None
                )
                await service._ensure_schedule_action_current(definition)
                repaired = await _trigger(client, definition.temporal_schedule_id)
                assert (
                    await (await repaired.describe()).memo_value(
                        PROVIDER_PROFILE_MEMO_KEY
                    )
                    == summary
                )
                assert (
                    await (await historical.describe()).memo_value(
                        PROVIDER_PROFILE_MEMO_KEY, default=None
                    )
                    is None
                )
                # Schedule edits can snapshot a new name without rewriting the
                # recorded identity of previous occurrences.
                profile = await session.get(
                    ManagedAgentProviderProfile, "scheduled-account"
                )
                profile.account_label = "Renamed scheduled work"
                await session.flush()
                await service.update_definition(definition, name="Updated schedule")
                renamed = await _trigger(client, definition.temporal_schedule_id)
                assert (
                    await (await renamed.describe()).memo_value(
                        PROVIDER_PROFILE_MEMO_KEY
                    )
                )["profiles"][0]["label"] == "Renamed scheduled work"
                assert (
                    await (await run.describe()).memo_value(PROVIDER_PROFILE_MEMO_KEY)
                    == summary
                )
            elif selection == "pending":
                # A real scheduled MoonMind.UserWorkflow can now merge the
                # granted child profile and replay the resulting history.
                recorded = await (await run.describe()).memo_value(
                    PROVIDER_PROFILE_MEMO_KEY
                )
                assert recorded["selectionState"] == "recorded"
                assert recorded["profiles"] == [
                    {"id": _GRANTED_PROFILE, "label": "Work"}
                ]
                await Replayer(
                    workflows=[MoonMindUserWorkflow],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(history)
    finally:
        await engine.dispose()
