"""Real branch producer, dispatch and restore retain work across input delivery."""

import os

import pytest

from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecordStore,
)
from tests.unit.omnigent.test_checkpoint_branch_dispatch_restore import (
    test_branch_producer_dispatch_restores_saved_git_work as exercise_branch,
)


class InterruptedProjection(RuntimeError):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ignored_inputs", [False, True], ids=["visible-inputs", "ignored-inputs"]
)
@pytest.mark.parametrize("cut", ["ownership", "partial-inputs", "before-ready"])
async def test_branch_redelivery_reuses_bound_inputs_with_fresh_materializer(
    tmp_path, monkeypatch, ignored_inputs, cut, saved_inputs=None
):
    materialize = OmnigentWorkspaceMaterializer.materialize
    mark_ready = SandboxWorkspaceRecordStore.mark_ready
    handoff = OmnigentWorkspaceMaterializer._handoff_restored_workspace
    link = os.link
    interruptions = 0
    replacements = []

    def interrupted_ready(store, *args, **kwargs):
        nonlocal interruptions
        if cut == "before-ready" and interruptions < 2:
            interruptions += 1
            raise InterruptedProjection("interrupted before readiness")
        return mark_ready(store, *args, **kwargs)

    def interrupted_link(src, dst, *args, **kwargs):
        nonlocal interruptions
        result = link(src, dst, *args, **kwargs)
        if (
            cut == "partial-inputs"
            and str(src).isdigit()
            and "src_dir_fd" in kwargs
            and interruptions < 2
        ):
            interruptions += 1
            raise InterruptedProjection("interrupted after one input promotion")
        return result

    async def interrupted_handoff(self, *args, **kwargs):
        nonlocal interruptions
        await handoff(self, *args, **kwargs)
        if cut == "ownership" and interruptions < 2:
            interruptions += 1
            raise InterruptedProjection("interrupted after ownership handoff")

    async def redeliver(self, request, **kwargs):
        # Exercise the existing named and optional-input request surfaces
        # through real dispatch, admission and materialization on every retry.
        request.parameters["gateResultRef"] = request.input_refs[0]
        request.parameters["stepEvidenceBundles"] = [list(request.input_refs)]
        current = self
        for _ in range(3):
            try:
                return await materialize(current, request, **kwargs)
            except InterruptedProjection:
                loc = request.workspace_spec["workspaceLocator"]
                store = SandboxWorkspaceRecordStore(current._root)
                state = store.read_readiness(loc["workspaceId"])
                assert state["phase"] == (
                    "restoring" if cut == "ownership" else "projecting"
                )
                assert state["inputProjection"]["inputContract"]["namedRefs"] == {
                    "gateResultPath": request.input_refs[0]
                }
                assert state["inputProjection"]["selectedAttachments"] == list(
                    request.input_refs
                )
                assert not store.is_ready(loc["workspaceId"], state["fingerprint"])
                parent = current._root / "temporal_sandbox" / loc["workspaceId"]
                assert not list(parent.glob(".moonmind-import-*"))
                assert not list((parent / "repo").rglob(".input-*"))
                fresh = OmnigentWorkspaceMaterializer(
                    command_runner=None,
                    workspace_root=current._root,
                    artifact_service=current._remediation_artifacts,
                )
                # Controlled local Git transport/branch port, as in the source
                # production-boundary fixture. Restore and input owners stay real.
                fresh._prepare_sandbox_workspace = self._prepare_sandbox_workspace
                fresh._prepare_target_branch = self._prepare_target_branch
                replacements.append(fresh)
                current = fresh
        pytest.fail("bounded redelivery did not converge")

    monkeypatch.setattr(SandboxWorkspaceRecordStore, "mark_ready", interrupted_ready)
    monkeypatch.setattr(os, "link", interrupted_link)
    monkeypatch.setattr(
        OmnigentWorkspaceMaterializer,
        "_handoff_restored_workspace",
        interrupted_handoff,
    )
    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", redeliver)
    await exercise_branch(
        tmp_path,
        monkeypatch,
        None,
        ignored_inputs=ignored_inputs,
        materialization_attempts=3 if cut == "before-ready" else 1,
        saved_inputs=saved_inputs,
    )
    assert interruptions == 2
    assert len(replacements) == 2


class AuditStopped(BaseException):
    pass


@pytest.mark.asyncio
async def test_binding_before_verification_cannot_adopt_legacy_partial_outputs(
    tmp_path, monkeypatch
):
    from moonmind.schemas.checkpoint_restore_models import CheckpointRestoreError

    materialize = OmnigentWorkspaceMaterializer.materialize
    mark_ready = SandboxWorkspaceRecordStore.mark_ready
    bind = SandboxWorkspaceRecordStore.bind_input_projection
    interrupted = False
    crash_on_rebind = False

    def lose_legacy_binding(store, workspace_id, fingerprint):
        nonlocal interrupted, crash_on_rebind
        if not interrupted:
            interrupted = True
            # Model a pre-change interrupted candidate: canonical receipt and
            # projected files exist, but no persisted projection selection did.
            store._readiness_marker_path(workspace_id).unlink()
            crash_on_rebind = True
            raise InterruptedProjection("legacy input projection was interrupted")
        return mark_ready(store, workspace_id, fingerprint)

    def interrupted_bind(store, *args, **kwargs):
        nonlocal crash_on_rebind
        result = bind(store, *args, **kwargs)
        if crash_on_rebind:
            crash_on_rebind = False
            assert result["phase"] == "restoring"
            raise InterruptedProjection(
                "crash after binding before baseline verification"
            )
        return result

    async def inspect(self, request, **kwargs):
        try:
            await materialize(self, request, **kwargs)
        except InterruptedProjection:
            locator = request.workspace_spec["workspaceLocator"]
            candidate = (
                self._root / "temporal_sandbox" / locator["workspaceId"] / "repo"
            )
            snapshot = {
                str(p.relative_to(candidate)): p.read_bytes()
                for p in candidate.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(candidate).parts
            }
            with pytest.raises(InterruptedProjection, match="before baseline"):
                await materialize(self, request, **kwargs)
            for _ in range(2):
                fresh = OmnigentWorkspaceMaterializer(
                    command_runner=None,
                    workspace_root=self._root,
                    artifact_service=self._remediation_artifacts,
                )
                with pytest.raises(
                    CheckpointRestoreError, match="materialized path set mismatch"
                ):
                    await materialize(fresh, request, **kwargs)
                record = SandboxWorkspaceRecordStore(self._root).read_readiness(
                    locator["workspaceId"]
                )
                assert record["phase"] == "restoring"
            assert snapshot == {
                str(p.relative_to(candidate)): p.read_bytes()
                for p in candidate.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(candidate).parts
            }
            raise AuditStopped
        pytest.fail("expected interrupted projection")

    monkeypatch.setattr(SandboxWorkspaceRecordStore, "mark_ready", lose_legacy_binding)
    monkeypatch.setattr(
        SandboxWorkspaceRecordStore, "bind_input_projection", interrupted_bind
    )
    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", inspect)
    with pytest.raises(AuditStopped):
        await exercise_branch(tmp_path, monkeypatch, None)


@pytest.mark.asyncio
async def test_completed_legacy_ready_workspace_keeps_later_agent_work(
    tmp_path, monkeypatch
):
    import json

    materialize = OmnigentWorkspaceMaterializer.materialize

    async def inspect(self, request, **kwargs):
        result = await materialize(self, request, **kwargs)
        locator = request.workspace_spec["workspaceLocator"]
        store = SandboxWorkspaceRecordStore(self._root)
        record = store.read_readiness(locator["workspaceId"])
        fingerprint = dict(record["fingerprint"])
        fingerprint.pop("inputProjectionDigest")
        marker = store._readiness_marker_path(locator["workspaceId"])
        marker.write_text(
            json.dumps({"version": "ready-v1", "fingerprint": fingerprint})
        )
        previous_marker = marker.read_bytes()
        candidate = self._root / "temporal_sandbox" / locator["workspaceId"] / "repo"
        changed = candidate / "unstaged.txt"
        changed.write_text("later agent work\n")
        receipts = {
            p: p.read_bytes() for p in (self._root / "managed_restores").glob("*.json")
        }
        fresh = OmnigentWorkspaceMaterializer(
            command_runner=None,
            workspace_root=self._root,
            artifact_service=self._remediation_artifacts,
        )
        await materialize(fresh, request, **kwargs)
        assert changed.read_text() == "later agent work\n"
        assert marker.read_bytes() == previous_marker
        assert receipts == {p: p.read_bytes() for p in receipts}
        assert result["targetPath"] == "/workspaces/run"
        raise AuditStopped

    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", inspect)
    with pytest.raises(AuditStopped):
        await exercise_branch(tmp_path, monkeypatch, None)


@pytest.mark.asyncio
async def test_cancelled_ownership_handoff_keeps_fence_until_thread_stops(
    tmp_path, monkeypatch
):
    import asyncio
    import threading

    from moonmind.omnigent.host_services import workspace as module

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def handoff(*args):
        entered.set()
        assert release.wait(10)
        finished.set()

    monkeypatch.setattr(module, "chown_tree", handoff)
    store = SandboxWorkspaceRecordStore(tmp_path)

    async def owner():
        async with store.materialization_locked("owned"):
            await module._chown_restored_workspace(tmp_path, os.getuid(), os.getgid())

    task = asyncio.create_task(owner())
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    assert not task.done()
    observed = []

    async def contender():
        async with SandboxWorkspaceRecordStore(tmp_path).materialization_locked(
            "owned"
        ):
            observed.append(finished.is_set())

    next_task = asyncio.create_task(contender())
    await asyncio.sleep(0.02)
    assert not observed
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await next_task
    assert observed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift", ["refs", "named", "optional-policy", "empty-to-input"]
)
async def test_ready_materializer_rejects_same_attempt_input_drift(
    tmp_path, monkeypatch, drift
):
    from moonmind.omnigent.harness_platform import HarnessPlatformError

    materialize = OmnigentWorkspaceMaterializer.materialize

    async def inspect(self, request, **kwargs):
        admitted_ref = request.input_refs[0]
        if drift == "empty-to-input":
            request.input_refs = []
            request.workspace_spec["restoreInputRefs"] = []
        await materialize(self, request, **kwargs)
        locator = request.workspace_spec["workspaceLocator"]
        store = SandboxWorkspaceRecordStore(self._root)
        previous = store.read_readiness(locator["workspaceId"])
        candidate = self._root / "temporal_sandbox" / locator["workspaceId"] / "repo"
        saved = (candidate / "unstaged.txt").read_bytes()
        if drift == "refs":
            request.input_refs.pop()
        elif drift == "named":
            request.parameters["gateResultRef"] = admitted_ref
        elif drift == "optional-policy":
            request.parameters["stepEvidenceBundles"] = [[admitted_ref]]
        else:
            request.input_refs = [admitted_ref]
        with pytest.raises(HarnessPlatformError, match="changed") as failure:
            await materialize(self, request, **kwargs)
        assert failure.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
        assert store.read_readiness(locator["workspaceId"]) == previous
        assert (candidate / "unstaged.txt").read_bytes() == saved
        raise AuditStopped

    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", inspect)
    with pytest.raises(AuditStopped):
        await exercise_branch(tmp_path, monkeypatch, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_inputs", ["same", "different"])
@pytest.mark.parametrize("cut", ["ownership", "partial-inputs", "before-ready"])
async def test_captured_inputs_stay_baseline_owned_on_repeated_redelivery(
    tmp_path, monkeypatch, saved_inputs, cut
):
    await test_branch_redelivery_reuses_bound_inputs_with_fresh_materializer(
        tmp_path, monkeypatch, False, cut, saved_inputs=saved_inputs
    )


@pytest.mark.asyncio
async def test_cold_baseline_handoff_precedes_distinct_runtime_input_verification(
    tmp_path, monkeypatch
):
    from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjector

    materialize = OmnigentWorkspaceMaterializer.materialize
    handoffs = []
    requested_uid, requested_gid = os.getuid() + 1, os.getgid() + 1

    async def request_distinct_owner(self, request, **kwargs):
        kwargs.update(runtime_uid=requested_uid, runtime_gid=requested_gid)
        return await materialize(self, request, **kwargs)

    async def handoff(self, spec, uid, gid):
        # This unprivileged fixture verifies the real call order and identity
        # contract; actual descriptor-safe chown is covered independently.
        handoffs.append((uid, gid))

    async def verify(
        self, workspace, *, projection, runtime_uid, runtime_gid, **kwargs
    ):
        assert handoffs == [(requested_uid, requested_gid)]
        assert (runtime_uid, runtime_gid) == handoffs[0]
        assert all(
            (entry["uid"], entry["gid"]) == handoffs[0]
            for entry in projection["entries"]
        )
        raise AuditStopped

    monkeypatch.setattr(
        OmnigentWorkspaceMaterializer, "materialize", request_distinct_owner
    )
    monkeypatch.setattr(
        OmnigentWorkspaceMaterializer, "_handoff_restored_workspace", handoff
    )
    monkeypatch.setattr(WorkspaceArtifactProjector, "project_bound_inputs", verify)
    with pytest.raises(AuditStopped):
        await exercise_branch(
            tmp_path, monkeypatch, None, ignored_inputs=False, saved_inputs="same"
        )


@pytest.mark.asyncio
async def test_ready_v2_does_not_recreate_later_deleted_baseline_input(
    tmp_path, monkeypatch
):
    from moonmind.omnigent.harness_platform import HarnessPlatformError

    materialize = OmnigentWorkspaceMaterializer.materialize

    async def inspect(self, request, **kwargs):
        await materialize(self, request, **kwargs)
        locator = request.workspace_spec["workspaceLocator"]
        store = SandboxWorkspaceRecordStore(self._root)
        previous = store.read_readiness(locator["workspaceId"])
        candidate = self._root / "temporal_sandbox" / locator["workspaceId"] / "repo"
        entry = next(
            entry
            for entry in previous["inputProjection"]["entries"]
            if entry["path"].startswith(".moonmind/restore/")
        )
        target = candidate / entry["path"]
        target.unlink()
        saved = (candidate / "unstaged.txt").read_bytes()
        with pytest.raises(HarnessPlatformError, match="missing"):
            await materialize(self, request, **kwargs)
        assert not target.exists()
        assert store.read_readiness(locator["workspaceId"]) == previous
        assert (candidate / "unstaged.txt").read_bytes() == saved
        raise AuditStopped

    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", inspect)
    with pytest.raises(AuditStopped):
        await exercise_branch(
            tmp_path, monkeypatch, None, ignored_inputs=False, saved_inputs="same"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", [None, "ownership", "partial-inputs"])
async def test_ready_v2_recovers_entire_lost_candidate_strictly(
    tmp_path, monkeypatch, cut
):
    import shutil

    materialize = OmnigentWorkspaceMaterializer.materialize

    handoff = OmnigentWorkspaceMaterializer._handoff_restored_workspace
    link = os.link
    armed = False

    async def interrupted_handoff(self, *args, **kwargs):
        nonlocal armed
        await handoff(self, *args, **kwargs)
        if armed and cut == "ownership":
            armed = False
            raise InterruptedProjection

    def interrupted_link(*args, **kwargs):
        nonlocal armed
        result = link(*args, **kwargs)
        if armed and cut == "partial-inputs":
            armed = False
            raise InterruptedProjection
        return result

    async def inspect(self, request, **kwargs):
        nonlocal armed
        await materialize(self, request, **kwargs)
        locator = request.workspace_spec["workspaceLocator"]
        store = SandboxWorkspaceRecordStore(self._root)
        previous = store.read_readiness(locator["workspaceId"])
        candidate = self._root / "temporal_sandbox" / locator["workspaceId"] / "repo"
        expected = {path.name: path.read_bytes() for path in candidate.glob("*.txt")}
        shutil.rmtree(candidate)
        fresh = OmnigentWorkspaceMaterializer(
            command_runner=None,
            workspace_root=self._root,
            artifact_service=self._remediation_artifacts,
        )
        fresh._prepare_sandbox_workspace = self._prepare_sandbox_workspace
        fresh._prepare_target_branch = self._prepare_target_branch
        armed = cut is not None
        try:
            await materialize(fresh, request, **kwargs)
        except InterruptedProjection:
            interrupted = store.read_readiness(locator["workspaceId"])
            assert interrupted["phase"] == (
                "restoring" if cut == "ownership" else "projecting"
            )
            assert not store.is_materialized(locator["workspaceId"])
            fresh = OmnigentWorkspaceMaterializer(
                command_runner=None,
                workspace_root=self._root,
                artifact_service=self._remediation_artifacts,
            )
            fresh._prepare_sandbox_workspace = self._prepare_sandbox_workspace
            fresh._prepare_target_branch = self._prepare_target_branch
            await materialize(fresh, request, **kwargs)
        assert {
            path.name: path.read_bytes() for path in candidate.glob("*.txt")
        } == expected
        assert store.read_readiness(locator["workspaceId"]) == previous
        for entry in previous["inputProjection"]["entries"]:
            assert (candidate / entry["path"]).is_file()
        raise AuditStopped

    monkeypatch.setattr(
        OmnigentWorkspaceMaterializer,
        "_handoff_restored_workspace",
        interrupted_handoff,
    )
    monkeypatch.setattr(os, "link", interrupted_link)
    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", inspect)
    with pytest.raises(AuditStopped):
        await exercise_branch(
            tmp_path, monkeypatch, None, ignored_inputs=False, saved_inputs="same"
        )


@pytest.mark.asyncio
async def test_baseline_input_deleted_during_handoff_is_not_recreated(
    tmp_path, monkeypatch
):
    from moonmind.omnigent.harness_platform import HarnessPlatformError

    materialize = OmnigentWorkspaceMaterializer.materialize
    handoff = OmnigentWorkspaceMaterializer._handoff_restored_workspace
    deleted = []

    async def interrupted_handoff(self, spec, *args):
        await handoff(self, spec, *args)
        locator = spec["workspaceLocator"]
        candidate = self._root / "temporal_sandbox" / locator["workspaceId"] / "repo"
        leaves = list((candidate / ".moonmind/restore").iterdir())
        assert len(leaves) == 1
        leaves[0].unlink()
        deleted.append(leaves[0])

    async def inspect(self, request, **kwargs):
        with pytest.raises(
            HarnessPlatformError, match="saved checkpoint input is missing"
        ):
            await materialize(self, request, **kwargs)
        assert len(deleted) == 1
        assert not deleted[0].exists()
        assert (deleted[0].parents[2] / "unstaged.txt").read_text() == "unstaged work\n"
        raise AuditStopped

    monkeypatch.setattr(
        OmnigentWorkspaceMaterializer,
        "_handoff_restored_workspace",
        interrupted_handoff,
    )
    monkeypatch.setattr(OmnigentWorkspaceMaterializer, "materialize", inspect)
    with pytest.raises(AuditStopped):
        await exercise_branch(
            tmp_path, monkeypatch, None, ignored_inputs=False, saved_inputs="same"
        )
