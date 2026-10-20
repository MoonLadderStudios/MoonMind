"""Exercise admission across the real recurring service and Temporal adapter."""

from copy import copy, deepcopy
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.api.common.v1 import Payload
from temporalio.client import ScheduleActionStartWorkflow

from api_service.db.models import Base
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.config.settings import settings
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER

pytestmark = pytest.mark.asyncio
MARKER = "linked_recovery_retired"


class ScheduleSDK:
    """Store real SDK Schedules and execute the adapter's real update callback."""

    data_converter = MOONMIND_TEMPORAL_DATA_CONVERTER
    namespace = "schedule-regression"

    def __init__(self):
        self.schedules = {}
        self.updates = 0
        self.creates = 0
        self.hide_once = False
        self.before_update = None

    async def create_schedule(self, schedule_id, schedule):
        self.creates += 1
        if schedule_id in self.schedules:
            raise RuntimeError("already exists")
        self.schedules[schedule_id] = deepcopy(schedule)
        return self.get_schedule_handle(schedule_id)

    def get_schedule_handle(self, schedule_id):
        sdk = self

        class Handle:
            id = schedule_id

            async def describe(self):
                if sdk.hide_once:
                    sdk.hide_once = False
                    raise RuntimeError("not found")
                if schedule_id not in sdk.schedules:
                    raise RuntimeError("not found")
                schedule = deepcopy(sdk.schedules[schedule_id])
                if schedule.action is not None:
                    proto = await schedule.action._to_proto(sdk)
                    schedule.action = ScheduleActionStartWorkflow._from_proto(
                        proto.start_workflow
                    )
                return SimpleNamespace(
                    schedule=schedule,
                    info=SimpleNamespace(next_action_times=[], recent_actions=[]),
                )

            async def update(self, callback):
                if sdk.before_update is not None:
                    sdk.before_update()
                    sdk.before_update = None
                update = await callback(
                    SimpleNamespace(description=await self.describe())
                )
                schedule = copy(update.schedule)
                schedule.action = copy(schedule.action)
                # Keep test storage decoded for intentional drift setup; every
                # SDK read above returns real protobuf-backed action values.
                if schedule.action is not None:
                    schedule.action.args = [
                        (await sdk.data_converter.decode([value]))[0]
                        if isinstance(value, Payload)
                        else value
                        for value in schedule.action.args
                    ]
                    schedule.action.memo = {
                        key: (await sdk.data_converter.decode([value]))[0]
                        if isinstance(value, Payload)
                        else value
                        for key, value in (schedule.action.memo or {}).items()
                    }
                sdk.schedules[schedule_id] = schedule
                sdk.updates += 1

            async def pause(self):
                sdk.schedules[schedule_id].state.paused = True

            async def unpause(self):
                sdk.schedules[schedule_id].state.paused = False

        return Handle()


@pytest_asyncio.fixture
async def chain(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    monkeypatch.setattr(settings.workflow, "git_user_name", "Schedule operator")
    monkeypatch.setattr(settings.workflow, "git_user_email", "schedule@example.invalid")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schedules.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sdk = ScheduleSDK()
    async with maker() as session:
        service = RecurringWorkflowsService(
            session, temporal_client_adapter=TemporalClientAdapter(client=sdk)
        )
        definition = await service.create_definition(
            name="Scheduled work",
            description="Original description",
            enabled=True,
            schedule_type="cron",
            cron="0 6 * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=None,
            target={
                "workflowType": "MoonMind.UserWorkflow",
                "initialParameters": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "targetRuntime": "codex",
                    "task": {
                        "instructions": "Inspect repository",
                        "publish": {"mode": "none"},
                    },
                },
            },
            policy={},
        )
        yield service, definition, sdk
    await engine.dispose()


def action(chain):
    _, definition, sdk = chain
    return sdk.schedules[definition.temporal_schedule_id].action


def legacy(chain):
    action(chain).args[0].pop(MARKER)


async def test_new_schedule_reconciliation_and_ensure_are_idempotent(chain):
    service, definition, sdk = chain
    assert action(chain).args[0][MARKER] is True
    assert await service.reconcile_schedules() == 0
    assert await service.reconcile_schedules() == 0
    await service._ensure_schedule_action_current(definition)
    assert sdk.updates == 0


@pytest.mark.parametrize(
    "edit",
    [
        {"name": "Renamed work"},
        {"description": "New description"},
        {"cron": "0 7 * * *"},
        {"timezone": "America/New_York"},
        {"policy": {"jitterSeconds": 30}},
        {"enabled": False},
        {"enabled": True},
        {"target": "equivalent"},
    ],
)
async def test_metadata_and_equivalent_target_edits_keep_legacy_admission(chain, edit):
    service, definition, sdk = chain
    legacy(chain)
    edit = deepcopy(edit)
    if edit.get("target") == "equivalent":
        edit["target"] = deepcopy(definition.target)
    await service.update_definition(definition, **edit)
    assert MARKER not in action(chain).args[0]
    # A name change also changes the generated workflow title. Repair that
    # projection on the next sweep without turning the legacy action fresh.
    await service.reconcile_schedules()
    assert MARKER not in action(chain).args[0]
    writes = sdk.updates
    assert await service.reconcile_schedules() == 0
    await service._ensure_schedule_action_current(definition)
    assert sdk.updates == writes
    assert MARKER not in action(chain).args[0]


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("drift", ["input", "queue", "id", "projection", "nested_marker"])
@pytest.mark.parametrize("repair", ["reconcile", "ensure"])
async def test_real_action_drift_repaired_once_without_changing_admission(
    chain, admitted, drift, repair
):
    service, definition, sdk = chain
    if not admitted:
        legacy(chain)
    expected = deepcopy(action(chain))
    current = action(chain)
    if drift == "input":
        current.args[0]["initial_parameters"]["task"]["instructions"] = "Stale instructions"
    elif drift == "queue":
        current.task_queue = "stale-queue"
    elif drift == "id":
        current.id = "stale-id"
    elif drift == "projection":
        current.memo = {}
    else:
        current.args[0]["initial_parameters"][MARKER] = True
    if repair == "reconcile":
        assert await service.reconcile_schedules() == 1
    else:
        await service._ensure_schedule_action_current(definition)
    assert sdk.updates == 1
    repaired = action(chain)
    assert repaired.args == expected.args
    assert repaired.id == expected.id
    assert repaired.task_queue == expected.task_queue
    assert repaired.memo == expected.memo
    assert await service.reconcile_schedules() == 0
    await service._ensure_schedule_action_current(definition)
    assert sdk.updates == 1


async def test_explicit_target_replacement_is_fresh_admission(chain):
    service, definition, _ = chain
    legacy(chain)
    target = deepcopy(definition.target)
    target["initialParameters"]["task"]["instructions"] = "Different requested work"
    await service.update_definition(definition, target=target)
    assert action(chain).args[0][MARKER] is True
    assert (
        action(chain).args[0]["initial_parameters"]["task"]["instructions"]
        == "Different requested work"
    )
    assert await service.reconcile_schedules() == 0


@pytest.mark.parametrize("missing", ["action", "schedule"])
async def test_missing_action_or_schedule_is_fresh_admission(chain, missing):
    service, definition, sdk = chain
    if missing == "action":
        sdk.schedules[definition.temporal_schedule_id].action = None
    else:
        del sdk.schedules[definition.temporal_schedule_id]
    assert await service.reconcile_schedules() == 1
    assert action(chain).args[0][MARKER] is True
    assert await service.reconcile_schedules() == 0


async def test_recreate_already_exists_race_preserves_legacy_action(chain):
    service, definition, sdk = chain
    legacy(chain)
    sdk.hide_once = True
    assert await service.reconcile_schedules() == 1
    assert sdk.creates == 2
    assert sdk.updates == 1
    assert MARKER not in action(chain).args[0]
    assert await service.reconcile_schedules() == 0
    await service._ensure_schedule_action_current(definition)
    assert sdk.updates == 1


async def test_repaired_legacy_schedule_still_selects_legacy_workflow_mode(
    chain, monkeypatch
):
    from temporalio import workflow
    from temporalio.exceptions import ApplicationError

    from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

    service, definition, _ = chain
    legacy(chain)
    await service.update_definition(definition, name="Renamed legacy work")
    await service.reconcile_schedules()
    payload = deepcopy(action(chain).args[0])
    instance = MoonMindUserWorkflow()
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(continued_run_id=None))
    monkeypatch.setattr(workflow, "patched", lambda _: True)

    def stop_after_decision(_payload):
        raise ValueError("stop after retirement decision")

    monkeypatch.setattr(instance, "_initialize_from_payload", stop_after_decision)
    with pytest.raises(ApplicationError, match="stop after retirement decision"):
        await instance.run(payload)
    assert instance._linked_recovery_retired is False


async def test_explicit_false_admission_survives_automatic_repair(chain):
    service, definition, sdk = chain
    action(chain).args[0][MARKER] = False
    action(chain).task_queue = "stale-queue"
    await service._ensure_schedule_action_current(definition)
    assert action(chain).args[0][MARKER] is False
    assert await service.reconcile_schedules() == 0
    assert sdk.updates == 1


@pytest.mark.parametrize("new_admission", [False, True])
async def test_repair_uses_callback_admission_after_concurrent_action_change(
    chain, new_admission
):
    service, definition, sdk = chain
    if new_admission:
        legacy(chain)
    action(chain).task_queue = "stale-queue"

    def replace_admission():
        if new_admission:
            action(chain).args[0][MARKER] = True
        else:
            action(chain).args[0].pop(MARKER)

    sdk.before_update = replace_admission
    await service._ensure_schedule_action_current(definition)
    assert (action(chain).args[0].get(MARKER) is True) is new_admission
    assert (MARKER in action(chain).args[0]) is new_admission
    assert await service.reconcile_schedules() == 0
    assert sdk.updates == 1
