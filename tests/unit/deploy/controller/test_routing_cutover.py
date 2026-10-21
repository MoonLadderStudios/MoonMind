"""One-time unversioned routing cutover owned by the controller operation."""

import json

import pytest
from conftest import load

NEW_ID = "sha256:new"
SERVICES = ["api", "temporal-worker-workflow"]


class ControllerLost(BaseException):
    """The controller process dies; nothing after this point runs."""


class Runner:
    """Docker/Compose double: worker inventory plus ordinary apply commands."""

    def __init__(self, workers):
        self.workers = workers
        self.commands = []

    def run(self, args, timeout_seconds):
        args = tuple(args)
        self.commands.append(args)
        if args[:2] == ("docker", "ps"):
            if "--all" in args:
                return {"exit": 0, "output": ""}  # No journal consumers.
            if "--quiet" in args:
                return {
                    "exit": 0,
                    "output": "\n".join(str(i) for i in range(len(self.workers))),
                }
            return {"exit": 0, "output": ""}  # No earlier one-off still running.
        if args[:3] == ("docker", "image", "inspect"):
            return {"exit": 0, "output": NEW_ID}
        if args[:2] == ("docker", "inspect"):
            return {
                "exit": 0,
                "output": "\n".join(
                    json.dumps(
                        {
                            "Image": image,
                            "Worker": service.startswith("temporal-worker"),
                            "Service": service,
                            "OneOff": "",
                        }
                    )
                    for service, image in self.workers
                ),
            }
        if "ps" in args:
            return {
                "exit": 0,
                "output": "\n".join(
                    json.dumps({"Service": name, "State": "running"})
                    for name in SERVICES
                ),
            }
        if "exec" in args:
            return {
                "exit": 0,
                "output": "MOONMIND_SINGLE_USER_CONVERSION="
                + json.dumps({"published": True, "disposition": "fresh_init"}),
            }
        return {"exit": 0, "output": "ok"}

    def applied(self):
        return [command for command in self.commands if "up" in command]


@pytest.fixture()
def controller(controller_path, tmp_path, monkeypatch):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    helper_calls = []
    monkeypatch.setattr(server, "ROUTING_CUTOVER_RETRY_SECONDS", 0)

    def install(runner, helper):
        monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)

        def run_step(store, operation, **kwargs):
            helper_calls.append(kwargs["phase"])
            return helper(kwargs["phase"], operation)

        monkeypatch.setattr(server, "run_omnigent_step", run_step)

    def begin(image="ghcr.io/org/app@sha256:abc"):
        return store.begin(
            stack="moonmind",
            desired_image=image,
            source_revision="abc123",
            target={
                "project": "moonmind",
                "projectDir": str(tmp_path),
                "composeFiles": ["docker-compose.yaml"],
                "services": SERVICES,
            },
        )

    def apply(operation):
        return server._apply_with_bounded_retries(
            store,
            operation["operationId"],
            lambda current: server.production_apply(store, current),
        )

    return server, store, install, begin, apply, helper_calls


def _check(store, operation):
    checks = {
        check["name"]: check
        for check in store.load(operation["operationId"])["verification"]
    }
    return checks.get("temporal-unversioned-cutover")


def test_cutover_runs_once_after_workers_poll_and_later_updates_deny_routing_changes(
    controller,
):
    server, store, install, begin, apply, helper_calls = controller
    runner = Runner([("temporal-worker-workflow", NEW_ID)])
    first = begin()

    def helper(phase, operation):
        assert phase == "routing-cutover-apply"
        # Ordinary workers already poll, and the operation is still open, so
        # a lost controller resumes this same operation.
        assert runner.applied()
        current = store.load(operation["operationId"])
        assert current["status"] == "applying"
        assert current["installed"]["image"] == operation["desired"]["image"]
        return {
            "status": "completed",
            "outcome": "transitioned",
            "retiredCanaries": [{"workflowId": "mm-release-canary-1"}],
        }

    install(runner, helper)
    assert apply(first)["status"] == "succeeded"
    assert store.load(first["operationId"])["routingCutover"]["status"] == "completed"
    assert _check(store, first)["status"] == "passed"
    assert "retired 1 release canaries" in _check(store, first)["detail"]

    later = begin("ghcr.io/org/app@sha256:def")
    install(
        Runner([("temporal-worker-workflow", NEW_ID)]),
        lambda phase, operation: {"status": "supported"},
    )
    helper_calls.clear()
    assert apply(later)["status"] == "succeeded"
    # Only the rollback-floor probe runs; routing is never touched again.
    assert helper_calls == ["routing-cutover-probe"]
    assert "routingCutover" not in store.load(later["operationId"])
    assert first["operationId"] in _check(store, later)["detail"]


def test_rollback_floor_refuses_a_versioning_image_before_containers_change(controller):
    server, store, install, begin, apply, helper_calls = controller
    done = begin()
    store.record_routing_cutover(done["operationId"], {"status": "completed"})
    store.confirm_installed(done["operationId"], image=done["desired"]["image"])
    rollback = begin("ghcr.io/org/app@sha256:older")
    runner = Runner([("temporal-worker-workflow", NEW_ID)])
    install(runner, lambda phase, operation: {"status": "unsupported"})
    with pytest.raises(server.engine.ApplyError):
        apply(rollback)
    result = store.load(rollback["operationId"])
    assert result["status"] == "failed"
    assert "Worker Deployment routing writers" in result["errorSummary"]
    assert runner.applied() == []
    assert set(helper_calls) == {"routing-cutover-probe"}


def test_rollback_floor_holds_once_routing_is_cleared_with_work_left_to_report(
    controller,
):
    server, store, install, begin, apply, helper_calls = controller
    cleared = begin()
    install(
        Runner([("temporal-worker-workflow", NEW_ID)]),
        lambda phase, operation: {
            "status": "attention",
            "outcome": "transitioned",
            "preservedPinned": [{"workflowId": "pinned-user"}],
        },
    )
    assert apply(cleared)["status"] == "partially_verified"
    rollback = begin("ghcr.io/org/app@sha256:older")
    runner = Runner([("temporal-worker-workflow", NEW_ID)])
    install(runner, lambda phase, operation: {"status": "unsupported"})
    with pytest.raises(server.engine.ApplyError):
        apply(rollback)
    assert "Worker Deployment routing writers" in (
        store.load(rollback["operationId"])["errorSummary"]
    )
    assert runner.applied() == []


def test_transient_cutover_failure_is_retried_within_the_same_operation(controller):
    server, store, install, begin, apply, helper_calls = controller
    operation = begin()
    receipts = [
        {"status": "pending", "outcome": "observation_unavailable", "detail": "down"},
        {"status": "pending", "outcome": "missing_queue", "detail": "mm.workflow"},
        {"status": "completed", "outcome": "transitioned"},
    ]
    install(
        Runner([("temporal-worker-workflow", NEW_ID)]),
        lambda phase, current: receipts.pop(0),
    )
    result = apply(operation)
    assert result["status"] == "succeeded"
    assert result["routingCutover"]["status"] == "completed"
    assert helper_calls == ["routing-cutover-apply"] * 3


def test_persistent_cutover_failure_stops_after_bounded_attempts(controller):
    server, store, install, begin, apply, helper_calls = controller
    operation = begin()
    install(
        Runner([("temporal-worker-workflow", NEW_ID)]),
        lambda phase, current: {
            "status": "pending",
            "outcome": "observation_unavailable",
            "detail": "Temporal is unavailable",
        },
    )
    result = apply(operation)
    assert result["status"] == "partially_verified"
    assert helper_calls == ["routing-cutover-apply"] * server.ROUTING_CUTOVER_ATTEMPTS
    assert result["routingCutover"]["attempts"] == server.ROUTING_CUTOVER_ATTEMPTS
    assert _check(store, operation)["status"] == "unavailable"


@pytest.mark.parametrize("effect_applied", [False, True])
def test_controller_loss_around_the_routing_effect_converges_through_one_operation(
    controller, effect_applied
):
    server, store, install, begin, apply, helper_calls = controller
    operation = begin()
    routing = {"current": "moonmind-workflow-fleet.old"}

    def helper(phase, current):
        if not helper_calls[:-1]:
            if effect_applied:
                routing["current"] = ""
            raise ControllerLost()
        # The resumed step rereads actual routing instead of repeating it.
        outcome = "already_unversioned" if not routing["current"] else "transitioned"
        routing["current"] = ""
        return {"status": "completed", "outcome": outcome}

    install(Runner([("temporal-worker-workflow", NEW_ID)]), helper)
    with pytest.raises(ControllerLost):
        apply(operation)
    assert store.load(operation["operationId"])["status"] in ("staged", "applying")

    server.converge_on_restart(
        store, lambda current: server.production_apply(store, current)
    )
    resumed = store.load(operation["operationId"])
    assert resumed["status"] == "succeeded"
    assert resumed["routingCutover"]["outcome"] == (
        "already_unversioned" if effect_applied else "transitioned"
    )
    assert helper_calls == ["routing-cutover-apply", "routing-cutover-apply"]
    assert [path.name for path in store.operations_dir.glob("*.json")] == [
        f"{operation['operationId']}.json"
    ]


def test_older_image_worker_defers_the_cutover_without_blocking_repair(controller):
    server, store, install, begin, apply, helper_calls = controller
    operation = begin()
    install(
        Runner(
            [
                ("temporal-worker-workflow", NEW_ID),
                ("temporal-worker-artifacts", "sha256:old"),
            ]
        ),
        lambda phase, current: pytest.fail("a legacy routing writer is still running"),
    )
    result = apply(operation)
    assert result["status"] == "partially_verified"
    assert result["installed"]["image"] == operation["desired"]["image"]
    state = result["routingCutover"]
    assert (state["status"], state["outcome"]) == ("pending", "legacy_writers")
    assert "temporal-worker-artifacts" in _check(store, operation)["detail"]
    assert _check(store, operation)["status"] == "failed"


@pytest.mark.parametrize(
    "receipt,check_status",
    [
        (
            {
                "status": "pending",
                "outcome": "observation_unavailable",
                "detail": "Temporal is unavailable",
            },
            "unavailable",
        ),
        (
            {
                "status": "pending",
                "outcome": "missing_queue",
                "detail": "no live ordinary poller on mm.workflow (workflow)",
            },
            "failed",
        ),
        (
            {
                "status": "attention",
                "outcome": "transitioned",
                "preservedPinned": [{"workflowId": "pinned-user"}],
            },
            "failed",
        ),
    ],
)
def test_unfinished_cutover_stays_explicit_and_runs_again_next_update(
    controller, receipt, check_status
):
    server, store, install, begin, apply, helper_calls = controller
    first = begin()
    install(Runner([("temporal-worker-workflow", NEW_ID)]), lambda phase, op: receipt)
    result = apply(first)
    assert result["status"] == "partially_verified"
    assert result["installed"] is not None
    assert _check(store, first)["status"] == check_status
    if receipt["status"] == "attention":
        assert "pinned-user" in _check(store, first)["detail"]
        assert "update-options" in _check(store, first)["detail"]
    later = begin("ghcr.io/org/app@sha256:def")
    install(
        Runner([("temporal-worker-workflow", NEW_ID)]),
        lambda phase, op: (
            {"status": "supported"}
            if phase == "routing-cutover-probe"
            else {"status": "completed", "outcome": "already_unversioned"}
        ),
    )
    helper_calls.clear()
    assert apply(later)["status"] == "succeeded"
    # Not completed earlier, so the cutover reruns. Cleared routing (attention)
    # already holds the rollback floor; a pending cutover does not.
    floor = ["routing-cutover-probe"] if receipt["status"] == "attention" else []
    assert helper_calls == [*floor, "routing-cutover-apply"]


def test_selected_image_that_still_versions_workers_needs_no_cutover(controller):
    server, store, install, begin, apply, helper_calls = controller
    operation = begin()
    install(
        Runner([("temporal-worker-workflow", NEW_ID)]),
        lambda phase, current: {"status": "unsupported"},
    )
    result = apply(operation)
    assert result["status"] == "succeeded"
    assert "routingCutover" not in result
    assert _check(store, operation) is None
