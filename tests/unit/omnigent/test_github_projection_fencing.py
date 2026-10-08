"""Exercise the production projection protocol with real concurrent processes."""

import json
import os
import shlex
import subprocess
from uuid import uuid4

import pytest

from moonmind.omnigent.host_services import github_credentials as credentials


def reservation(revision, owner="owned-runtime:1"):
    return {"ownerRef": owner, "revision": revision, "reservationId": str(uuid4())}


def command(path, stamp, action="publish"):
    return [
        "sh",
        "-ceu",
        credentials.github_projection_script(str(path), action=action),
        "--",
        str(os.getuid()),
        str(os.getgid()),
        "github.com",
        json.dumps(stamp),
    ]


def run(path, stamp, action="publish", token=b"test-value", check=True):
    return subprocess.run(
        command(path, stamp, action), input=token, capture_output=True, check=check
    )


def test_older_issuance_cannot_finish_after_newer_projection(tmp_path):
    old, new = reservation(1), reservation(2)
    run(tmp_path, old, "reserve")
    writer = subprocess.Popen(
        command(tmp_path, old),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        writer.stdin.write(b"old-partial")
        writer.stdin.flush()
        run(tmp_path, new, "reserve")
        run(tmp_path, new, token=b"new-value")
        winner = (tmp_path / "hosts.yml").read_bytes()
        writer.communicate(b"-finished", timeout=5)
        assert writer.returncode != 0
        assert (tmp_path / "hosts.yml").read_bytes() == winner
        assert b"new-value" in winner and b"old-partial" not in winner
        assert json.loads(run(tmp_path, new, "inspect").stdout) == new
    finally:
        if writer.poll() is None:
            writer.kill()
        writer.wait()


def test_lost_ack_is_idempotent_and_stamp_is_in_same_file(tmp_path):
    stamp = reservation(1)
    run(tmp_path, stamp, "reserve")
    run(tmp_path, stamp, token=b"first-value")
    installed = (tmp_path / "hosts.yml").read_bytes()
    assert json.loads(installed.splitlines()[0].decode().split(": ", 1)[1]) == stamp
    run(tmp_path, stamp, token=b"must-not-replace-cached-issuance")
    assert (tmp_path / "hosts.yml").read_bytes() == installed
    assert json.loads(run(tmp_path, stamp, "inspect").stdout) == stamp
    assert (tmp_path / "hosts.yml").stat().st_mode & 0o777 == 0o600


def test_new_reservation_failure_preserves_previous_complete_file(tmp_path):
    old, new = reservation(1), reservation(2)
    run(tmp_path, old, "reserve")
    run(tmp_path, old, token=b"old-value")
    complete = (tmp_path / "hosts.yml").read_bytes()
    run(tmp_path, new, "reserve")
    result = run(tmp_path, new, token=b"bad\nvalue", check=False)
    assert result.returncode != 0
    assert (tmp_path / "hosts.yml").read_bytes() == complete
    assert json.loads(run(tmp_path, new, "inspect").stdout) == old
    assert b"bad" not in result.stderr


def test_stale_cleanup_and_foreign_owner_preserve_winner(tmp_path):
    old, new = reservation(1), reservation(2)
    run(tmp_path, old, "reserve")
    run(tmp_path, new, "reserve")
    run(tmp_path, new)
    complete = (tmp_path / "hosts.yml").read_bytes()
    assert run(tmp_path, old, "retire", check=False).returncode != 0
    assert (
        run(
            tmp_path, reservation(999, "different-owner"), "reserve", check=False
        ).returncode
        != 0
    )
    assert (tmp_path / "hosts.yml").read_bytes() == complete
    run(tmp_path, new, "retire")
    assert run(tmp_path, reservation(3), "reserve", check=False).returncode != 0
    assert run(tmp_path, new, check=False).returncode != 0


def test_historical_projection_remains_until_first_ordered_publication(tmp_path):
    legacy = b"github.com:\n    oauth_token: legacy-value\n"
    (tmp_path / "hosts.yml").write_bytes(legacy)
    stamp = reservation(1)
    run(tmp_path, stamp, "reserve")
    assert (tmp_path / "hosts.yml").read_bytes() == legacy
    run(tmp_path, stamp, token=b"new-value")
    assert json.loads(run(tmp_path, stamp, "inspect").stdout) == stamp
    assert b"legacy-value" not in (tmp_path / "hosts.yml").read_bytes()


@pytest.mark.asyncio
async def test_durable_generic_owner_reserves_before_acquisition_and_fences_restart():
    from moonmind.omnigent import runtime_bindings

    store = runtime_bindings.InMemoryStableRuntimeBindingStore()
    first = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "1" * 64,
        idempotency_key="projection-owner",
        provider_leases={},
    )
    _binding, old = await runtime_bindings.reserve_github_projection(store, first)
    observed = await store.get(first.bindingId)
    assert old in [
        x.get("projectionReservation")
        for x in observed.cleanupAuthorityRefs
        if isinstance(x, dict)
    ]
    restarted, newer = await runtime_bindings.reserve_github_projection(store, observed)
    assert newer["revision"] > old["revision"]
    assert newer["ownerRef"] == old["ownerRef"]
    assert newer["reservationId"] != old["reservationId"]
    with pytest.raises(Exception, match="fence|conflict"):
        await runtime_bindings.reserve_github_projection(store, first)
    await store.update(
        restarted.bindingId,
        expected_revision=restarted.revision,
        expected_fencing_generation=restarted.fencingGeneration,
        increment_fence=True,
    )
    with pytest.raises(Exception, match="fence|conflict"):
        await runtime_bindings.reserve_github_projection(store, restarted)


def test_historical_cleanup_cannot_delete_new_reservation(tmp_path):
    legacy = reservation(1, "legacy:owned-volume")
    (tmp_path / "hosts.yml").write_text("github.com:\n    oauth_token: preserved\n")
    new = reservation(2)
    run(tmp_path, new, "reserve")
    run(tmp_path, new)
    contents = (tmp_path / "hosts.yml").read_bytes()
    assert run(tmp_path, legacy, "retire_legacy", check=False).returncode != 0
    assert (tmp_path / "hosts.yml").read_bytes() == contents


def test_historical_cleanup_tombstones_unversioned_volume(tmp_path):
    legacy = reservation(1, "legacy:owned-volume")
    (tmp_path / "hosts.yml").write_text("github.com:\n    oauth_token: old\n")
    run(tmp_path, legacy, "retire_legacy")
    run(
        tmp_path, legacy, "retire_legacy"
    )  # failed volume removal retries the same tombstone
    assert json.loads((tmp_path / ".projection-reservation.json").read_text())[
        "retired"
    ]
    assert run(tmp_path, reservation(2), "reserve", check=False).returncode != 0


def test_stale_failed_publish_cannot_reconcile_against_old_installed_stamp(tmp_path):
    old, new = reservation(1), reservation(2)
    run(tmp_path, old, "reserve")
    run(tmp_path, old)
    run(tmp_path, new, "reserve")
    assert run(tmp_path, old, check=False).returncode != 0
    assert run(tmp_path, old, "inspect", check=False).returncode != 0


@pytest.mark.asyncio
async def test_database_projection_reservation_survives_lost_ack_and_restart(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api_service.db.models import Base
    from moonmind.omnigent.runtime_bindings import (
        DbRuntimeBindingStore,
        reserve_github_projection,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/binding.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        store = DbRuntimeBindingStore(sessions)
        first = await store.create_initial(
            execution_plan_ref="omnigent-execution-plan:sha256:" + "2" * 64,
            idempotency_key="database-projection",
            provider_leases={},
        )
        real_update = store.update

        async def lose_ack(*args, **kwargs):
            await real_update(*args, **kwargs)
            raise OSError("ack lost after database commit")

        store.update = lose_ack
        with pytest.raises(OSError, match="ack lost"):
            await reserve_github_projection(store, first)
        restarted = DbRuntimeBindingStore(sessions)
        observed = await restarted.get(first.bindingId)
        old = observed.cleanupAuthorityRefs[-1]["projectionReservation"]
        final, new = await reserve_github_projection(restarted, observed)
        assert new["revision"] > old["revision"]
        assert new["ownerRef"] == old["ownerRef"]
        assert "token" not in json.dumps(final.model_dump(mode="json")).lower()
        with pytest.raises(Exception, match="fence|conflict"):
            await reserve_github_projection(restarted, first)
    finally:
        await engine.dispose()


class LocalProjectionBackend:
    """Exercise real writer/reader scripts; only Docker volume mounting is modeled."""

    def __init__(self, root, owner, *, lose_publish_ack=False, lose_reserve_ack=False):
        import hashlib

        self.root, self.owner = root, hashlib.sha256(owner.encode()).hexdigest()[:32]
        self.lose_publish_ack, self.lose_reserve_ack = (
            lose_publish_ack,
            lose_reserve_ack,
        )
        self.calls = []

    async def run(self, argv, **kwargs):
        import asyncio

        self.calls.append(argv)
        if argv[1:3] == ["volume", "inspect"]:
            return 0, self.owner, ""
        if argv[1:3] == ["volume", "rm"]:
            return 0, "", ""
        if argv[1] != "run":
            raise AssertionError("unexpected Docker operation")
        script = argv[argv.index("-ceu") + 1].replace("/config", str(self.root))
        args = argv[-4:]
        args[0:2] = [str(os.getuid()), str(os.getgid())]
        result = await asyncio.to_thread(
            subprocess.run,
            ["sh", "-ceu", script, "--", *args],
            input=kwargs.get("input_bytes"),
            capture_output=True,
        )
        if result.returncode:
            raise OSError("projection command rejected")
        if "action = 'publish'" in shlex.split(script)[3] and self.lose_publish_ack:
            self.lose_publish_ack = False
            raise OSError("publication acknowledgement lost")
        if "action = 'reserve'" in shlex.split(script)[3] and self.lose_reserve_ack:
            return 0, "", ""
        return 0, result.stdout.decode(), result.stderr.decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("lose_publish_ack", [False, True])
async def test_generic_acquisition_occurs_after_durable_destination_reservation(
    tmp_path, monkeypatch, lose_publish_ack
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.auth.bound_acquisition import EphemeralCredential
    from tests.unit.omnigent.test_generic_platform_production_services import _request

    stamp = reservation(1)
    backend = LocalProjectionBackend(
        tmp_path, stamp["ownerRef"], lose_publish_ack=lose_publish_ack
    )
    service = credentials.OmnigentGithubCredentialService(backend)
    material = EphemeralCredential(b"ephemeral-selected-value")
    acquisitions = []

    async def acquire(**kwargs):
        state = json.loads((tmp_path / ".projection-reservation.json").read_text())
        assert state["reservation"] == stamp
        assert not (tmp_path / "hosts.yml").exists()
        acquisitions.append(kwargs)
        return SimpleNamespace(
            credential=material, binding=SimpleNamespace(generation=1)
        )

    monkeypatch.setattr(
        service,
        "admitted_repository_identity",
        AsyncMock(return_value=SimpleNamespace(endpoint="https://github.com")),
    )
    monkeypatch.setattr(service, "acquire_repository_use", acquire)
    evidence = []

    async def sink(item):
        evidence.append(item)

    attachment = await service.materialize(
        request=_request(),
        resolved_tools={"tools": ["gh"], "repositoryAccess": {"collaboration": True}},
        owner_ref=stamp["ownerRef"],
        projection_reservation=stamp,
        projection_verifier=AsyncMock(),
        writer_image_ref="trusted-test-image",
        runtime_uid=1000,
        runtime_gid=1000,
        authority_sink=sink,
    )
    assert len(acquisitions) == 1
    assert evidence[0]["projectionReservation"] == stamp
    assert "ephemeral-selected-value" not in json.dumps(evidence)
    assert json.loads(run(tmp_path, stamp, "inspect").stdout) == stamp
    assert attachment["projectionReservation"] == stamp
    assert (
        sum(
            call[1] == "run"
            and "action = 'publish'" in shlex.split(call[call.index("-ceu") + 1])[3]
            for call in backend.calls
        )
        == 1
    )


@pytest.mark.asyncio
async def test_missing_reservation_ack_does_not_acquire_value(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.unit.omnigent.test_generic_platform_production_services import _request

    stamp = reservation(1)
    backend = LocalProjectionBackend(tmp_path, stamp["ownerRef"], lose_reserve_ack=True)
    service = credentials.OmnigentGithubCredentialService(backend)
    acquire = AsyncMock()
    monkeypatch.setattr(
        service,
        "admitted_repository_identity",
        AsyncMock(return_value=SimpleNamespace(endpoint="https://github.com")),
    )
    monkeypatch.setattr(service, "acquire_repository_use", acquire)
    with pytest.raises(json.JSONDecodeError):
        await service.materialize(
            request=_request(),
            resolved_tools={
                "tools": ["gh"],
                "repositoryAccess": {"collaboration": True},
            },
            owner_ref=stamp["ownerRef"],
            projection_reservation=stamp,
            projection_verifier=AsyncMock(),
            writer_image_ref="trusted-test-image",
            runtime_uid=1000,
            runtime_gid=1000,
        )
    acquire.assert_not_called()
    assert not (tmp_path / "hosts.yml").exists()


@pytest.mark.asyncio
async def test_stale_generic_cleanup_never_removes_newer_volume(tmp_path):
    stamp, newer = reservation(1), reservation(2)
    backend = LocalProjectionBackend(tmp_path, stamp["ownerRef"])
    service = credentials.OmnigentGithubCredentialService(backend)
    attachment = service.anticipated_attachment(
        {"tools": ["gh"], "repositoryAccess": {"collaboration": True}},
        owner_ref=stamp["ownerRef"],
    )
    attachment.update(
        projectionReservation=stamp,
        projectionImageRef="trusted-test-image",
        githubHost="github.com",
    )
    run(tmp_path, stamp, "reserve")
    run(tmp_path, newer, "reserve")
    run(tmp_path, newer)
    with pytest.raises(OSError):
        await service.cleanup(attachment)
    assert not any(call[1:3] == ["volume", "rm"] for call in backend.calls)
    assert json.loads(run(tmp_path, newer, "inspect").stdout) == newer


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_stage", ["reserve", "acquire"])
async def test_generic_cleanup_during_wait_cannot_resurrect_projection(
    tmp_path, monkeypatch, cleanup_stage
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.auth.bound_acquisition import EphemeralCredential
    from moonmind.omnigent.runtime_bindings import (
        InMemoryStableRuntimeBindingStore,
        RuntimeBindingState,
        reserve_github_projection,
        validate_github_projection,
    )
    from tests.unit.omnigent.test_generic_platform_production_services import _request

    store = InMemoryStableRuntimeBindingStore()
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "3" * 64,
        idempotency_key="cleanup-race",
        provider_leases={},
    )
    binding, stamp = await reserve_github_projection(store, initial)
    backend = LocalProjectionBackend(tmp_path, stamp["ownerRef"])
    service = credentials.OmnigentGithubCredentialService(backend)

    async def verifier():
        await validate_github_projection(store, binding.bindingId, stamp)

    async def cleanup():
        current = await store.get(binding.bindingId)
        await store.update(
            binding.bindingId,
            expected_revision=current.revision,
            expected_fencing_generation=current.fencingGeneration,
            state=RuntimeBindingState.cleanup_pending,
            increment_fence=True,
        )
        # Docker's named-volume mount can recreate an empty deleted volume.
        # The durable owner must still deny this paused delivery before value use.
        for child in tmp_path.iterdir():
            child.unlink()

    material = EphemeralCredential(b"must-not-reach-cleaned-volume")

    async def acquire(**kwargs):
        if cleanup_stage == "acquire":
            await cleanup()
        return SimpleNamespace(
            credential=material, binding=SimpleNamespace(generation=1)
        )

    acquire_mock = AsyncMock(side_effect=acquire)
    monkeypatch.setattr(
        service,
        "admitted_repository_identity",
        AsyncMock(return_value=SimpleNamespace(endpoint="https://github.com")),
    )
    monkeypatch.setattr(service, "acquire_repository_use", acquire_mock)
    original = backend.run

    async def paused_run(argv, **kwargs):
        if (
            cleanup_stage == "reserve"
            and argv[1] == "run"
            and "action = 'reserve'" in shlex.split(argv[argv.index("-ceu") + 1])[3]
        ):
            await cleanup()
        return await original(argv, **kwargs)

    backend.run = paused_run
    with pytest.raises(Exception, match="owner|fence"):
        await service.materialize(
            request=_request(),
            resolved_tools={
                "tools": ["gh"],
                "repositoryAccess": {"collaboration": True},
            },
            owner_ref=stamp["ownerRef"],
            projection_reservation=stamp,
            projection_verifier=verifier,
            writer_image_ref="trusted-test-image",
            runtime_uid=1000,
            runtime_gid=1000,
        )
    assert acquire_mock.await_count == (1 if cleanup_stage == "acquire" else 0)
    assert not (tmp_path / "hosts.yml").exists()
    assert not any(
        "action = 'publish'" in shlex.split(call[call.index("-ceu") + 1])[3]
        for call in backend.calls
        if call[1] == "run"
    )


def test_missing_reservation_metadata_cannot_roll_back_installed_stamp(tmp_path):
    old, new = reservation(1), reservation(2)
    run(tmp_path, new, "reserve")
    run(tmp_path, new)
    installed = (tmp_path / "hosts.yml").read_bytes()
    (tmp_path / ".projection-reservation.json").unlink()
    assert run(tmp_path, old, "reserve", check=False).returncode != 0
    assert (tmp_path / "hosts.yml").read_bytes() == installed


@pytest.mark.asyncio
async def test_independent_generation_one_acquirers_cannot_reverse_publication(
    tmp_path, monkeypatch
):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.auth.bound_acquisition import EphemeralCredential
    from moonmind.omnigent.runtime_bindings import (
        InMemoryStableRuntimeBindingStore,
        reserve_github_projection,
        validate_github_projection,
    )
    from tests.unit.omnigent.test_generic_platform_production_services import _request

    store = InMemoryStableRuntimeBindingStore()
    initial = await store.create_initial(
        execution_plan_ref="omnigent-execution-plan:sha256:" + "4" * 64,
        idempotency_key="independent-caches",
        provider_leases={},
    )
    current, old = await reserve_github_projection(store, initial)
    acquiring, release_old = asyncio.Event(), asyncio.Event()
    generations = []

    async def project(stamp, token, delayed=False):
        service = credentials.OmnigentGithubCredentialService(
            LocalProjectionBackend(tmp_path, stamp["ownerRef"])
        )

        async def verify():
            await validate_github_projection(store, current.bindingId, stamp)

        async def acquire(**kwargs):
            # Each production acquisition constructs its own cache; neither
            # generation1 can establish order across these independent owners.
            generations.append(1)
            value = SimpleNamespace(
                credential=EphemeralCredential(token),
                binding=SimpleNamespace(generation=1),
            )
            if delayed:
                acquiring.set()
                await release_old.wait()
            return value

        monkeypatch.setattr(
            service,
            "admitted_repository_identity",
            AsyncMock(return_value=SimpleNamespace(endpoint="https://github.com")),
        )
        monkeypatch.setattr(service, "acquire_repository_use", acquire)
        return await service.materialize(
            request=_request(),
            resolved_tools={
                "tools": ["gh"],
                "repositoryAccess": {"collaboration": True},
            },
            owner_ref=stamp["ownerRef"],
            projection_reservation=stamp,
            projection_verifier=verify,
            writer_image_ref="trusted-test-image",
            runtime_uid=1000,
            runtime_gid=1000,
        )

    older = asyncio.create_task(project(old, b"older-issuance", delayed=True))
    try:
        await asyncio.wait_for(acquiring.wait(), timeout=5)
        current, new = await reserve_github_projection(store, current)
        await project(new, b"newer-issuance")
        release_old.set()
        with pytest.raises(Exception, match="fence"):
            await older
        assert generations == [1, 1]
        assert b"newer-issuance" in (tmp_path / "hosts.yml").read_bytes()
        assert json.loads(run(tmp_path, new, "inspect").stdout) == new
    finally:
        release_old.set()
        await asyncio.gather(older, return_exceptions=True)
