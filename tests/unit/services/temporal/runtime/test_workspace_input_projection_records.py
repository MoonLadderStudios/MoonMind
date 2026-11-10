"""Durable admission and cancellation-safe fencing of restored workspace inputs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from moonmind.schemas.workspace_locator_models import WorkspaceLocatorResolutionError
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
)

WORKSPACE_ID = "input-projection-workspace"
BASE_FINGERPRINT = {"sourceDigest": "sha256:source", "targetOwnerWorkflowId": "owner"}
PROJECTION = {
    "version": "inputs-v1",
    "files": [{"path": "inputs/a.txt", "digest": "a"}],
}


def _ready_path(store: SandboxWorkspaceRecordStore) -> Path:
    return store.store_root / f"{WORKSPACE_ID}.ready.json"


def _bind(store: SandboxWorkspaceRecordStore) -> dict:
    with store.claims_locked(WORKSPACE_ID):
        return store.bind_input_projection(WORKSPACE_ID, BASE_FINGERPRINT, PROJECTION)


def test_projection_is_bound_before_ready_with_canonical_digest(tmp_path: Path) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    projection = json.loads(json.dumps(PROJECTION))
    with store.claims_locked(WORKSPACE_ID):
        record = store.bind_input_projection(WORKSPACE_ID, BASE_FINGERPRINT, projection)
    digest = hashlib.sha256(
        json.dumps(PROJECTION, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    expected = {
        "version": "ready-v2",
        "phase": "restoring",
        "fingerprint": {
            **BASE_FINGERPRINT,
            "inputProjectionDigest": f"sha256:{digest}",
        },
        "inputProjection": PROJECTION,
    }
    assert record == expected
    assert store.read_readiness(WORKSPACE_ID) == expected
    assert not store.is_ready(WORKSPACE_ID, record["fingerprint"])
    assert not store.is_ready(WORKSPACE_ID, BASE_FINGERPRINT)
    assert not store.is_materialized(WORKSPACE_ID)
    assert _ready_path(store).stat().st_mode & 0o777 == 0o600
    projection["files"][0]["path"] = "changed"
    assert record == expected
    with store.claims_locked(WORKSPACE_ID):
        assert (
            store.bind_input_projection(
                WORKSPACE_ID,
                dict(reversed(list(BASE_FINGERPRINT.items()))),
                dict(reversed(list(PROJECTION.items()))),
            )
            == expected
        )


def test_projection_advancement_preserves_descriptor_and_marks_only_ready(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    with store.claims_locked(WORKSPACE_ID):
        for phase in ("restoring", "projecting", "projecting", "ready", "ready"):
            record = store.advance_input_projection(
                WORKSPACE_ID, record["fingerprint"], phase
            )
            assert record["phase"] == phase
            assert record["inputProjection"] == PROJECTION
            assert store.is_materialized(WORKSPACE_ID) == (phase == "ready")
            assert store.is_ready(WORKSPACE_ID, record["fingerprint"]) == (
                phase == "ready"
            )
        assert (
            store.bind_input_projection(WORKSPACE_ID, BASE_FINGERPRINT, PROJECTION)
            == record
        )
    assert not store.is_ready(WORKSPACE_ID, BASE_FINGERPRINT)


@pytest.mark.parametrize("changed", ["fingerprint", "projection"])
def test_rebinding_changed_identity_preserves_record(
    tmp_path: Path, changed: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    _bind(store)
    before = _ready_path(store).read_bytes()
    fingerprint, projection = dict(BASE_FINGERPRINT), dict(PROJECTION)
    if changed == "fingerprint":
        fingerprint["sourceDigest"] = "sha256:other"
    else:
        projection["files"] = []
    with store.claims_locked(WORKSPACE_ID), pytest.raises(
        WorkspaceLocatorResolutionError
    ):
        store.bind_input_projection(WORKSPACE_ID, fingerprint, projection)
    assert _ready_path(store).read_bytes() == before


@pytest.mark.parametrize("phase", ["ready", "invalid"])
def test_cannot_skip_baseline_verification_or_use_unknown_phase(
    tmp_path: Path, phase: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    before = _ready_path(store).read_bytes()
    with store.claims_locked(WORKSPACE_ID), pytest.raises(
        WorkspaceLocatorResolutionError
    ):
        store.advance_input_projection(WORKSPACE_ID, record["fingerprint"], phase)
    assert _ready_path(store).read_bytes() == before
    assert not store.is_materialized(WORKSPACE_ID)


def test_advancement_rejects_changed_identity_and_backwards_phase(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    with store.claims_locked(WORKSPACE_ID):
        store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], "projecting"
        )
        before = _ready_path(store).read_bytes()
        for fingerprint, phase in (
            ({**record["fingerprint"], "sourceDigest": "sha256:other"}, "ready"),
            (BASE_FINGERPRINT, "ready"),
            (record["fingerprint"], "restoring"),
        ):
            with pytest.raises(WorkspaceLocatorResolutionError):
                store.advance_input_projection(WORKSPACE_ID, fingerprint, phase)
            assert _ready_path(store).read_bytes() == before


@pytest.mark.parametrize("corruption", ["descriptor", "digest", "phase"])
def test_corrupt_v2_record_fails_closed_unchanged(
    tmp_path: Path, corruption: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    if corruption == "descriptor":
        record["inputProjection"]["files"] = []
    elif corruption == "digest":
        record["fingerprint"]["inputProjectionDigest"] = "sha256:corrupt"
    else:
        record["phase"] = "invalid"
    _ready_path(store).write_text(json.dumps(record))
    before = _ready_path(store).read_bytes()
    with store.claims_locked(WORKSPACE_ID):
        for action in (
            lambda: store.bind_input_projection(
                WORKSPACE_ID, BASE_FINGERPRINT, PROJECTION
            ),
            lambda: store.advance_input_projection(
                WORKSPACE_ID, record["fingerprint"], "projecting"
            ),
            lambda: store.is_ready(WORKSPACE_ID, record["fingerprint"]),
        ):
            with pytest.raises(WorkspaceLocatorResolutionError):
                action()
            assert _ready_path(store).read_bytes() == before


@pytest.mark.parametrize(
    "projection",
    [{"large": "x" * (128 * 1024)}, {"nan": float("nan")}, {"object": object()}, []],
)
def test_unbounded_or_non_json_descriptors_are_not_admitted(
    tmp_path: Path, projection: object
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    with store.claims_locked(WORKSPACE_ID), pytest.raises(
        WorkspaceLocatorResolutionError
    ):
        store.bind_input_projection(WORKSPACE_ID, BASE_FINGERPRINT, projection)
    assert store.read_readiness(WORKSPACE_ID) is None
    assert not store.is_materialized(WORKSPACE_ID)


def test_legacy_readiness_is_unchanged_and_not_implicitly_migrated(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    assert not store.is_ready(WORKSPACE_ID, BASE_FINGERPRINT)
    store.mark_ready(WORKSPACE_ID, BASE_FINGERPRINT)
    assert store.is_ready(WORKSPACE_ID, BASE_FINGERPRINT)
    assert store.is_materialized(WORKSPACE_ID)
    before = _ready_path(store).read_bytes()
    with store.claims_locked(WORKSPACE_ID), pytest.raises(
        WorkspaceLocatorResolutionError
    ):
        store.bind_input_projection(WORKSPACE_ID, BASE_FINGERPRINT, PROJECTION)
    assert _ready_path(store).read_bytes() == before


def test_legacy_mark_ready_cannot_overwrite_projection_record(tmp_path: Path) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    before = _ready_path(store).read_bytes()
    with pytest.raises(WorkspaceLocatorResolutionError):
        store.mark_ready(WORKSPACE_ID, record["fingerprint"])
    assert _ready_path(store).read_bytes() == before
    assert not store.is_materialized(WORKSPACE_ID)


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_atomic_write_failure_preserves_admitted_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    before = _ready_path(store).read_bytes()

    def fail(*args, **kwargs):
        raise OSError("interrupted persistence")

    monkeypatch.setattr(os, failure, fail)
    with store.claims_locked(WORKSPACE_ID), pytest.raises(OSError, match="interrupted"):
        store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], "projecting"
        )
    assert _ready_path(store).read_bytes() == before
    assert not list(store.store_root.glob("*.tmp"))
    assert not store.is_materialized(WORKSPACE_ID)


@pytest.mark.asyncio
async def test_materialization_lock_serializes_same_process_without_blocking_sibling(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    other = SandboxWorkspaceRecordStore(tmp_path)
    entered = asyncio.Event()

    async def wait_for_same():
        async with other.materialization_locked(WORKSPACE_ID):
            entered.set()

    async with store.materialization_locked(WORKSPACE_ID):
        contender = asyncio.create_task(wait_for_same())
        await asyncio.sleep(0.05)
        assert not entered.is_set()
        async with other.materialization_locked("sibling"):
            assert not entered.is_set()
    await asyncio.wait_for(contender, 2)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_cancelled_acquire_drains_thread_and_releases_existing_claim_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    attempted = threading.Event()
    claims_locked = store.claims_locked

    @contextmanager
    def tracked(workspace_id):
        attempted.set()
        with claims_locked(workspace_id):
            yield

    monkeypatch.setattr(store, "claims_locked", tracked)

    async def acquire():
        async with store.materialization_locked(WORKSPACE_ID):
            pytest.fail("cancelled acquisition entered its body")

    with claims_locked(WORKSPACE_ID):
        contender = asyncio.create_task(acquire())
        assert await asyncio.to_thread(attempted.wait, 1)
        contender.cancel()
        await asyncio.sleep(0.02)
        contender.cancel()  # Repeated cancellation must not orphan the acquisition.
        await asyncio.sleep(0.02)
        assert not contender.done()
        async with store.materialization_locked("sibling"):
            pass
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(contender, 2)
    async with store.materialization_locked(WORKSPACE_ID):
        pass


@pytest.mark.asyncio
async def test_cancelled_hold_releases_claim_lock(tmp_path: Path) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    entered = asyncio.Event()

    async def hold():
        async with store.materialization_locked(WORKSPACE_ID):
            entered.set()
            await asyncio.Future()

    holder = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), 1)
    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder
    async with store.materialization_locked(WORKSPACE_ID):
        pass


@pytest.mark.asyncio
async def test_base_exception_during_hold_releases_claim_lock(tmp_path: Path) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)

    class Interrupted(BaseException):
        pass

    with pytest.raises(Interrupted):
        async with store.materialization_locked(WORKSPACE_ID):
            raise Interrupted()
    async with store.materialization_locked(WORKSPACE_ID):
        pass


def test_mark_ready_advances_verified_projection_without_replacing_descriptor(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    with store.claims_locked(WORKSPACE_ID):
        store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], "projecting"
        )
        store.mark_ready(WORKSPACE_ID, record["fingerprint"])
        store.mark_ready(WORKSPACE_ID, record["fingerprint"])
    assert store.read_readiness(WORKSPACE_ID) == {**record, "phase": "ready"}
    assert store.is_ready(WORKSPACE_ID, record["fingerprint"])
    assert store.is_materialized(WORKSPACE_ID)


def test_ready_retry_repairs_interrupted_legacy_marker_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    mark_materialized = store.mark_materialized

    def fail(workspace_id):
        raise OSError("interrupted legacy marker")

    with store.claims_locked(WORKSPACE_ID):
        store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], "projecting"
        )
        monkeypatch.setattr(store, "mark_materialized", fail)
        with pytest.raises(OSError, match="interrupted"):
            store.mark_ready(WORKSPACE_ID, record["fingerprint"])
        assert store.is_ready(WORKSPACE_ID, record["fingerprint"])
        assert not store.is_materialized(WORKSPACE_ID)
        monkeypatch.setattr(store, "mark_materialized", mark_materialized)
        store.mark_ready(WORKSPACE_ID, record["fingerprint"])
    assert store.read_readiness(WORKSPACE_ID) == {**record, "phase": "ready"}
    assert store.is_materialized(WORKSPACE_ID)


@pytest.mark.asyncio
async def test_cancelled_failed_acquisition_preserves_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    attempted, proceed = threading.Event(), threading.Event()
    claims_locked = store.claims_locked

    @contextmanager
    def fail(workspace_id):
        attempted.set()
        assert proceed.wait(2)
        raise WorkspaceLocatorResolutionError("contended", "test acquisition failed")
        yield  # pragma: no cover

    monkeypatch.setattr(store, "claims_locked", fail)

    async def acquire():
        async with store.materialization_locked(WORKSPACE_ID):
            pytest.fail("failed acquisition entered its body")

    contender = asyncio.create_task(acquire())
    assert await asyncio.to_thread(attempted.wait, 1)
    contender.cancel()
    await asyncio.sleep(0)
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(contender, 2)
    monkeypatch.setattr(store, "claims_locked", claims_locked)
    async with store.materialization_locked(WORKSPACE_ID):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_body", [False, True])
async def test_repeated_cancellation_at_descriptor_release_cannot_orphan_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_body: bool
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    entered = asyncio.Event()
    close = os.close
    event_loop_thread = threading.get_ident()
    release_count = 0
    holder = None

    def cancel_at_close(descriptor):
        nonlocal release_count
        if (
            threading.get_ident() == event_loop_thread
            and asyncio.current_task() is holder
        ):
            # Request cancellation at the actual kernel-lock release boundary,
            # including a second cancellation while a cancelled body unwinds.
            release_count += 1
            holder.cancel()
            holder.cancel()
        close(descriptor)

    async def hold():
        async with store.materialization_locked(WORKSPACE_ID):
            entered.set()
            if cancel_body:
                await asyncio.Future()

    monkeypatch.setattr(os, "close", cancel_at_close)
    holder = asyncio.create_task(hold())
    await asyncio.wait_for(entered.wait(), 1)
    if cancel_body:
        holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(holder, 1)
    assert release_count == 1
    async with store.materialization_locked(WORKSPACE_ID):
        pass


def test_readiness_persistence_syncs_parent_after_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    fsync, replace = os.fsync, os.replace
    operations = []
    replacement_directory = None

    def sync(descriptor):
        is_directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        operations.append("parent fsync" if is_directory else "file fsync")
        if is_directory:
            assert descriptor == replacement_directory
        fsync(descriptor)

    def rename(source, destination, **kwargs):
        nonlocal replacement_directory
        operations.append("replace")
        replacement_directory = kwargs.get("dst_dir_fd")
        assert replacement_directory is not None
        assert kwargs["src_dir_fd"] == replacement_directory
        replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", rename)
    _bind(store)
    assert operations == ["file fsync", "replace", "parent fsync"]


def test_failed_binding_parent_sync_is_observable_and_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    fsync = os.fsync
    failed_syncs = 0

    def fail_parent(descriptor):
        nonlocal failed_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed_syncs += 1
            raise OSError("readiness parent sync failed")
        fsync(descriptor)

    monkeypatch.setattr(os, "fsync", fail_parent)
    with pytest.raises(OSError, match="parent sync failed"):
        _bind(store)
    # Replacement is observable, but neither its first write nor an
    # idempotent retry may report success until directory durability succeeds.
    record = store.read_readiness(WORKSPACE_ID)
    assert record["phase"] == "restoring"
    with pytest.raises(OSError, match="parent sync failed"):
        _bind(store)
    assert failed_syncs == 2
    assert not store.is_materialized(WORKSPACE_ID)
    assert not store.is_ready(WORKSPACE_ID, record["fingerprint"])
    assert not list(store.store_root.glob("*.tmp"))
    monkeypatch.setattr(os, "fsync", fsync)
    assert _bind(store) == record


@pytest.mark.parametrize("phase", ["projecting", "ready"])
def test_failed_phase_parent_sync_is_observable_and_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    record = _bind(store)
    fsync = os.fsync
    failed_syncs = 0

    def fail_parent(descriptor):
        nonlocal failed_syncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed_syncs += 1
            raise OSError("readiness parent sync failed")
        fsync(descriptor)

    with store.claims_locked(WORKSPACE_ID):
        if phase == "ready":
            store.advance_input_projection(
                WORKSPACE_ID, record["fingerprint"], "projecting"
            )
        monkeypatch.setattr(os, "fsync", fail_parent)
        for _ in range(2):
            with pytest.raises(OSError, match="parent sync failed"):
                store.advance_input_projection(
                    WORKSPACE_ID, record["fingerprint"], phase
                )
            assert store.read_readiness(WORKSPACE_ID)["phase"] == phase
            assert not store.is_materialized(WORKSPACE_ID)
        assert failed_syncs == 2
        monkeypatch.setattr(os, "fsync", fsync)
        result = store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], phase
        )
    assert result == {**record, "phase": phase}
    assert store.is_materialized(WORKSPACE_ID) == (phase == "ready")


@pytest.mark.parametrize("already_bound", [False, True])
def test_readiness_sync_does_not_follow_symlinked_record_directory(
    tmp_path: Path, already_bound: bool
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    if already_bound:
        _bind(store)
    else:
        store.store_root.mkdir(parents=True)
    moved = store.store_root.with_name("moved-records")
    store.store_root.rename(moved)
    store.store_root.symlink_to(moved, target_is_directory=True)
    before = {path.name: path.read_bytes() for path in moved.iterdir()}
    with pytest.raises(OSError):
        _bind(store)
    after = {path.name: path.read_bytes() for path in moved.iterdir()}
    # The lock already belongs to this directory; no readiness file is added or
    # replaced through the substituted directory symlink.
    before.pop(f"{WORKSPACE_ID}.claims.lock", None)
    after.pop(f"{WORKSPACE_ID}.claims.lock", None)
    assert before == after


def _completed_projection(store: SandboxWorkspaceRecordStore) -> dict:
    record = _bind(store)
    with store.claims_locked(WORKSPACE_ID):
        store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], "projecting"
        )
        return store.advance_input_projection(
            WORKSPACE_ID, record["fingerprint"], "ready"
        )


def test_explicit_absent_candidate_restart_preserves_admitted_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    ready = _completed_projection(store)
    store.mark_ready("sibling", {"owner": "sibling"})
    replace = os.replace

    def publish(source, destination, **kwargs):
        assert not store.is_materialized(WORKSPACE_ID)
        replace(source, destination, **kwargs)

    with store.claims_locked(WORKSPACE_ID):
        # Neither ordinary rebinding nor forward advancement may regress ready.
        assert (
            store.bind_input_projection(WORKSPACE_ID, BASE_FINGERPRINT, PROJECTION)
            == ready
        )
        with pytest.raises(WorkspaceLocatorResolutionError):
            store.advance_input_projection(
                WORKSPACE_ID, ready["fingerprint"], "restoring"
            )
        monkeypatch.setattr(os, "replace", publish)
        restarted = store.restart_input_projection(WORKSPACE_ID, ready["fingerprint"])
        assert restarted == {**ready, "phase": "restoring"}
        assert (
            store.restart_input_projection(WORKSPACE_ID, ready["fingerprint"])
            == restarted
        )
    assert not store.is_ready(WORKSPACE_ID, ready["fingerprint"])
    assert not store.is_materialized(WORKSPACE_ID)
    assert store.is_ready("sibling", {"owner": "sibling"})
    assert store.is_materialized("sibling")


@pytest.mark.parametrize(
    "invalid", ["fingerprint", "legacy", "projecting", "descriptor", "missing"]
)
def test_restart_refuses_invalid_generation_without_mutating_records(
    tmp_path: Path, invalid: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    if invalid == "legacy":
        store.mark_ready(WORKSPACE_ID, BASE_FINGERPRINT)
        fingerprint = BASE_FINGERPRINT
    elif invalid == "missing":
        fingerprint = BASE_FINGERPRINT
    else:
        record = _completed_projection(store)
        fingerprint = record["fingerprint"]
        if invalid == "fingerprint":
            fingerprint = {**fingerprint, "sourceDigest": "sha256:other"}
        elif invalid == "projecting":
            record["phase"] = "projecting"
            _ready_path(store).write_text(json.dumps(record))
        else:
            record["inputProjection"]["files"] = []
            _ready_path(store).write_text(json.dumps(record))
    with store.claims_locked(WORKSPACE_ID):
        before = {path.name: path.read_bytes() for path in store.store_root.iterdir()}
        with pytest.raises(WorkspaceLocatorResolutionError):
            store.restart_input_projection(WORKSPACE_ID, fingerprint)
        assert {
            path.name: path.read_bytes() for path in store.store_root.iterdir()
        } == before


@pytest.mark.parametrize("failure", ["unlink", "file sync", "replace", "parent sync"])
def test_restart_publication_failure_stays_observable_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    ready = _completed_projection(store)
    fsync, replace, unlink = os.fsync, os.replace, os.unlink

    def fail_sync(descriptor):
        kind = (
            "parent sync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file sync"
        )
        if failure == kind:
            raise OSError("restart persistence interrupted")
        fsync(descriptor)

    def fail_replace(*args, **kwargs):
        if failure == "replace":
            raise OSError("restart persistence interrupted")
        replace(*args, **kwargs)

    def fail_unlink(path, **kwargs):
        if failure == "unlink" and str(path).endswith(".materialized"):
            raise OSError("restart persistence interrupted")
        unlink(path, **kwargs)

    with store.claims_locked(WORKSPACE_ID):
        with monkeypatch.context() as patched:
            patched.setattr(os, "fsync", fail_sync)
            patched.setattr(os, "replace", fail_replace)
            patched.setattr(os, "unlink", fail_unlink)
            for _ in range(2):
                with pytest.raises(OSError, match="restart persistence interrupted"):
                    store.restart_input_projection(WORKSPACE_ID, ready["fingerprint"])
                visible_phase = "restoring" if failure == "parent sync" else "ready"
                assert store.read_readiness(WORKSPACE_ID) == {
                    **ready,
                    "phase": visible_phase,
                }
                assert store.is_materialized(WORKSPACE_ID) == (failure == "unlink")
                assert not list(store.store_root.glob("*.tmp"))
        assert store.restart_input_projection(WORKSPACE_ID, ready["fingerprint"]) == {
            **ready,
            "phase": "restoring",
        }
    assert not store.is_ready(WORKSPACE_ID, ready["fingerprint"])
    assert not store.is_materialized(WORKSPACE_ID)


def test_initial_binding_clears_stale_legacy_marker_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    store.mark_materialized(WORKSPACE_ID)
    assert store.read_readiness(WORKSPACE_ID) is None
    replace = os.replace

    def publish(source, destination, **kwargs):
        assert not store.is_materialized(WORKSPACE_ID)
        replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", publish)
    record = _bind(store)
    assert record["phase"] == "restoring"
    assert not store.is_ready(WORKSPACE_ID, record["fingerprint"])
    assert not store.is_materialized(WORKSPACE_ID)


@pytest.mark.asyncio
async def test_explicit_restart_callers_serialize_with_existing_claims_fence(
    tmp_path: Path,
) -> None:
    store = SandboxWorkspaceRecordStore(tmp_path)
    other = SandboxWorkspaceRecordStore(tmp_path)
    ready = _completed_projection(store)
    entered = asyncio.Event()

    async def restart_again():
        async with other.materialization_locked(WORKSPACE_ID):
            entered.set()
            return other.restart_input_projection(WORKSPACE_ID, ready["fingerprint"])

    async with store.materialization_locked(WORKSPACE_ID):
        restarted = store.restart_input_projection(WORKSPACE_ID, ready["fingerprint"])
        contender = asyncio.create_task(restart_again())
        await asyncio.sleep(0.05)
        assert not entered.is_set()
    assert await asyncio.wait_for(contender, 2) == restarted
    assert not store.is_materialized(WORKSPACE_ID)
