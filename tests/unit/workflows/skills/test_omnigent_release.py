"""Singular omnigent release record, decisions, and migration."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.deployment_execution import FileDesiredStateStore
from moonmind.workflows.skills.omnigent_release import (
    OMNIGENT_RELEASE_RECORD_KEY,
    OmnigentRelease,
    OmnigentReleaseDrivers,
    OmnigentReleaseError,
    build_migrated_policy_document,
    decide_release_transition,
    migrate_omnigent_release,
    read_omnigent_release,
)

OLD_SERVER = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
NEW_SERVER = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "9" * 64
OLD_HOST = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "c" * 64
NEW_HOST = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "a" * 64


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


def test_record_round_trip_preserves_revision_chain():
    release = _release()
    parsed = OmnigentRelease.from_record(release.to_record())
    assert parsed == release
    assert parsed.to_env()["OMNIGENT_IMAGE_REF"] == OLD_SERVER
    assert parsed.to_env()["OMNIGENT_OPENCODE_HOST_IMAGE_REF"] == OLD_HOST


def test_record_rejects_malformed_documents():
    assert OmnigentRelease.from_record({}) is None
    assert OmnigentRelease.from_record({"revision": 0, "serverImageRef": "x"}) is None
    assert OmnigentRelease.from_record({"revision": 1}) is None
    assert OmnigentRelease.from_record("nope") is None


def test_read_requires_env_and_sidecar_agreement():
    release = _release()
    env_entries = release.to_env()
    record = {OMNIGENT_RELEASE_RECORD_KEY: release.to_record()}
    assert read_omnigent_release(env_entries, record) == release
    # Sidecar/env disagreement (out-of-band edit) reads as absent so the
    # migration converges instead of trusting either half.
    tampered = dict(env_entries, OMNIGENT_IMAGE_REF=NEW_SERVER)
    assert read_omnigent_release(tampered, record) is None
    assert read_omnigent_release(env_entries, {}) is None


def test_decide_noop_when_live_record_and_candidates_agree():
    release = _release()
    action, target = decide_release_transition(
        _refs(), release, _refs()
    )
    assert action == "noop"
    assert target["server"] == OLD_SERVER


def test_decide_noop_when_live_omits_unobserved_kinds():
    """Live resolved state has no legacy codex field; that is not drift."""
    release = _release()
    live = {k: v for k, v in _refs().items() if k != "codex"}
    action, target = decide_release_transition(live, release, _refs())
    assert action == "noop"
    assert target["server"] == OLD_SERVER


def test_decide_converge_when_live_disagrees_with_record():
    release = _release()
    action, target = decide_release_transition(
        {}, release, _refs(server=NEW_SERVER, host=NEW_HOST)
    )
    # Live is unknown/stale while a record exists: drive live to the record,
    # no new revision.
    assert action == "converge"
    assert target["server"] == OLD_SERVER


def test_decide_advance_when_upstream_moves():
    release = _release()
    action, target = decide_release_transition(
        _refs(), release, _refs(server=NEW_SERVER, host=NEW_HOST)
    )
    assert action == "advance"
    assert target["server"] == NEW_SERVER


def test_decide_advance_without_record_or_live():
    action, target = decide_release_transition({}, None, _refs())
    assert action == "advance"
    assert target["server"] == OLD_SERVER


def test_decide_advance_without_record_even_when_live_matches():
    """First migration must create the record even when already aligned."""
    action, target = decide_release_transition(_refs(), None, _refs())
    assert action == "advance"
    assert target["server"] == OLD_SERVER


def test_decide_advance_when_candidate_adds_host_family():
    """A newly available optional host ref requires a new revision."""
    # Existing record omits `pi` (was unavailable); candidate supplies it.
    recorded_live = {k: v for k, v in _refs().items() if k != "pi"}
    release_missing_pi = OmnigentRelease(
        revision=1,
        server_image_ref=OLD_SERVER,
        host_image_refs={
            "codex": OLD_HOST,
            "opencode": OLD_HOST,
            "shared": OLD_HOST,
            "pi": "",
        },
        updated_at="2026-09-15T19:00:00+00:00",
        updated_by="test",
    )
    action, _ = decide_release_transition(recorded_live, release_missing_pi, _refs())
    assert action == "advance"


def test_build_migrated_policy_document_carries_operator_fields():
    document = {
        "host": {"serverImageRef": OLD_SERVER, "hostImageRef": OLD_HOST, "mode": "x"},
        "resources": {"cpuMillis": 1000},
        "rollout": {"state": "y"},
    }
    migrated = build_migrated_policy_document(
        document, server_ref=NEW_SERVER, host_ref=NEW_HOST
    )
    assert migrated["host"]["serverImageRef"] == NEW_SERVER
    assert migrated["host"]["hostImageRef"] == NEW_HOST
    assert migrated["host"]["mode"] == "x"
    assert migrated["resources"] == {"cpuMillis": 1000}
    assert migrated["rollout"] == {"state": "y"}
    # Input untouched.
    assert document["host"]["serverImageRef"] == OLD_SERVER


def test_build_migrated_policy_document_rejects_bad_shapes():
    with pytest.raises(OmnigentReleaseError):
        build_migrated_policy_document({}, server_ref=NEW_SERVER, host_ref=NEW_HOST)
    with pytest.raises(OmnigentReleaseError):
        build_migrated_policy_document({"host": {}}, server_ref="", host_ref=NEW_HOST)


def _enable_omnigent(monkeypatch):
    from moonmind.omnigent import settings

    gate = type("Gate", (), {"enabled": True})()
    monkeypatch.setattr(settings, "build_omnigent_gate", lambda: gate)
    monkeypatch.setattr(settings, "generic_host_enabled", lambda: True)


def _disable_omnigent(monkeypatch):
    from moonmind.omnigent import settings

    gate = type("Gate", (), {"enabled": False})()
    monkeypatch.setattr(settings, "build_omnigent_gate", lambda: gate)


def _drivers(calls, *, candidates=None, live=None):
    async def deployment_inputs():
        return {"OMNIGENT_IMAGE": "ghcr.io/omnigent-ai/omnigent-server"}

    async def resolve_candidates(_env):
        calls.append("resolve")
        return candidates if candidates is not None else _refs()

    async def read_live_refs():
        calls.append("live")
        return dict(live) if live is not None else {}

    async def restart_server(_target):
        calls.append("restart")

    async def await_resolution(target):
        calls.append("await-resolution")
        return {k: v for k, v in target.items() if k != "codex"}

    async def sync_catalog():
        calls.append("catalog")
        return {"catalogRef": "catalog:new"}

    async def cut_policy_versions(_target):
        calls.append("policies")
        return {"cut": ["omnigent-on-demand@17"], "skipped": []}

    async def refresh_schedules():
        calls.append("schedules")
        return 3

    async def verify_live_container(server_ref):
        calls.append("verify-live")
        return server_ref

    return OmnigentReleaseDrivers(
        deployment_inputs=deployment_inputs,
        resolve_candidates=resolve_candidates,
        read_live_refs=read_live_refs,
        restart_server=restart_server,
        await_resolution=await_resolution,
        sync_catalog=sync_catalog,
        cut_policy_versions=cut_policy_versions,
        refresh_schedules=refresh_schedules,
        verify_live_container=verify_live_container,
    )


@pytest.mark.asyncio
async def test_migrate_noop_reruns_post_steps(tmp_path, monkeypatch):
    from moonmind.omnigent import settings

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    release = _release()
    await store.merge(
        env_updates=release.to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: release.to_record()},
    )
    calls: list[str] = []
    receipt = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_drivers(calls, candidates=_refs(), live=_refs()),
    )
    assert receipt["status"] == "aligned"
    # Aligned refs still rerun convergent post-record steps (without restart)
    # so an interrupted advance that already aligned the server completes its
    # catalog/policy/schedule work instead of reporting stale alignment.
    assert calls == [
        "resolve",
        "live",
        "await-resolution",
        "catalog",
        "policies",
        "schedules",
        "verify-live",
    ]


@pytest.mark.asyncio
async def test_migrate_advances_0_13_to_0_14_and_converges_after(tmp_path, monkeypatch):
    """Replay of the 2026-09-15 saga: 0.13 record, upstream at 0.14.

    First pass advances the record, restarts, cuts policy, refreshes
    schedules. Second pass (live now on the record) is a no-op, which is
    what makes every later schedule fire dispatch instead of failing.
    """
    from moonmind.omnigent import settings

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    release = _release()
    await store.merge(
        env_updates=release.to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: release.to_record()},
    )
    new_refs = _refs(server=NEW_SERVER, host=NEW_HOST)
    calls: list[str] = []
    receipt = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_drivers(calls, candidates=new_refs, live=_refs()),
    )
    assert receipt["status"] == "migrated"
    assert receipt["revision"] == 2
    assert receipt["serverImageRef"] == NEW_SERVER
    assert receipt["policiesCut"] == ["omnigent-on-demand@17"]
    assert receipt["schedulesRefreshed"] == 3
    assert calls == [
        "resolve",
        "live",
        "restart",
        "await-resolution",
        "catalog",
        "policies",
        "schedules",
        "verify-live",
    ]
    env_entries, record_doc = store.read()
    assert env_entries["OMNIGENT_IMAGE_REF"] == NEW_SERVER
    stored = read_omnigent_release(env_entries, record_doc)
    assert stored is not None and stored.revision == 2
    assert stored.previous["serverImageRef"] == OLD_SERVER

    again: list[str] = []
    second = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_drivers(again, candidates=new_refs, live=new_refs),
    )
    assert second["status"] == "aligned"
    assert again == [
        "resolve",
        "live",
        "await-resolution",
        "catalog",
        "policies",
        "schedules",
        "verify-live",
    ]


@pytest.mark.asyncio
async def test_migrate_converge_keeps_revision(tmp_path, monkeypatch):
    from moonmind.omnigent import settings

    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    release = _release(revision=4)
    await store.merge(
        env_updates=release.to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: release.to_record()},
    )
    calls: list[str] = []
    receipt = await migrate_omnigent_release(
        store=store,
        runner=object(),
        owner="test",
        drivers=_drivers(calls, candidates=_refs(server=NEW_SERVER), live={}),
    )
    assert receipt["status"] == "converged"
    assert receipt["revision"] == 4
    assert "restart" in calls


@pytest.mark.asyncio
async def test_migrate_skipped_when_runtime_disabled(tmp_path, monkeypatch):
    from moonmind.omnigent import settings

    _disable_omnigent(monkeypatch)
    calls: list[str] = []
    receipt = await migrate_omnigent_release(
        store=_store(tmp_path),
        runner=object(),
        drivers=_drivers(calls),
    )
    assert receipt["status"] == "skipped"
    assert calls == []


@pytest.mark.asyncio
async def test_migrate_requires_runner_bound_drivers(tmp_path, monkeypatch):
    from moonmind.omnigent import settings

    _enable_omnigent(monkeypatch)
    drivers = _drivers([])
    drivers = OmnigentReleaseDrivers(
        deployment_inputs=drivers.deployment_inputs,
        resolve_candidates=drivers.resolve_candidates,
        read_live_refs=drivers.read_live_refs,
        restart_server=None,
        await_resolution=drivers.await_resolution,
        sync_catalog=drivers.sync_catalog,
        cut_policy_versions=None,
        refresh_schedules=drivers.refresh_schedules,
        verify_live_container=drivers.verify_live_container,
    )
    with pytest.raises(OmnigentReleaseError, match="wiring"):
        await migrate_omnigent_release(
            store=_store(tmp_path), runner=object(), drivers=drivers
        )


@pytest.mark.asyncio
async def test_migrate_surfaces_step_failures(tmp_path, monkeypatch):
    from moonmind.omnigent import settings

    _enable_omnigent(monkeypatch)
    calls: list[str] = []
    drivers = _drivers(calls, live={})

    async def boom(_target):
        raise RuntimeError("compose daemon refused")

    drivers = OmnigentReleaseDrivers(
        deployment_inputs=drivers.deployment_inputs,
        resolve_candidates=drivers.resolve_candidates,
        read_live_refs=drivers.read_live_refs,
        restart_server=boom,
        await_resolution=drivers.await_resolution,
        sync_catalog=drivers.sync_catalog,
        cut_policy_versions=drivers.cut_policy_versions,
        refresh_schedules=drivers.refresh_schedules,
        verify_live_container=drivers.verify_live_container,
    )
    with pytest.raises(RuntimeError, match="compose daemon refused"):
        await migrate_omnigent_release(
            store=_store(tmp_path), runner=object(), drivers=drivers
        )


def test_store_merge_preserves_unrelated_entries(tmp_path):
    import asyncio

    async def go():
        store = _store(tmp_path)
        await store.merge(
            env_updates={"MOONMIND_IMAGE": "img@sha256:" + "b" * 64},
            json_updates={"stack": "moonmind"},
        )
        await store.merge(
            env_updates={"OMNIGENT_IMAGE_REF": NEW_SERVER, "EMPTY": ""},
            json_updates={OMNIGENT_RELEASE_RECORD_KEY: {"revision": 1}},
        )
        return store.read()

    env_entries, record = asyncio.run(go())
    assert env_entries["MOONMIND_IMAGE"] == "img@sha256:" + "b" * 64
    assert env_entries["OMNIGENT_IMAGE_REF"] == NEW_SERVER
    assert "EMPTY" not in env_entries
    assert record["stack"] == "moonmind"
    assert record[OMNIGENT_RELEASE_RECORD_KEY] == {"revision": 1}


def test_store_merge_preserves_unparseable_lines(tmp_path):
    env_file = tmp_path / ".env.deploy"
    env_file.write_text(
        "# operator note\nMOONMIND_IMAGE=\"img\"\nNOT AN ASSIGNMENT\n",
        encoding="utf-8",
    )
    store = FileDesiredStateStore(
        env_file_path=str(env_file),
        json_file_path=str(tmp_path / "desired-state.json"),
    )
    env_entries, _record = store.read()
    assert env_entries == {"MOONMIND_IMAGE": "img"}
    import asyncio

    asyncio.run(store.merge(env_updates={"OMNIGENT_IMAGE_REF": NEW_SERVER}))
    text = env_file.read_text(encoding="utf-8")
    assert "# operator note" in text
    assert "NOT AN ASSIGNMENT" in text
    assert f'OMNIGENT_IMAGE_REF="{NEW_SERVER}"' in text


def test_store_merge_round_trips_escaped_values(tmp_path):
    import asyncio

    store = _store(tmp_path)
    asyncio.run(store.merge(env_updates={"QUOTED": 'say "hi" \\ now'}))
    env_entries, _record = store.read()
    assert env_entries["QUOTED"] == 'say "hi" \\ now'


def test_sidecar_disagreement_forces_convergence(tmp_path):
    import asyncio

    async def go():
        store = _store(tmp_path)
        release = _release()
        await store.merge(
            env_updates=release.to_env(),
            json_updates={OMNIGENT_RELEASE_RECORD_KEY: release.to_record()},
        )
        # Out-of-band env edit: sidecar says OLD, env says NEW.
        await store.merge(env_updates={"OMNIGENT_IMAGE_REF": NEW_SERVER})
        return store.read()

    env_entries, record = asyncio.run(go())
    assert read_omnigent_release(env_entries, record) is None
    action, target = decide_release_transition(
        _refs(server=NEW_SERVER, host=NEW_HOST), None, _refs()
    )
    assert action == "advance"
    assert target["server"] == OLD_SERVER


def test_store_persist_preserves_omnigent_release(tmp_path):
    """Persist must not drop the independently owned Omnigent release."""
    import asyncio

    async def go():
        store = _store(tmp_path)
        release = _release()
        await store.merge(
            env_updates=release.to_env(),
            json_updates={OMNIGENT_RELEASE_RECORD_KEY: release.to_record()},
        )
        await store.persist(
            {
                "stack": "moonmind",
                "imageRepository": "ghcr.io/moonladderstudios/moonmind",
                "requestedReference": "latest",
                "resolvedDigest": "sha256:" + "f" * 64,
                "reason": "test",
                "sourceRunId": "run-1",
            }
        )
        return store.read()

    env_entries, record = asyncio.run(go())
    assert env_entries["OMNIGENT_IMAGE_REF"] == OLD_SERVER
    assert OMNIGENT_RELEASE_RECORD_KEY in record
    assert read_omnigent_release(env_entries, record) is not None


@pytest.mark.asyncio
async def test_cohort_migrate_wires_store_runner_and_image(tmp_path, monkeypatch):
    """The release controller passes its store, runner, and image through."""
    import os

    from moonmind.workflows.skills import deployment_release

    env_file = tmp_path / ".env.deploy"
    json_file = tmp_path / "desired-state.json"
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", str(env_file))
    monkeypatch.setenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE", str(json_file))

    seen: dict[str, object] = {}

    async def fake_migrate(*, store, runner, owner, moonmind_image, drivers, actor):
        seen["owner"] = owner
        seen["moonmind_image"] = moonmind_image
        seen["actor"] = actor
        seen["has_drivers"] = drivers is not None
        store.read()
        seen["store_path"] = str(getattr(store, "env_file_path", ""))
        assert runner is cohort.runner
        return {"status": "aligned", "revision": 0}

    monkeypatch.setattr(
        "moonmind.workflows.skills.omnigent_release.migrate_omnigent_release",
        fake_migrate,
    )
    runner = object()
    cohort = deployment_release.ReleaseCohort(runner, tmp_path, "owner-1")
    receipt = await cohort.migrate_omnigent("img@sha256:" + "f" * 64)
    assert receipt == {"status": "aligned", "revision": 0}
    assert seen["owner"] == "owner-1"
    assert seen["moonmind_image"] == "img@sha256:" + "f" * 64
    assert seen["actor"] == "release"
    assert seen["has_drivers"] is True
    assert seen["store_path"] == str(env_file)
    assert os.environ["MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE"] == str(env_file)


@pytest.mark.asyncio
async def test_cohort_migrate_reports_missing_desired_state(tmp_path, monkeypatch):
    from moonmind.workflows.skills import deployment_release

    monkeypatch.delenv("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE", raising=False)
    cohort = deployment_release.ReleaseCohort(object(), tmp_path, "owner-1")
    receipt = await cohort.migrate_omnigent("img")
    assert receipt["status"] == "skipped"


def test_release_drift_compatible_rebuild_points_at_bootstrap_reconcile():
    """#4379 R7: same-repo rebuild is fenced with an executable recovery."""
    from moonmind.workflows.skills.omnigent_release import (
        raise_for_release_policy_drift,
        release_policy_drift_dispositions,
    )

    # NOTE: "c"*64/"0"*64 digests are synthetic placeholders that never
    # auto-advance; use real-looking digests so the rebuild qualifies.
    old = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "d" * 64
    new = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "e" * 64
    dispositions = release_policy_drift_dispositions(
        {"omnigent-on-demand": old},
        {"server": NEW_SERVER, "opencode": new},
    )
    assert len(dispositions) == 1
    assert dispositions[0]["compatibleRebuild"] is True
    assert dispositions[0]["fencePromotion"] is True
    assert "bootstrap reconcile" in dispositions[0]["recovery"]
    with pytest.raises(OmnigentReleaseError) as exc:
        raise_for_release_policy_drift(dispositions)
    assert exc.value.step == "drift-fence"
    assert "bootstrap reconcile" in str(exc.value)


def test_release_drift_family_change_fences_with_explicit_revision():
    """#4379 R7: a genuinely incompatible image fences closed."""
    from moonmind.workflows.skills.omnigent_release import (
        raise_for_release_policy_drift,
        release_policy_drift_dispositions,
    )

    old = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "d" * 64
    foreign = "ghcr.io/example/other-host@sha256:" + "e" * 64
    dispositions = release_policy_drift_dispositions(
        {"omnigent-on-demand": old},
        {"server": NEW_SERVER, "opencode": foreign},
    )
    assert len(dispositions) == 1
    assert dispositions[0]["compatibleRebuild"] is False
    with pytest.raises(OmnigentReleaseError) as exc:
        raise_for_release_policy_drift(dispositions)
    assert exc.value.step == "drift-fence"
    assert "explicitly" in str(exc.value)


def test_release_drift_absent_when_aligned():
    from moonmind.workflows.skills.omnigent_release import (
        raise_for_release_policy_drift,
        release_policy_drift_dispositions,
    )

    assert (
        release_policy_drift_dispositions(
            {"omnigent-on-demand": NEW_HOST},
            {"server": NEW_SERVER, "opencode": NEW_HOST},
        )
        == []
    )
    assert raise_for_release_policy_drift([]) is None


def test_release_drift_missing_selected_fences():
    """P1: a policy pin with no recorded host target must fence promotion."""
    from moonmind.workflows.skills.omnigent_release import (
        raise_for_release_policy_drift,
        release_policy_drift_dispositions,
    )

    old = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "d" * 64
    dispositions = release_policy_drift_dispositions(
        {"omnigent-on-demand": old},
        {"server": NEW_SERVER, "opencode": ""},
    )
    assert len(dispositions) == 1
    assert dispositions[0]["fencePromotion"] is True
    with pytest.raises(OmnigentReleaseError, match="drift-fence"):
        raise_for_release_policy_drift(dispositions)


def test_decide_advance_preserves_recorded_host_on_transient_empty():
    """P1: a transiently empty candidate must not clear recorded authority."""
    release = _release()
    candidate_missing_codex = dict(_refs(server=NEW_SERVER, host=NEW_HOST))
    candidate_missing_codex["codex"] = ""
    action, target = decide_release_transition(
        _refs(), release, candidate_missing_codex
    )
    assert action == "advance"
    assert target["server"] == NEW_SERVER
    assert target["codex"] == OLD_HOST


@pytest.mark.asyncio
async def test_qualify_host_drift_fails_when_policy_load_fails(monkeypatch):
    """P1: unavailable qualification must fence instead of silently passing."""
    from moonmind.workflows.skills import omnigent_release as release_module

    class _Policy:
        default_version = 14

    async def _get_policy(_self, _policy_id):
        return _Policy()

    async def _get_version(_self, _policy_id, _version):
        raise RuntimeError("transient db error")

    captured: dict[str, object] = {}

    class _Service:
        def __init__(self, _session):
            pass

        get_version = _get_version

    @__import__("contextlib").asynccontextmanager
    async def _session_ctx():
        class _Session:
            async def get(self, _model, _policy_id):
                return _Policy()

        yield _Session()

    monkeypatch.setattr(
        "api_service.services.omnigent_policies.OmnigentPolicyService", _Service
    )
    monkeypatch.setattr(
        release_module, "OMNIGENT_RELEASE_POLICIES", (("omnigent-on-demand", "opencode"),)
    )
    import api_service.db.base as db_base

    monkeypatch.setattr(db_base, "get_async_session_context", _session_ctx)
    with pytest.raises(OmnigentReleaseError, match="qualify-host-drift"):
        await release_module._default_qualify_host_drift({"opencode": NEW_HOST})


@pytest.mark.asyncio
async def test_migrate_fences_promotion_while_drift_remains(tmp_path, monkeypatch):
    """#4379 R7: drift after cut/refresh blocks the release receipt."""
    _enable_omnigent(monkeypatch)
    store = _store(tmp_path)
    release = _release()
    await store.merge(
        env_updates=release.to_env(),
        json_updates={OMNIGENT_RELEASE_RECORD_KEY: release.to_record()},
    )
    from dataclasses import replace

    from moonmind.workflows.skills.omnigent_release import OmnigentReleaseError

    old = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "d" * 64
    foreign = "ghcr.io/example/other-host@sha256:" + "e" * 64

    async def fenced_drift(_target):
        return [
            {
                "policyRef": "omnigent-on-demand@14",
                "plannedHostImageRef": old,
                "selectedHostImageRef": foreign,
                "compatibleRebuild": False,
                "fencePromotion": True,
                "recovery": "revise the policy, profile, and schedule explicitly",
            }
        ]

    calls: list[str] = []
    drivers = replace(_drivers(calls), qualify_host_drift=fenced_drift)
    with pytest.raises(OmnigentReleaseError) as exc:
        await migrate_omnigent_release(
            store=store,
            runner=object(),
            owner="test",
            drivers=drivers,
        )
    assert exc.value.step == "drift-fence"
