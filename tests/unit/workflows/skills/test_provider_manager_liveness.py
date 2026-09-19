"""Unit coverage for the provider-manager liveness release gate.

Source: MoonLadderStudios/MoonMind#4363.

The evaluator is pure (no Temporal, no Postgres) so these tests run in the
fast unit suite. They pin the fail-closed contract: a running-but-unqueryable
singleton, a repeated nondeterminism failure loop, and a DB-vs-memory lease
disagreement each block promotion with precise evidence and the recovery
owner. Absence (not running) and agreement pass. The collector is covered
with hand-rolled fakes (no Temporal server) proving describe/query/history
observations feed the same verdicts.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.skills.provider_manager_liveness import (
    ManagerLivenessObservation,
    collect_provider_manager_liveness,
    describe_liveness_block,
    evaluate_provider_manager_liveness,
    observation_from_manager_state,
)


def _healthy_opencode() -> ManagerLivenessObservation:
    return observation_from_manager_state(
        workflow_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        run_id="run-healthy",
        running=True,
        status="RUNNING",
        inspection={
            "inspection_succeeded": True,
            "profiles": {
                "opencode-go-default": {
                    "current_leases": ["wf-1"],
                    "lease_metadata": {"wf-1": {"fencingGeneration": 1021}},
                }
            },
        },
        db_held_leases=1,
    )


def test_healthy_manager_with_reconciled_ledger_passes() -> None:
    disposition = evaluate_provider_manager_liveness([_healthy_opencode()])

    assert disposition["blocked"] is False
    assert disposition["reasonCode"] == "provider_manager_liveness_ok"
    assert disposition["reasons"] == []
    assert disposition["recoveryOwner"] is None


def test_running_but_unqueryable_manager_blocks_with_evidence() -> None:
    """The exact #4363 wedge signature: running, get_state RPC failing."""

    observation = observation_from_manager_state(
        workflow_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        run_id="run-wedged",
        running=True,
        status="RUNNING",
        inspection={
            "inspection_succeeded": False,
            "inspection_status": "RPC_ERROR_FAILED_PRECONDITION",
            "error": "Unable to query workflow due to Workflow Task in failed state.",
        },
        db_held_leases=0,
    )

    disposition = evaluate_provider_manager_liveness([observation])

    assert disposition["blocked"] is True
    assert disposition["reasonCode"] == "provider_manager_liveness_blocked"
    assert disposition["recoveryOwner"] == "update-moonmind"
    assert disposition["recoveryRunbook"] is not None
    assert disposition["recoveryHint"] is not None
    (reason,) = disposition["reasons"]
    assert "provider-profile-manager:opencode" in reason
    assert "RPC_ERROR_FAILED_PRECONDITION" in reason
    (entry,) = disposition["evidence"]
    assert entry["finding"] == "running_unqueryable"
    assert entry["run_id"] == "run-wedged"


def test_repeated_nondeterminism_failures_block() -> None:
    observation = _healthy_opencode()
    observation.nondeterminism_failures = 5
    observation.workflow_task_failures = 5

    disposition = evaluate_provider_manager_liveness([observation])

    assert disposition["blocked"] is True
    assert "nondeterminism" in disposition["reasons"][0].lower()
    assert disposition["evidence"][0]["finding"] == "nondeterminism_loop"


def test_sub_threshold_failures_do_not_block() -> None:
    observation = _healthy_opencode()
    observation.nondeterminism_failures = 2
    observation.workflow_task_failures = 2

    assert evaluate_provider_manager_liveness([observation])["blocked"] is False


def test_ledger_disagreement_blocks_with_both_counts() -> None:
    """DB held leases must reconcile with in-memory execution grants."""

    observation = _healthy_opencode()
    observation.db_held_leases = 0
    assert observation.memory_execution_grants == 1

    disposition = evaluate_provider_manager_liveness([observation])

    assert disposition["blocked"] is True
    (reason,) = disposition["reasons"]
    assert "0" in reason and "1" in reason
    assert disposition["evidence"][0]["finding"] == "ledger_disagreement"


def test_missing_db_read_is_not_a_disagreement() -> None:
    """An unreadable ledger records a gap; it must not read as zero held."""

    observation = _healthy_opencode()
    observation.db_held_leases = None

    assert evaluate_provider_manager_liveness([observation])["blocked"] is False


def test_stopped_manager_passes_with_start_on_demand_note() -> None:
    observation = ManagerLivenessObservation(
        workflow_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        running=False,
        status="TERMINATED",
        inspection_succeeded=False,
    )

    disposition = evaluate_provider_manager_liveness([observation])

    assert disposition["blocked"] is False
    assert disposition["evidence"][0]["finding"] == "not_running_starts_on_demand"


def test_empty_observation_set_passes_without_inventing_failure() -> None:
    disposition = evaluate_provider_manager_liveness([])

    assert disposition["blocked"] is False
    assert disposition["evidence"] == [{"finding": "no_manager_singletons_visible"}]


def test_block_description_names_every_reason() -> None:
    disposition = evaluate_provider_manager_liveness(
        [
            ManagerLivenessObservation(
                workflow_id="provider-profile-manager:opencode",
                runtime_id="opencode",
                running=True,
                status="RUNNING",
                inspection_succeeded=False,
                inspection_status="QUERY_TIMEOUT",
            ),
            ManagerLivenessObservation(
                workflow_id="provider-profile-manager:codex_cli",
                runtime_id="codex_cli",
                running=True,
                status="RUNNING",
                inspection_succeeded=True,
                nondeterminism_failures=4,
            ),
        ]
    )

    assert disposition["blocked"] is True
    rendered = describe_liveness_block(disposition)
    assert "provider-profile-manager:opencode" in rendered
    assert "provider-profile-manager:codex_cli" in rendered


def test_grant_and_fencing_totals_come_from_get_state() -> None:
    observation = observation_from_manager_state(
        workflow_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        run_id="run-1",
        running=True,
        status="RUNNING",
        inspection={
            "inspection_succeeded": True,
            "profiles": {
                "a": {
                    "current_leases": ["wf-1", "wf-2"],
                    "lease_metadata": {
                        "wf-1": {"fencingGeneration": 7},
                        "wf-2": {"fencingGeneration": 9},
                    },
                }
            },
        },
    )

    assert observation.memory_execution_grants == 2
    assert observation.fencing_generation == 9
    assert observation.inspection_succeeded is True


def test_unqueryable_observation_carries_no_invented_grants() -> None:
    observation = observation_from_manager_state(
        workflow_id="provider-profile-manager:opencode",
        runtime_id="opencode",
        run_id="run-1",
        running=True,
        status="RUNNING",
        inspection={"inspection_succeeded": False},
    )

    assert observation.memory_execution_grants is None
    assert observation.fencing_generation is None


class _HistoryEvent:
    """Minimal history-event double: failed tasks carry a cause name."""

    def __init__(self, failed: bool = False, nondeterministic: bool = False) -> None:
        self._failed = failed
        self._cause = (
            "WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR"
            if nondeterministic
            else "WORKFLOW_TASK_FAILED_CAUSE_UNHANDLED_COMMAND"
        )

    def HasField(self, name: str) -> bool:
        return self._failed and name == "workflow_task_failed_event_attributes"

    @property
    def workflow_task_failed_event_attributes(self) -> Any:
        if not self._failed:
            raise AttributeError("no failure attributes")
        return SimpleNamespace(cause=self._cause)


class _FakeHandle:
    def __init__(
        self,
        *,
        status: str = "RUNNING",
        run_id: str = "run-1",
        state: Any = None,
        query_error: Exception | None = None,
        history: list[Any] | None = None,
    ) -> None:
        self._status = status
        self._run_id = run_id
        self._state = state
        self._query_error = query_error
        self._history = history or []

    async def describe(self) -> Any:
        return SimpleNamespace(
            status=SimpleNamespace(name=self._status), run_id=self._run_id
        )

    async def query(self, query_type: str) -> Any:
        assert query_type == "get_state"
        if self._query_error is not None:
            raise self._query_error
        return self._state

    async def fetch_history_events(self, page_size: int = 0) -> Any:
        for event in self._history:
            yield event


class _FakeClient:
    def __init__(self, executions: list[Any], handles: dict[str, Any]) -> None:
        self._executions = executions
        self._handles = handles

    async def list_workflows(self, query: str = "") -> Any:
        assert "provider-profile-manager" in query
        for execution in self._executions:
            yield execution

    def get_workflow_handle(self, workflow_id: str) -> Any:
        return self._handles[workflow_id]


def _healthy_state() -> dict[str, Any]:
    return {
        "profiles": {
            "opencode-go-default": {
                "current_leases": ["wf-1"],
                "lease_metadata": {"wf-1": {"fencingGeneration": 4}},
            }
        },
    }


@pytest.mark.asyncio
async def test_collect_healthy_manager_reconciles_and_passes() -> None:
    client = _FakeClient(
        [SimpleNamespace(id="provider-profile-manager:opencode", run_id="run-1")],
        {
            "provider-profile-manager:opencode": _FakeHandle(
                state=_healthy_state(),
                history=[_HistoryEvent(), _HistoryEvent()],
            )
        },
    )

    observations = await collect_provider_manager_liveness(
        client, db_held_leases={"opencode": 1}
    )

    (observation,) = observations
    assert observation.running is True
    assert observation.inspection_succeeded is True
    assert observation.memory_execution_grants == 1
    assert observation.fencing_generation == 4
    assert observation.nondeterminism_failures == 0
    assert evaluate_provider_manager_liveness(observations)["blocked"] is False


@pytest.mark.asyncio
async def test_collect_nondeterminism_loop_blocks() -> None:
    client = _FakeClient(
        [SimpleNamespace(id="provider-profile-manager:opencode", run_id="run-x")],
        {
            "provider-profile-manager:opencode": _FakeHandle(
                run_id="run-x",
                state=_healthy_state(),
                history=[
                    _HistoryEvent(),
                    _HistoryEvent(failed=True, nondeterministic=True),
                    _HistoryEvent(failed=True, nondeterministic=True),
                    _HistoryEvent(failed=True, nondeterministic=True),
                ],
            )
        },
    )

    observations = await collect_provider_manager_liveness(client)

    (observation,) = observations
    assert observation.observed_from_history is True
    assert observation.nondeterminism_failures == 3
    disposition = evaluate_provider_manager_liveness(observations)
    assert disposition["blocked"] is True
    assert "provider-profile-manager:opencode" in disposition["reasons"][0]


@pytest.mark.asyncio
async def test_collect_unqueryable_manager_blocks() -> None:
    client = _FakeClient(
        [SimpleNamespace(id="provider-profile-manager:opencode", run_id="run-y")],
        {
            "provider-profile-manager:opencode": _FakeHandle(
                run_id="run-y",
                query_error=RuntimeError("Unable to query workflow"),
            )
        },
    )

    observations = await collect_provider_manager_liveness(client)

    (observation,) = observations
    assert observation.running is True
    assert observation.inspection_succeeded is False
    assert evaluate_provider_manager_liveness(observations)["blocked"] is True


@pytest.mark.asyncio
async def test_collect_stopped_manager_passes() -> None:
    client = _FakeClient(
        [SimpleNamespace(id="provider-profile-manager:opencode", run_id="run-z")],
        {"provider-profile-manager:opencode": _FakeHandle(status="TERMINATED")},
    )

    observations = await collect_provider_manager_liveness(client)

    assert evaluate_provider_manager_liveness(observations)["blocked"] is False


@pytest.mark.asyncio
async def test_stopped_manager_with_stale_failures_still_passes() -> None:
    """A closed run's history is not evidence against its successor."""

    client = _FakeClient(
        [SimpleNamespace(id="provider-profile-manager:opencode", run_id="run-z")],
        {
            "provider-profile-manager:opencode": _FakeHandle(
                status="TERMINATED",
                history=[
                    _HistoryEvent(failed=True, nondeterministic=True)
                    for _ in range(9)
                ],
            )
        },
    )

    observations = await collect_provider_manager_liveness(client)

    (observation,) = observations
    assert observation.nondeterminism_failures == 0
    disposition = evaluate_provider_manager_liveness(observations)
    assert disposition["blocked"] is False
    assert disposition["evidence"][0]["finding"] == "not_running_starts_on_demand"


@pytest.mark.asyncio
async def test_collect_describe_failure_is_unqueryable_evidence() -> None:
    class _UndescribedHandle(_FakeHandle):
        async def describe(self) -> Any:
            raise RuntimeError("not found")

    client = _FakeClient(
        [SimpleNamespace(id="provider-profile-manager:opencode", run_id="run-z")],
        {"provider-profile-manager:opencode": _UndescribedHandle()},
    )

    observations = await collect_provider_manager_liveness(client)

    (observation,) = observations
    assert observation.inspection_succeeded is False
    assert observation.inspection_status == "DESCRIBE_FAILED"
