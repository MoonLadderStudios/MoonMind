"""Unit tests for release-routing stewardship (plain restarts must converge)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.service import RPCError, RPCStatusCode

from moonmind.workflows.temporal.release_routing import bootstrap_version_routing


def _install_controlled_routing_clock(monkeypatch, *, timeout_seconds, poll_seconds):
    """Narrowly scoped controlled time for stewardship re-verification.

    Production ``_await_no_live_pollers`` waits on ``time.monotonic()`` plus
    ``asyncio.sleep(_ROUTE_DEATH_POLL_SECONDS)`` with 120s/10s windows
    (MoonLadderStudios/MoonMind#4374). Tests must neither repeat that real
    wait nor busy-spin on a real monotonic deadline behind a global no-op
    sleep. This helper shortens the window for the test and drives a fake
    clock forward on each sleep, so the loop terminates in milliseconds
    with no real-time wait and no busy loop. Scoped to the test via
    monkeypatch; production poller-freshness/recovery windows are untouched.
    """
    from moonmind.workflows.temporal import release_routing

    clock = {"now": 1000.0}

    async def _advancing_sleep(delay=None):
        clock["now"] += float(delay) if delay else float(poll_seconds)
        return None

    monkeypatch.setattr(
        release_routing, "_ROUTE_DEATH_TIMEOUT_SECONDS", timeout_seconds
    )
    monkeypatch.setattr(release_routing, "_ROUTE_DEATH_POLL_SECONDS", poll_seconds)
    monkeypatch.setattr(release_routing.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(release_routing.asyncio, "sleep", _advancing_sleep)


def _install_bounded_routing_sleep(monkeypatch):
    """Module-scoped no-op sleep for bounded bootstrap retry loops.

    These paths retry a fixed number of in-memory fake-server observations
    (no ``time.monotonic()`` deadline), so a non-advancing sleep completes
    in milliseconds without a busy loop. Patched on the routing module's
    ``asyncio`` reference and reverted by monkeypatch; production code is
    untouched.
    """
    from moonmind.workflows.temporal import release_routing

    async def _no_sleep(delay=None):
        return None

    monkeypatch.setattr(release_routing.asyncio, "sleep", _no_sleep)



def _not_found():
    return RPCError("missing", RPCStatusCode.NOT_FOUND, None)


class _FakeServer:
    """In-memory worker-deployment routing with CAS promotion."""

    def __init__(self):
        self.deployment = "moonmind-workflow-fleet"
        self.current = ""
        self.conflict_token = b"token-0"
        self.versions = {}
        self.pollers = {}
        self.set_current_calls = []
        self.canaries_started = []
        self.on_canary_start = None
        self.canary_result = None
        self.fail_ordinary_canaries = False

    def add_version(self, build, *, queues=()):
        version = f"{self.deployment}.{build}"
        self.versions[version] = {"queues": set(queues)}
        return version

    def add_poller(
        self, build, queue, kind, *, identity="worker@abc123", live_for_calls=None
    ):
        version = f"{self.deployment}.{build}"
        key = (queue, kind, version)
        poller = SimpleNamespace(
            deployment_options=SimpleNamespace(
                deployment_name=self.deployment, build_id=build
            ),
            identity=identity,
            last_access_time=SimpleNamespace(
                ToDatetime=lambda tzinfo=None: datetime.now(timezone.utc)
            ),
        )
        self.pollers.setdefault(key, []).append(
            {"poller": poller, "remaining": live_for_calls}
        )

    def deployment_snapshot(self):
        deployment_name, _, build_id = self.current.partition(".")
        routing = SimpleNamespace(
            current_version=self.current,
            current_deployment_version=SimpleNamespace(
                deployment_name=deployment_name, build_id=build_id
            ),
            HasField=lambda name: True,
        )
        return SimpleNamespace(
            worker_deployment_info=SimpleNamespace(routing_config=routing),
            conflict_token=self.conflict_token,
        )

    def version_snapshot(self, version):
        try:
            state = self.versions[version]
        except KeyError:
            raise _not_found()
        infos = [
            SimpleNamespace(name=name, type=kind) for name, kind in state["queues"]
        ]
        return SimpleNamespace(
            worker_deployment_version_info=SimpleNamespace(
                task_queue_infos=infos,
                deployment_version=SimpleNamespace(deployment_name=self.deployment),
                deployment_name=self.deployment,
            )
        )

    def promote(self, version, token):
        self.set_current_calls.append(version)
        if token != self.conflict_token:
            raise RPCError("conflict", RPCStatusCode.ABORTED, None)
        if version not in self.versions:
            raise _not_found()
        self.current = version
        self.conflict_token += b"+1"


class _FakeWorkflowService:
    def __init__(self, server):
        self._server = server

    async def describe_worker_deployment(self, request):
        return self._server.deployment_snapshot()

    async def describe_worker_deployment_version(self, request):
        return self._server.version_snapshot(request.version)

    async def describe_task_queue(self, request):
        name = request.task_queue.name
        kind = request.task_queue_type
        pollers = []
        for (queue, queue_kind, _version), entries in self._server.pollers.items():
            if queue != name or queue_kind != kind:
                continue
            live = []
            for entry in entries:
                if entry["remaining"] is not None:
                    if entry["remaining"] <= 0:
                        continue
                    entry["remaining"] -= 1
                live.append(entry["poller"])
            entries[:] = [
                entry
                for entry in entries
                if entry["remaining"] is None or entry["remaining"] > 0
            ]
            pollers.extend(live)
        return SimpleNamespace(
            pollers=pollers,
            stats=SimpleNamespace(
                approximate_backlog_age=SimpleNamespace(
                    ToTimedelta=lambda: timedelta(0)
                )
            ),
        )

    async def set_worker_deployment_current_version(self, request):
        self._server.promote(request.version, request.conflict_token)


class _FakeHandle:
    def __init__(self, payload):
        self._payload = payload

    async def result(self):
        return self._payload


class _FakeClient:
    def __init__(self, server):
        self.namespace = "default"
        self.workflow_service = _FakeWorkflowService(server)
        self._server = server

    async def start_workflow(self, *args, **kwargs):
        server = self._server
        execution_id = kwargs.get("id")
        server.canaries_started.append(execution_id)
        if server.on_canary_start is not None:
            server.on_canary_start()
        if server.canary_result is not None:
            return _FakeHandle(server.canary_result)
        if server.fail_ordinary_canaries and "-ordinary-" in str(execution_id):
            return _FakeHandle({"digest": "mismatch", "status": "rejected"})
        (name, arg) = args[:2]
        assert name == "MoonMind.ReleaseCanary"
        digest = arg["digest"] if isinstance(arg, dict) else arg
        return _FakeHandle({"digest": digest, "status": "verified"})

    def get_workflow_handle(self, workflow_id):
        raise AssertionError("unexpected handle lookup")


def _spec(build, queues=("mm.workflow.user.v2",)):
    return SimpleNamespace(
        versioning_enabled=True,
        workflows=(object,),
        deployment_id="moonmind-workflow-fleet",
        build_id=build,
        task_queues=tuple(queues),
    )


def _server_with_current_old_new():
    server = _FakeServer()
    queue = "mm.workflow.user.v2"
    registered = {
        (queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
        (queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
    }
    old = server.add_version("old", queues=registered)
    new = server.add_version("new", queues=registered)
    server.current = old
    return server, old, new


@pytest.mark.asyncio
async def test_steward_promotes_abandoned_current(monkeypatch):
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {"status": "current", "currentVersion": new}
    assert server.current == new
    assert server.set_current_calls == [new]
    # One qualification canary plus one ordinary-traffic canary per promotion.
    assert len(server.canaries_started) == 2
    # Concurrent stewards and conflict retries reuse the same qualification
    # run instead of spinning new canaries.
    assert server.canaries_started[0] == (
        "mm-steward-canary-moonmind-workflow-fleet.new"
    )
    assert server.canaries_started[1].startswith(
        "mm-steward-canary-moonmind-workflow-fleet.new-ordinary-"
    )


@pytest.mark.asyncio
async def test_steward_promotes_lone_stale_fleet_when_nothing_serves(monkeypatch):
    """Registration timestamps never order releases.

    A lone previously unseen image with nothing serving converges routing to
    the release that is actually deployed rather than stalling forever. A
    live route is never displaced whatever image backs it (see the
    preserve-live-route tests); deliberate moves of a live route stay on the
    managed update path.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    server.current = new
    stale = server.add_version(
        "stale",
        queues={
            ("mm.workflow.user.v2", TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
            ("mm.workflow.user.v2", TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
        },
    )
    result = await bootstrap_version_routing(_FakeClient(server), _spec("stale"))
    assert result == {"status": "current", "currentVersion": stale}
    assert server.current == stale
    assert server.set_current_calls == [stale]


@pytest.mark.asyncio
async def test_steward_preserves_live_current_route(monkeypatch):
    """The unpromoted-installed-fix replay: live pollers keep the route.

    An older recorded current version with live workers must never be
    displaced by a newer restarted image; qualification and promotion stay
    with the authorized release controller.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    # Controlled time: the fake pollers below never expire, so drive the
    # fake clock past the shortened window instead of busy-spinning.
    _install_controlled_routing_clock(
        monkeypatch, timeout_seconds=3, poll_seconds=1
    )
    server, old, new = _server_with_current_old_new()
    queue = "mm.workflow.user.v2"
    server.add_poller("old", queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW)
    server.add_poller("old", queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY)
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {
        "status": "awaiting_promotion",
        "currentVersion": old,
        "candidateVersion": new,
        "recoveryOwner": "deployment-control",
    }
    assert server.canaries_started == []
    assert server.set_current_calls == []
    assert server.current == old


@pytest.mark.asyncio
async def test_steward_promotes_after_restart_in_flight_dies(monkeypatch):
    """Fresh pollers from a stopping fleet must not park startup forever.

    The previous fleet's last polls look live for a bounded     window after a
    rolling restart. Once they expire with no replacement, the deployed
    release is promoted instead of waiting indefinitely.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    _install_controlled_routing_clock(
        monkeypatch, timeout_seconds=30, poll_seconds=1
    )
    server, _old, new = _server_with_current_old_new()
    queue = "mm.workflow.user.v2"
    server.add_poller(
        "old", queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, live_for_calls=3
    )
    server.add_poller(
        "old", queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY, live_for_calls=3
    )
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {"status": "current", "currentVersion": new}
    assert server.current == new
    assert server.set_current_calls == [new]


@pytest.mark.asyncio
async def test_steward_fails_loud_when_own_version_never_registers(monkeypatch):
    """An unregistered target must fail startup, never park silently.

    If this worker's version never appears after its own polls, promotion
    cannot be qualified; a loud startup error retries the process instead of
    caching a parked result that nobody will revisit.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    _install_bounded_routing_sleep(monkeypatch)
    server, old, new = _server_with_current_old_new()
    del server.versions[new]
    with pytest.raises(RuntimeError, match="did not register"):
        await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert server.current == old
    assert server.set_current_calls == []


@pytest.mark.asyncio
async def test_steward_parks_on_lost_promotion_race(monkeypatch):
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    rival = server.add_version(
        "rival",
        queues={
            ("mm.workflow.user.v2", TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
            ("mm.workflow.user.v2", TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
        },
    )

    def _rival_wins():
        server.current = rival
        server.conflict_token += b"+rival"

    server.on_canary_start = _rival_wins
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {
        "status": "awaiting_promotion",
        "currentVersion": rival,
        "candidateVersion": new,
        "recoveryOwner": "deployment-control",
    }
    # The losing compare-and-set attempt must not move routing itself.
    assert server.current == rival


@pytest.mark.asyncio
async def test_steward_reraises_failed_canary(monkeypatch):
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, old, _new = _server_with_current_old_new()
    server.canary_result = {"digest": "new", "status": "rejected"}
    with pytest.raises(ValueError, match="canary"):
        await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert server.current == old
    assert server.set_current_calls == []


@pytest.mark.asyncio
async def test_bootstrap_current_needs_no_stewardship(monkeypatch):
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    server.current = new
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {
        "status": "current",
        "currentVersion": new,
        "candidateVersion": new,
    }
    assert server.canaries_started == []
    assert server.set_current_calls == []


@pytest.mark.asyncio
async def test_steward_parks_on_partial_outage(monkeypatch):
    """A degraded route is not an abandoned route.

    When the current version still has live pollers on some queues while
    others went quiet, startup must preserve the route for the authorized
    release controller instead of promoting over the surviving workers.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    _install_controlled_routing_clock(
        monkeypatch, timeout_seconds=3, poll_seconds=1
    )
    server, old, new = _server_with_current_old_new()
    queue = "mm.workflow.user.v2"
    server.add_poller("old", queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW)
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {
        "status": "awaiting_promotion",
        "currentVersion": old,
        "candidateVersion": new,
        "recoveryOwner": "deployment-control",
    }
    assert server.canaries_started == []
    assert server.set_current_calls == []
    assert server.current == old


@pytest.mark.asyncio
async def test_steward_requires_every_served_queue(monkeypatch):
    """Promotion qualifies the whole serving surface, not one queue.

    When the current version served queues that the deployed release has
    not registered -- a missing or late fleet -- stewardship must fail
    loudly instead of promoting a release ordinary workflows cannot use.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    _install_bounded_routing_sleep(monkeypatch)
    server = _FakeServer()
    queue = "mm.workflow.user.v2"
    other = "mm.activity.agent_runtime"
    old = server.add_version(
        "old",
        queues={
            (queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
            (queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
            (other, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
        },
    )
    server.add_version(
        "new",
        queues={
            (queue, TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW),
            (queue, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY),
        },
    )
    server.current = old
    with pytest.raises(RuntimeError, match="did not register"):
        await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert server.current == old
    assert server.set_current_calls == []


@pytest.mark.asyncio
async def test_steward_reraises_failed_ordinary_verification(monkeypatch):
    """A failed ordinary-route check must not look like a handoff.

    When the compare-and-set applies while ordinary verification fails, the
    steward re-proves the converged route instead of reporting the target
    as current; a still-failing route raises loudly even though routing
    already moved.
    """
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    server.fail_ordinary_canaries = True
    with pytest.raises(ValueError, match="failed verification"):
        await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert server.current == new
    assert server.set_current_calls == [new]
