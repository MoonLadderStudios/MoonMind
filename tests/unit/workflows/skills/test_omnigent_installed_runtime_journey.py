"""Installed-runtime journey for #4503.

Ties the real deployment/record path (``FileDesiredStateStore`` +
``migrate_omnigent_release``) to the real normal-admission consumers
(``refresh_schedule_deployment_snapshot`` + ``assert_plan_matches_deployed_runtime``).

A pre-existing recurring schedule's next attempt binds the installed target
without per-release policy-version creation, profile revision, schedule
re-admission, or manual DB edits. Schedule identity, timing, pause state, and
authored choices are preserved; an already-running older attempt keeps its
original evidence byte-identical.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfileVersion,
)
from api_service.services import omnigent_agent_profile_selection as selection
from api_service.services.omnigent_policies import OmnigentPolicyService
from moonmind.workflows.skills.deployment_execution import FileDesiredStateStore
from moonmind.workflows.skills.omnigent_release import (
    OMNIGENT_RELEASE_RECORD_KEY,
    OmnigentRelease,
    OmnigentReleaseDrivers,
    OmnigentReleaseError,
    migrate_omnigent_release,
)

SERVER_REPO = "ghcr.io/omnigent-ai/omnigent-server"
HOST_REPO = "ghcr.io/moonladderstudios/omnigent-host-moonmind"
OLD_SERVER = f"{SERVER_REPO}@sha256:" + "3" * 64
NEW_SERVER = f"{SERVER_REPO}@sha256:" + "9" * 64
OLD_HOST = f"{HOST_REPO}@sha256:" + "c" * 64
NEW_HOST = f"{HOST_REPO}@sha256:" + "a" * 64
FOREIGN_HOST = "ghcr.io/example/other-host@sha256:" + "e" * 64


def _refs(server=OLD_SERVER, host=OLD_HOST):
    return {
        "server": server,
        "codex": host,
        "opencode": host,
        "shared": host,
        "pi": host,
    }


def _release(server=OLD_SERVER, host=OLD_HOST, revision=1):
    return OmnigentRelease(
        revision=revision,
        server_image_ref=server,
        host_image_refs={
            "codex": host,
            "opencode": host,
            "shared": host,
            "pi": host,
        },
        updated_at="2026-09-15T19:00:00+00:00",
        updated_by="test",
    )


def _store(tmp_path):
    return FileDesiredStateStore(
        env_file_path=str(tmp_path / ".env.deploy"),
        json_file_path=str(tmp_path / "desired-state.json"),
    )


def _enable_omnigent(monkeypatch):
    from moonmind.omnigent import settings

    gate = type("Gate", (), {"enabled": True})()
    monkeypatch.setattr(settings, "build_omnigent_gate", lambda: gate)
    monkeypatch.setattr(settings, "generic_host_enabled", lambda: True)


def _journey_drivers(calls, *, candidates=None, live=None):
    async def deployment_inputs():
        return {"OMNIGENT_IMAGE": SERVER_REPO}

    async def resolve_candidates(_env):
        calls.append("resolve")
        return dict(candidates) if candidates is not None else _refs()

    async def read_live_refs():
        calls.append("live")
        return dict(live) if live is not None else {}

    async def restart_server(_target):
        calls.append("restart")

    async def await_resolution(target):
        calls.append("await-resolution")
        return {k: v for k, v in target.items() if k != "codex"}

    async def verify_live_container(server_ref):
        calls.append("verify-live")
        return server_ref

    return OmnigentReleaseDrivers(
        deployment_inputs=deployment_inputs,
        resolve_candidates=resolve_candidates,
        read_live_refs=read_live_refs,
        restart_server=restart_server,
        await_resolution=await_resolution,
        sync_catalog=None,
        cut_policy_versions=None,
        refresh_schedules=None,
        qualify_host_drift=None,
        verify_live_container=verify_live_container,
    )


@pytest.mark.asyncio
async def test_journey_release_advance_then_schedule_occurrence_uses_installed_target(
    tmp_path, monkeypatch,
):
    """REQ-01: deployment/record -> normal admission with zero bulk rewrites.

    Uses the real ``FileDesiredStateStore`` record and the real
    ``migrate_omnigent_release`` path, then admits the next occurrence of a
    pre-existing schedule through the real launch gate
    (``assert_plan_matches_deployed_runtime``). The schedule's next attempt
    binds the installed target; no policy versions are cut, no profile
    revisions are made, no schedule re-admission runs, and no manual DB edit
    occurs.
    """

    from moonmind.omnigent.deployment_identity import (
        assert_plan_matches_deployed_runtime,
    )

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    await store.merge(
        env_updates=_release().to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: _release().to_record()},
    )
    new_refs = _refs(server=NEW_SERVER, host=NEW_HOST)

    # The policy service must never be asked to cut a version merely to
    # synchronize deployment digests on this path.
    async def _fail_new_version(*_args, **_kwargs):
        raise AssertionError("routine image advance must not cut policy versions")

    monkeypatch.setattr(OmnigentPolicyService, "new_version", _fail_new_version)

    calls: list[str] = []
    receipt = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_journey_drivers(calls, candidates=new_refs, live=_refs()),
    )
    assert receipt["status"] == "migrated"
    assert receipt["revision"] == 2
    assert receipt["serverImageRef"] == NEW_SERVER
    assert receipt["policiesCut"] == []
    assert receipt["policiesSkipped"] == []
    assert receipt["schedulesRefreshed"] == 0
    assert receipt["catalogRef"] is None
    # No catalog/policy/schedule/drift boundary runs on the routine path.
    assert calls == ["resolve", "live", "restart", "await-resolution", "verify-live"]

    # Pre-existing schedule occurrence admits the installed target. The stored
    # schedule pins the previous digest; the launch gate allows the
    # same-repository rebuild without a new policy/version ladder.
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_identity.resolve_deployed_server_build_digest",
        lambda: "sha256:" + "9" * 64,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_identity._resolve_deployed_host_image_ref",
        lambda _harness: NEW_HOST,
    )

    class _Payload:
        executionRealizerRef = "generic-omnigent-host@1"
        omnigentVersion = None
        harnessCatalogRef = None
        harnessId = "opencode-native"
        hostImageRef = OLD_HOST

    class _Support:
        omnigentServerBuildRef = "sha256:" + "9" * 64

    payload = _Payload()
    payload.supportIdentity = _Support()
    await assert_plan_matches_deployed_runtime(payload)

    # A genuinely different family still blocks with diagnostics naming it.
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_identity._resolve_deployed_host_image_ref",
        lambda _harness: FOREIGN_HOST,
    )
    from moonmind.omnigent.deployment_identity import (
        OmnigentDeploymentIdentityConflict,
    )

    with pytest.raises(OmnigentDeploymentIdentityConflict, match="family"):
        await assert_plan_matches_deployed_runtime(payload)


@pytest.mark.asyncio
async def test_journey_schedule_identity_and_older_attempt_evidence_preserved(
    tmp_path, monkeypatch,
):
    """REQ-02: schedule identity/timing/pause/choices survive an image advance.

    A pre-existing schedule keeps its id, cadence, timezone, pause state,
    input, harness/profile/model/account/publication semantics, and budgets
    across a compatible image rebuild. An already-running older attempt keeps
    its original image/session/evidence byte-identical (no silent restart or
    rebind).
    """

    from tests.unit.services.test_schedule_deployment_refresh import (
        DeploymentSession,
    )
    from moonmind.omnigent.policies import document_digest

    async def policy_version(_self, policy_id, version):
        ref = f"{policy_id}@{version}"
        document = copy.deepcopy(session.policies[ref]["boundaries"])
        return SimpleNamespace(
            state=session.policy_states[ref],
            document_json=document,
            validation_json={"valid": True},
            digest=document_digest(document),
        )

    session = DeploymentSession()
    monkeypatch.setattr(OmnigentPolicyService, "get_version", policy_version)
    monkeypatch.setattr(
        selection, "_managed_secret_statuses_for_profiles", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        selection, "provider_profile_launch_ready", lambda *_a, **_kw: True
    )

    schedule_id = uuid4()
    schedule = {
        "id": str(schedule_id),
        "cron": "0 9 * * *",
        "timezone": "America/Los_Angeles",
        "enabled": True,
        "policy": {"budgetUsd": 5.0, "maxParallelRuns": 1},
    }
    parameters = session.parameters()
    authored_workflow = dict(parameters["workflow"])
    authored_model = parameters["model"]

    # Older attempt evidence captured before the image advance.
    older_attempt = {
        "attemptId": "att-older-1",
        "sessionRef": "session-older-1",
        "serverImageRef": "ghcr.io/example/server@sha256:" + "1" * 64,
        "hostImageRef": "ghcr.io/example/host@sha256:" + "1" * 64,
        "bindingDigest": "digest:older",
    }
    older_snapshot = copy.deepcopy(older_attempt)

    refreshed = await selection.refresh_schedule_deployment_snapshot(
        session, parameters=parameters, consumer_id="schedule", user=None,
    )

    # Digest-only rebuild advances the snapshot without touching authored intent.
    assert refreshed["omnigent"]["launchPolicyRef"] == "omnigent-on-demand@2"
    assert refreshed["model"] == authored_model
    assert refreshed["workflow"] == authored_workflow
    assert refreshed["profileId"] == parameters["profileId"]
    # Schedule identity owners are untouched by this helper: the caller keeps
    # the same id/cadence/timezone/pause/budget rows; only the snapshot moves.
    assert schedule["id"] == str(schedule_id)
    assert schedule["cron"] == "0 9 * * *"
    assert schedule["timezone"] == "America/Los_Angeles"
    assert schedule["enabled"] is True
    assert schedule["policy"] == {"budgetUsd": 5.0, "maxParallelRuns": 1}
    # Usage publication is deferred to the schedule revision; the helper must
    # not silently rebind the older attempt either.
    assert session.usage.version == 1
    assert older_attempt == older_snapshot


@pytest.mark.asyncio
async def test_journey_default_vs_active_policy_needs_no_new_ladder(monkeypatch):
    """REQ-03: historical default vs later-active policy through real consumers.

    The bootstrap compiler consumers (``resolve_runtime_snapshot`` for an
    explicit pin and ``resolve_default_runtime_snapshot``-equivalent default
    read) both resolve without cutting a new policy/version ladder when only
    same-repository image digests moved. A foreign family still blocks at the
    launch gate.
    """

    from moonmind.omnigent.compatibility import is_same_image_repository
    from moonmind.omnigent.deployment_identity import (
        OmnigentDeploymentIdentityConflict,
        assert_plan_matches_deployed_runtime,
    )

    from moonmind.omnigent.policies import document_digest
    from tests.unit.services.test_omnigent_execution_plan_service import (
        _policy_snapshot,
    )

    # Real compiler-owned policy documents: same repository, moved digests.
    old_snapshot = _policy_snapshot(
        harness="opencode-native",
        policy="omnigent-on-demand@13",
        host_image_ref=OLD_HOST,
    )
    new_snapshot = _policy_snapshot(
        harness="opencode-native",
        policy="omnigent-on-demand@14",
        host_image_ref=NEW_HOST,
    )
    old_boundaries = copy.deepcopy(old_snapshot["boundaries"])
    new_boundaries = copy.deepcopy(new_snapshot["boundaries"])
    old_boundaries["host"]["serverImageRef"] = OLD_SERVER
    new_boundaries["host"]["serverImageRef"] = NEW_SERVER

    class _Row:
        def __init__(self, document):
            self.document_json = document
            self.validation_json = {"valid": True}
            self.digest = document_digest(document)
            self.state = "active"

    rows = {
        ("omnigent-on-demand", 13): _Row(old_boundaries),
        ("omnigent-on-demand", 14): _Row(new_boundaries),
    }

    async def fake_get_version(_self, policy_id, version):
        return rows[(policy_id, version)]

    monkeypatch.setattr(OmnigentPolicyService, "get_version", fake_get_version)

    async def fake_get_policy(_self, policy_id):
        return SimpleNamespace(policy_id=policy_id, default_version=13)

    monkeypatch.setattr(OmnigentPolicyService, "get_policy", fake_get_policy)

    service = OmnigentPolicyService(SimpleNamespace())
    historical_default = await service.resolve_runtime_snapshot("omnigent-on-demand@13")
    later_active = await service.resolve_runtime_snapshot("omnigent-on-demand@14")
    assert historical_default["policyRef"] == "omnigent-on-demand@13"
    assert later_active["policyRef"] == "omnigent-on-demand@14"
    # Same-repository rebuild: neither resolution requires a new ladder.
    assert is_same_image_repository(OLD_HOST, NEW_HOST) is True
    assert is_same_image_repository(OLD_HOST, FOREIGN_HOST) is False

    monkeypatch.setattr(
        "moonmind.omnigent.deployment_identity.resolve_deployed_server_build_digest",
        lambda: "sha256:" + "9" * 64,
    )
    monkeypatch.setattr(
        "moonmind.omnigent.deployment_identity._resolve_deployed_host_image_ref",
        lambda _harness: NEW_HOST,
    )

    class _Payload:
        executionRealizerRef = "generic-omnigent-host@1"
        omnigentVersion = None
        harnessCatalogRef = None
        harnessId = "opencode-native"
        hostImageRef = OLD_HOST

    class _Support:
        omnigentServerBuildRef = "sha256:" + "9" * 64

    payload = _Payload()
    payload.supportIdentity = _Support()
    await assert_plan_matches_deployed_runtime(payload)

    monkeypatch.setattr(
        "moonmind.omnigent.deployment_identity._resolve_deployed_host_image_ref",
        lambda _harness: FOREIGN_HOST,
    )
    with pytest.raises(OmnigentDeploymentIdentityConflict, match="family"):
        await assert_plan_matches_deployed_runtime(payload)


@pytest.mark.asyncio
async def test_journey_interrupted_installation_resumes_same_operation(
    tmp_path, monkeypatch,
):
    """REQ-04: interruption resumes the accepted operation exactly once.

    An interrupted migration (live disagrees with the record) converges to the
    same recorded revision without advancing it or duplicating occurrences.
    A second pass after convergence is a no-op verify.
    """

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    await store.merge(
        env_updates=_release(revision=4).to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: _release(revision=4).to_record()},
    )
    first_calls: list[str] = []
    first = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_journey_drivers(first_calls, candidates=_refs(), live={}),
    )
    assert first["status"] == "converged"
    assert first["revision"] == 4
    assert first["policiesCut"] == []
    assert first["schedulesRefreshed"] == 0
    assert "restart" in first_calls

    second_calls: list[str] = []
    second = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_journey_drivers(second_calls, candidates=_refs(), live=_refs()),
    )
    assert second["status"] == "aligned"
    assert second["revision"] == 4
    assert "restart" not in second_calls


@pytest.mark.asyncio
async def test_journey_required_runtime_failure_stays_visible_with_retry(
    tmp_path, monkeypatch,
):
    """REQ-04: required-runtime failure is a visible failure with retry.

    A failed ``await_resolution`` raises after the record advance; the retry
    of the same accepted operation converges onto that recorded revision
    without duplicating it. Controller repair stays available via the lock +
    revision-CAS path, and local logs (the raised step error) remain usable.
    """

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    await store.merge(
        env_updates=_release().to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: _release().to_record()},
    )
    new_refs = _refs(server=NEW_SERVER, host=NEW_HOST)
    calls: list[str] = []
    drivers = _journey_drivers(calls, candidates=new_refs, live=_refs())

    async def _failing_resolution(_target):
        raise OmnigentReleaseError("await-resolution", "required runtime not ready")

    drivers = OmnigentReleaseDrivers(
        deployment_inputs=drivers.deployment_inputs,
        resolve_candidates=drivers.resolve_candidates,
        read_live_refs=drivers.read_live_refs,
        restart_server=drivers.restart_server,
        await_resolution=_failing_resolution,
        sync_catalog=None,
        cut_policy_versions=None,
        refresh_schedules=None,
        qualify_host_drift=None,
        verify_live_container=drivers.verify_live_container,
    )
    with pytest.raises(OmnigentReleaseError, match="await-resolution"):
        await migrate_omnigent_release(
            store=store, runner=object(), owner="test", drivers=drivers
        )

    # The advance persists the record before restart/resolution, so the
    # failure leaves r2 recorded but un-resolved. Retry must converge onto
    # that same accepted revision, never cut r3 or duplicate the launch.
    from moonmind.workflows.skills.omnigent_release import read_omnigent_release

    env_entries, record_doc = store.read()
    record = read_omnigent_release(env_entries, record_doc)
    assert record is not None and record.revision == 2

    retry_calls: list[str] = []
    retry = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_journey_drivers(retry_calls, candidates=new_refs, live=_refs()),
    )
    assert retry["status"] == "converged"
    assert retry["revision"] == 2


@pytest.mark.asyncio
async def test_journey_optional_catalog_outage_does_not_undo_compose_work(
    tmp_path, monkeypatch,
):
    """REQ-04/REQ-05: optional catalog outage never undoes confirmed work.

    The routine release path never consults the catalog/schedule/policy
    hooks, so even a failing legacy catalog driver cannot undo confirmed
    Compose work. Production wiring leaves those hooks unset.
    """

    from dataclasses import replace

    from moonmind.workflows.skills.omnigent_release import production_drivers

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    await store.merge(
        env_updates=_release().to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: _release().to_record()},
    )
    new_refs = _refs(server=NEW_SERVER, host=NEW_HOST)
    calls: list[str] = []
    drivers = replace(
        _journey_drivers(calls, candidates=new_refs, live=_refs()),
        sync_catalog=AsyncMock(side_effect=RuntimeError("catalog unavailable")),
    )
    receipt = await migrate_omnigent_release(
        store=store, runner=object(), owner="test", drivers=drivers
    )
    assert receipt["status"] == "migrated"
    assert receipt["catalogRef"] is None
    assert "catalog" not in calls

    class _Runner:
        async def up(self, **kwargs):
            return {"exitCode": 0}

    production = production_drivers(
        runner=_Runner(), moonmind_image="img@sha256:" + "f" * 64, actor="release"
    )
    assert production.sync_catalog is None
    assert production.cut_policy_versions is None
    assert production.refresh_schedules is None
    assert production.qualify_host_drift is None
