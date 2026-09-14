"""Unit tests for release-routing stewardship (plain restarts must converge)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.service import RPCError, RPCStatusCode

from moonmind.workflows.temporal.release_routing import bootstrap_version_routing


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

    def add_version(self, build, *, age, queues=()):
        version = f"{self.deployment}.{build}"
        self.versions[version] = {
            "create_time": datetime.now(timezone.utc) - age,
            "queues": set(queues),
        }
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
                create_time=state["create_time"],
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
        server.canaries_started.append(kwargs.get("id"))
        if server.on_canary_start is not None:
            server.on_canary_start()
        if server.canary_result is not None:
            return _FakeHandle(server.canary_result)
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
    old = server.add_version("old", age=timedelta(hours=4), queues=registered)
    new = server.add_version("new", age=timedelta(minutes=20), queues=registered)
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


@pytest.mark.asyncio
async def test_steward_parks_when_current_is_newer(monkeypatch):
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    server.current = new
    result = await bootstrap_version_routing(_FakeClient(server), _spec("old"))
    assert result == {
        "status": "awaiting_promotion",
        "currentVersion": new,
        "candidateVersion": f"{server.deployment}.old",
        "recoveryOwner": "deployment-control",
    }
    assert server.canaries_started == []
    assert server.set_current_calls == []
    assert server.current == new


@pytest.mark.asyncio
async def test_steward_preserves_live_current_route(monkeypatch):
    """The unpromoted-installed-fix replay: live pollers keep the route.

    An older recorded current version with live workers must never be
    displaced by a newer restarted image; qualification and promotion stay
    with the authorized release controller.
    """
    from moonmind.workflows.temporal import release_routing

    async def _no_sleep(delay):
        return None

    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    # Shorten the re-verification window; the fake pollers below never expire.
    monkeypatch.setattr(release_routing, "_ROUTE_DEATH_TIMEOUT_SECONDS", 3)
    monkeypatch.setattr(release_routing, "_ROUTE_DEATH_POLL_SECONDS", 1)
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

    The previous fleet's last polls look live for a bounded window after a
    rolling restart. Once they expire with no replacement, the deployed
    release is promoted instead of waiting indefinitely.
    """
    from moonmind.workflows.temporal import release_routing

    async def _no_sleep(delay):
        return None

    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(release_routing, "_ROUTE_DEATH_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(release_routing, "_ROUTE_DEATH_POLL_SECONDS", 1)
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
async def test_steward_parks_when_own_version_unregistered(monkeypatch):
    async def _no_sleep(delay):
        return None

    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    server, old, new = _server_with_current_old_new()
    del server.versions[new]
    result = await bootstrap_version_routing(_FakeClient(server), _spec("new"))
    assert result == {
        "status": "awaiting_promotion",
        "currentVersion": old,
        "candidateVersion": new,
        "recoveryOwner": "deployment-control",
    }
    assert server.canaries_started == []
    assert server.set_current_calls == []


@pytest.mark.asyncio
async def test_steward_parks_on_lost_promotion_race(monkeypatch):
    monkeypatch.delenv("MOONMIND_RELEASE_QUALIFICATION", raising=False)
    server, _old, new = _server_with_current_old_new()
    rival = server.add_version(
        "rival",
        age=timedelta(minutes=5),
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
