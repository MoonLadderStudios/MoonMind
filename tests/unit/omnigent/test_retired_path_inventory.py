"""MoonLadderStudios/MoonMind#5343: read-only retired-path drain inventory."""

import json

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode
from typer.testing import CliRunner

from moonmind import cli
from moonmind.omnigent import retired_path_inventory as inventory
from moonmind.omnigent.retired_path_inventory import (
    RETIRED_PATH_CATEGORIES,
    RecordedState,
    collect_retired_path_report,
    open_execution,
    recorded_path_items,
)

PROFILE_BOUND = "codex-profile-bound@1"
OPENCODE_V1 = "omnigent-opencode@1"


def _plan(realizer="codex-native@1", host_class="omnigent-codex@2"):
    return {"realizer": realizer, "hostClass": host_class}


def _state(*, plans, holders, executions, recorded=()):
    return RecordedState(
        plans=plans,
        plan_workflow_ids={ref: frozenset(holders.get(ref, ())) for ref in plans},
        open_executions={item.workflow_id: item for item in executions},
        recorded_workflow_ids=frozenset(recorded)
        | frozenset(item.workflow_id for item in executions),
    )


def test_recorded_items_classify_each_retired_plan_start_retry_and_request():
    started = open_execution(
        "wf-started", {"temporalStart": {"state": "confirmed"}}, {}
    )
    queued = open_execution(
        "wf-queued",
        {"temporalStart": {"state": "pending"}},
        {"omnigentExecutionPlan": {"planRef": "plan-queued"}},
    )
    requested = open_execution(
        "wf-host-class", {}, {"workflow": {"omnigent": {"hostClassRef": OPENCODE_V1}}}
    )
    # A current opencode execution target with the same text is not a request
    # for the retired Host Class.
    target_only = open_execution(
        "wf-target", {}, {"agentProfile": {"executionTargetRef": OPENCODE_V1}}
    )
    state = _state(
        plans={
            "plan-open": _plan(realizer=PROFILE_BOUND),
            "plan-queued": _plan(realizer=PROFILE_BOUND),
            "plan-closed": _plan(realizer=PROFILE_BOUND),
            "plan-unresolved": _plan(realizer=PROFILE_BOUND),
            "plan-opencode": _plan(host_class=OPENCODE_V1),
        },
        holders={
            "plan-open": {"wf-started"},
            "plan-queued": {"wf-queued"},
            "plan-closed": {"wf-closed"},
            "plan-unresolved": {"wf-elsewhere"},
        },
        executions=[started, queued, requested, target_only],
        recorded={"wf-closed"},
    )

    items = recorded_path_items(state, {"wf-elsewhere": "unknown"})

    assert {item["id"]: item["state"] for item in items["profile_bound_plans"]} == {
        "plan-closed": "clean",
        "plan-open": "active",
        "plan-queued": "active",
        "plan-unresolved": "unknown",
    }
    unresolved = next(
        item for item in items["profile_bound_plans"] if item["id"] == "plan-unresolved"
    )
    assert unresolved["unresolvedWorkflowIds"] == ["wf-elsewhere"]
    assert items["profile_bound_queued_starts"] == [
        {"id": "wf-queued", "state": "active", "planRefs": ["plan-queued"]}
    ]
    assert items["profile_bound_retries"] == [
        {"id": "wf-started", "state": "active", "planRefs": ["plan-open"]}
    ]
    assert items["opencode_v1_plans"] == [
        {"id": "plan-opencode", "state": "clean", "openWorkflowIds": []}
    ]
    assert items["opencode_v1_host_class_requests"] == [
        {"id": "wf-host-class", "state": "active", "phase": "started"}
    ]


def test_plan_held_only_by_a_running_temporal_workflow_is_an_active_retry():
    state = _state(
        plans={"plan-a": _plan(realizer=PROFILE_BOUND)},
        holders={"plan-a": {"wf-child"}},
        executions=[],
    )
    assert state.unrecorded_workflow_ids() == ["wf-child"]

    items = recorded_path_items(state, {"wf-child": "open"})

    assert items["profile_bound_plans"][0]["state"] == "active"
    assert items["profile_bound_retries"] == [
        {"id": "wf-child", "state": "active", "planRefs": ["plan-a"]}
    ]
    closed = recorded_path_items(state, {"wf-child": "closed"})
    assert closed["profile_bound_plans"][0]["state"] == "clean"
    assert closed["profile_bound_retries"] == []


class _Execution:
    def __init__(self, workflow_id, workflow_type, task_queue, parent_id=None):
        self.id = workflow_id
        self.workflow_type = workflow_type
        self.task_queue = task_queue
        self.parent_id = parent_id


class _Handle:
    def __init__(self, outcome):
        self._outcome = outcome

    async def describe(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return type("Description", (), {"status": self._outcome})()


class _Client:
    def __init__(self, executions, describe=None):
        self._executions = executions
        self._describe = describe or {}
        self.queries = []

    def list_workflows(self, query):
        self.queries.append(query)

        async def matching():
            for execution in self._executions:
                if (
                    f'WorkflowType="{execution.workflow_type}"' in query
                    or f'TaskQueue="{execution.task_queue}"' in query
                ):
                    yield execution

        return matching()

    def get_workflow_handle(self, workflow_id):
        return _Handle(self._describe[workflow_id])


class _Engine:
    disposed = False

    async def dispose(self):
        self.disposed = True


async def _report(monkeypatch, recorded, client, engine=None):
    engine = engine or _Engine()

    async def read(_engine):
        if isinstance(recorded, Exception):
            raise recorded
        return recorded

    async def connect():
        if isinstance(client, Exception):
            raise client
        return client

    monkeypatch.setattr(inventory, "read_recorded_state", read)
    report = await collect_retired_path_report(
        engine_factory=lambda: engine,
        temporal_connect=connect,
        legacy_task_queue="mm.workflow",
    )
    return report, engine


@pytest.mark.asyncio
async def test_empty_deployment_is_clean_and_agent_sessions_are_informational(
    monkeypatch,
):
    client = _Client(
        [
            _Execution(
                "session-1", "MoonMind.AgentSession", "mm.workflow.user.v2", "run-1"
            )
        ]
    )
    report, engine = await _report(
        monkeypatch, _state(plans={}, holders={}, executions=[]), client
    )

    assert engine.disposed is True
    assert report["categories"] == list(RETIRED_PATH_CATEGORIES)
    assert report["deletable"] is True
    assert report["states"] == {"clean": 0, "active": 0, "unknown": 0, "blocked": 0}
    assert report["informationalStates"]["active"] == 1
    assert report["items"]["agent_session_children"] == [
        {
            "id": "session-1",
            "state": "active",
            "workflowType": "MoonMind.AgentSession",
            "taskQueue": "mm.workflow.user.v2",
            "parentWorkflowId": "run-1",
        }
    ]
    assert all(
        query.startswith('ExecutionStatus="Running" AND ') for query in client.queries
    )


@pytest.mark.asyncio
async def test_open_legacy_queue_workflow_and_unrecorded_holders_gate_the_drain(
    monkeypatch,
):
    state = _state(
        plans={
            "plan-gone": _plan(realizer=PROFILE_BOUND),
            "plan-running": _plan(realizer=PROFILE_BOUND),
        },
        holders={"plan-gone": {"wf-gone"}, "plan-running": {"wf-running"}},
        executions=[],
    )
    client = _Client(
        [_Execution("legacy-1", "MoonMind.Run", "mm.workflow")],
        describe={
            "wf-gone": RPCError("gone", RPCStatusCode.NOT_FOUND, b""),
            "wf-running": WorkflowExecutionStatus.RUNNING,
        },
    )
    report, _ = await _report(monkeypatch, state, client)

    assert report["deletable"] is False
    assert report["items"]["legacy_task_queue_workflows"][0]["id"] == "legacy-1"
    assert {
        item["id"]: item["state"] for item in report["items"]["profile_bound_plans"]
    } == {
        "plan-gone": "clean",
        "plan-running": "active",
    }
    assert report["items"]["profile_bound_retries"][0]["id"] == "wf-running"


@pytest.mark.asyncio
async def test_unreachable_sources_report_unknown_never_clean(monkeypatch):
    report, _ = await _report(
        monkeypatch,
        ConnectionRefusedError("postgres refused"),
        OSError("temporal refused"),
    )

    assert report["deletable"] is False
    for category in RETIRED_PATH_CATEGORIES:
        [item] = report["items"][category]
        assert item["state"] == "unknown"
        assert "refused" in item["error"]
    assert report["states"]["unknown"] == len(RETIRED_PATH_CATEGORIES) - 1
    assert report["informationalStates"]["unknown"] == 1

    # A plan holder Temporal cannot confirm keeps its plan unknown.
    state = _state(
        plans={"plan-a": _plan(realizer=PROFILE_BOUND)},
        holders={"plan-a": {"wf-a"}},
        executions=[],
    )
    report, _ = await _report(monkeypatch, state, OSError("temporal refused"))
    assert report["items"]["profile_bound_plans"][0]["state"] == "unknown"
    assert report["deletable"] is False


@pytest.mark.asyncio
async def test_failed_engine_creation_reports_recorded_categories_unknown(monkeypatch):
    def broken_engine():
        raise ValueError("unsupported database url")

    report = await collect_retired_path_report(
        engine_factory=broken_engine,
        temporal_connect=lambda: _async_value(_Client([])),
        legacy_task_queue="mm.workflow",
    )

    assert report["items"]["profile_bound_plans"][0]["state"] == "unknown"
    assert report["items"]["legacy_task_queue_workflows"] == []
    assert report["deletable"] is False


async def _async_value(value):
    return value


@pytest.mark.parametrize("deletable, exit_code", [(True, 0), (False, 1)])
def test_cli_prints_the_report_and_exits_nonzero_unless_clean(
    monkeypatch, deletable, exit_code
):
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return {"deletable": deletable, "items": {}}

    monkeypatch.setattr(inventory, "run_retired_path_inventory", fake_run)

    result = CliRunner().invoke(
        cli.app,
        [
            "drain-inventory",
            "--database-url",
            "postgresql+asyncpg://db/moonmind",
            "--temporal-address",
            "temporal:7233",
        ],
    )

    assert result.exit_code == exit_code, result.output
    assert json.loads(result.output) == {"deletable": deletable, "items": {}}
    assert calls[0]["database_url"] == "postgresql+asyncpg://db/moonmind"
    assert calls[0]["temporal_address"] == "temporal:7233"
    assert calls[0]["legacy_task_queue"]
