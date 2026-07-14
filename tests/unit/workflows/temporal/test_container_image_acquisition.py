"""Image acquisition coordination coverage for MoonLadderStudios/MoonMind#3256."""

from __future__ import annotations

import asyncio

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobFailureClass,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.container_image_acquisition import (
    FilesystemImageAcquisitionLock,
    ImageAcquisitionError,
    classify_pull_failure,
    image_lock_key,
    normalize_image_reference,
    parse_resolved_digest,
)
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


# --------------------------------------------------------------------------- #
# Pure helpers: normalization, lock keys, digest parsing, classification.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("image", "identity"),
    [
        ("python", "docker.io/library/python:latest"),
        ("python:3.13", "docker.io/library/python:3.13"),
        ("library/python:3.13", "docker.io/library/python:3.13"),
        ("docker.io/library/python:3.13", "docker.io/library/python:3.13"),
        ("ghcr.io/org/app:1.2", "ghcr.io/org/app:1.2"),
        ("localhost:5000/app:dev", "localhost:5000/app:dev"),
        (f"org/app@{DIGEST_A}", f"docker.io/org/app@{DIGEST_A}"),
    ],
)
def test_normalization_produces_stable_identity(image: str, identity: str) -> None:
    assert normalize_image_reference(image).identity == identity


def test_equivalent_references_share_a_lock_key_but_backends_do_not() -> None:
    a = normalize_image_reference("python")
    b = normalize_image_reference("docker.io/library/python:latest")
    assert image_lock_key("system", a) == image_lock_key("system", b)
    assert image_lock_key("system", a) != image_lock_key("other", a)


def test_distinct_images_do_not_share_a_lock_key() -> None:
    system = "system"
    assert image_lock_key(
        system, normalize_image_reference("python:3.13")
    ) != image_lock_key(system, normalize_image_reference("python:3.12"))


def test_digest_parsing_prefers_repo_digest_then_id() -> None:
    assert parse_resolved_digest(f"repo@{DIGEST_A}", DIGEST_B) == DIGEST_A
    assert parse_resolved_digest("", DIGEST_B) == DIGEST_B
    assert parse_resolved_digest("", "not-a-digest") is None


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("manifest unknown", ContainerJobFailureClass.IMAGE_NOT_FOUND),
        ("repository does not exist", ContainerJobFailureClass.IMAGE_NOT_FOUND),
        ("unauthorized: authentication required", ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED),
        ("net/http: request canceled (timeout)", ContainerJobFailureClass.IMAGE_PULL_TIMEOUT),
        ("no matching manifest for linux/arm64", ContainerJobFailureClass.IMAGE_PLATFORM_MISMATCH),
        ("Cannot connect to the Docker daemon", ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE),
        ("something unexpected", ContainerJobFailureClass.IMAGE),
    ],
)
def test_pull_failure_classification(stderr: str, expected) -> None:
    assert classify_pull_failure(stderr) == expected


# --------------------------------------------------------------------------- #
# Filesystem lease: mutual exclusion, ownership-scoped release, expiry recovery.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lease_is_mutually_exclusive_and_release_is_owner_scoped(tmp_path) -> None:
    lock = FilesystemImageAcquisitionLock(tmp_path, clock=lambda: 0.0)
    assert await lock.try_acquire("k", ttl_seconds=60, owner_id="a")
    assert not await lock.try_acquire("k", ttl_seconds=60, owner_id="b")
    # A non-owner cannot release the lease out from under the owner.
    await lock.release("k", "b")
    assert not await lock.try_acquire("k", ttl_seconds=60, owner_id="b")
    await lock.release("k", "a")
    assert await lock.try_acquire("k", ttl_seconds=60, owner_id="b")


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_without_permanent_block(tmp_path) -> None:
    now = {"t": 0.0}
    lock = FilesystemImageAcquisitionLock(tmp_path, clock=lambda: now["t"])
    assert await lock.try_acquire("k", ttl_seconds=10, owner_id="dead-owner")
    now["t"] = 5.0
    assert not await lock.try_acquire("k", ttl_seconds=10, owner_id="later")
    now["t"] = 20.0  # the owning worker died; its lease has expired.
    assert await lock.try_acquire("k", ttl_seconds=10, owner_id="later")


@pytest.mark.asyncio
async def test_recent_empty_lease_preserves_creator_claim(tmp_path) -> None:
    now = 100.0
    lease = tmp_path / "k.lease"
    lease.touch()
    lease.chmod(0o600)
    lock = FilesystemImageAcquisitionLock(tmp_path, clock=lambda: now)

    # Simulate the gap between O_EXCL creation and writing the lease payload.
    assert not await lock.try_acquire("k", ttl_seconds=10, owner_id="contender")


# --------------------------------------------------------------------------- #
# Backend acquisition scenarios.
# --------------------------------------------------------------------------- #


def _request(
    image: str,
    *,
    policy: str = "if-missing",
    workspace: str | None = "ws:resolved",
) -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "resolvedWorkspaceRef": workspace,
            "request": {
                "idempotencyKey": "issue-3256",
                "source": {"source": "workflow", "workflowId": "mm:3256"},
                "spec": {
                    "image": image,
                    "pullPolicy": policy,
                        "workspaceRef": {
                            "kind": "sandbox",
                            "workspaceId": "art_workspace",
                        },
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )


class FakeDaemon:
    """In-memory Docker CLI stand-in tracking presence and pull commands."""

    def __init__(self, present: dict[str, tuple[str, str]] | None = None) -> None:
        # image -> (image_id, repo_digests_csv)
        self.present: dict[str, tuple[str, str]] = dict(present or {})
        self.pulls: list[str] = []
        self.pull_fails_with: bytes | None = None
        self._pull_hook = None

    def set_pull_hook(self, hook) -> None:
        self._pull_hook = hook

    async def runner(self, args):
        args = tuple(args)
        if args[:2] == ("image", "inspect"):
            image = args[-1]
            if image in self.present:
                image_id, repo = self.present[image]
                return 0, f"{image_id}\t{repo}".encode(), b""
            return 1, b"", b"No such image"
        if args[0] == "pull":
            image = args[1]
            self.pulls.append(image)
            if self._pull_hook is not None:
                await self._pull_hook(image)
            if self.pull_fails_with is not None:
                return 1, b"", self.pull_fails_with
            self.present.setdefault(image, (DIGEST_A, f"{image}@{DIGEST_A}"))
            return 0, b"pull progress\n" * 3, b""
        return 0, b"", b""


def _backend(daemon: FakeDaemon, tmp_path, **kwargs) -> DockerContainerJobBackend:
    return DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=daemon.runner,
        image_lock_root=tmp_path / "locks",
        pull_lock_poll_seconds=0.01,
        pull_lock_max_wait_seconds=5.0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_if_missing_present_is_cache_hit_without_pull(tmp_path) -> None:
    daemon = FakeDaemon({"python:3.13": (DIGEST_A, f"python@{DIGEST_A}")})
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(_request("python:3.13"))
    assert daemon.pulls == []
    obs = result.image_observation
    assert obs.cache_present and obs.cache_hit
    assert obs.resolved_digest == DIGEST_A
    assert obs.pull_duration_ms is None
    assert result.resolved_image_ref == DIGEST_A


@pytest.mark.asyncio
async def test_if_missing_absent_pulls_and_records_duration(tmp_path) -> None:
    daemon = FakeDaemon()
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(_request("python:3.13"))
    assert daemon.pulls == ["python:3.13"]
    obs = result.image_observation
    assert not obs.cache_present
    assert not obs.cache_hit
    assert obs.resolved_digest == DIGEST_A
    assert obs.pull_duration_ms is not None and obs.pull_duration_ms >= 0


@pytest.mark.asyncio
async def test_never_without_image_fails_image_not_found_without_pull(tmp_path) -> None:
    daemon = FakeDaemon()
    backend = _backend(daemon, tmp_path)
    with pytest.raises(ImageAcquisitionError) as excinfo:
        await backend.acquire_image(_request("python:3.13", policy="never"))
    assert excinfo.value.failure_class == ContainerJobFailureClass.IMAGE_NOT_FOUND
    assert excinfo.value.terminal is True
    assert daemon.pulls == []


@pytest.mark.asyncio
async def test_never_preserves_backend_inspect_failure(tmp_path) -> None:
    async def unavailable(args):
        return 1, b"", b"Cannot connect to the Docker daemon"

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=unavailable,
        image_lock_root=tmp_path / "locks",
    )
    with pytest.raises(ImageAcquisitionError) as excinfo:
        await backend.acquire_image(_request("python:3.13", policy="never"))
    assert (
        excinfo.value.failure_class
        == ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_never_with_present_image_is_cache_hit(tmp_path) -> None:
    daemon = FakeDaemon({"python:3.13": (DIGEST_A, f"python@{DIGEST_A}")})
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(_request("python:3.13", policy="never"))
    assert daemon.pulls == []
    assert result.image_observation.cache_hit


@pytest.mark.asyncio
async def test_always_refreshes_present_image_and_records_digest(tmp_path) -> None:
    daemon = FakeDaemon({"python:3.13": (DIGEST_A, f"python@{DIGEST_A}")})
    backend = _backend(daemon, tmp_path)
    result = await backend.acquire_image(_request("python:3.13", policy="always"))
    assert daemon.pulls == ["python:3.13"]
    obs = result.image_observation
    assert obs.cache_present  # present at start
    assert not obs.cache_hit  # but refreshed anyway
    assert obs.resolved_digest == DIGEST_A


@pytest.mark.asyncio
async def test_acquire_requires_resolved_workspace_before_pull(tmp_path) -> None:
    daemon = FakeDaemon()
    backend = _backend(daemon, tmp_path)
    with pytest.raises(ImageAcquisitionError) as excinfo:
        await backend.acquire_image(_request("python:3.13", workspace=None))
    assert excinfo.value.failure_class == ContainerJobFailureClass.WORKSPACE
    assert daemon.pulls == []


@pytest.mark.asyncio
async def test_pull_failure_is_classified_and_terminal(tmp_path) -> None:
    daemon = FakeDaemon()
    daemon.pull_fails_with = b"manifest unknown: manifest unknown"
    backend = _backend(daemon, tmp_path)
    with pytest.raises(ImageAcquisitionError) as excinfo:
        await backend.acquire_image(_request("python:3.13"))
    assert excinfo.value.failure_class == ContainerJobFailureClass.IMAGE_NOT_FOUND


@pytest.mark.asyncio
async def test_concurrent_jobs_pull_missing_image_once(tmp_path) -> None:
    daemon = FakeDaemon()
    slow = asyncio.Event()

    async def hook(image: str) -> None:
        # Hold the pull long enough that the second job must wait on the lease.
        await asyncio.sleep(0.05)
        slow.set()

    daemon.set_pull_hook(hook)
    backend = _backend(daemon, tmp_path)
    results = await asyncio.gather(
        backend.acquire_image(_request("python:3.13")),
        backend.acquire_image(_request("python:3.13")),
    )
    assert daemon.pulls == ["python:3.13"]  # exactly one acquisition
    hits = [r.image_observation.cache_hit for r in results]
    # One job owned the pull; the other waited and reused the result.
    assert sorted(hits) == [False, True]
    waiter = next(r for r in results if r.image_observation.cache_hit)
    assert waiter.image_observation.pull_lock_wait_ms >= 0


@pytest.mark.asyncio
async def test_concurrent_jobs_for_different_images_are_not_serialized(tmp_path) -> None:
    daemon = FakeDaemon()
    backend = _backend(daemon, tmp_path)
    await asyncio.gather(
        backend.acquire_image(_request("python:3.13")),
        backend.acquire_image(_request("node:20")),
    )
    assert sorted(daemon.pulls) == ["node:20", "python:3.13"]


@pytest.mark.asyncio
async def test_lease_alone_is_not_proof_owner_reinspects_before_pull(tmp_path) -> None:
    # The image appears (another worker pulled it) between the initial inspect
    # and winning the lease; the owner must re-inspect and skip its own pull.
    daemon = FakeDaemon()
    backend = _backend(daemon, tmp_path)

    original_inspect = backend._inspect_image
    calls = {"n": 0}

    async def inspect(image: str):
        calls["n"] += 1
        if calls["n"] == 2:
            daemon.present[image] = (DIGEST_A, f"{image}@{DIGEST_A}")
        return await original_inspect(image)

    backend._inspect_image = inspect  # type: ignore[assignment]
    result = await backend.acquire_image(_request("python:3.13"))
    assert daemon.pulls == []
    assert result.image_observation.cache_hit


# --------------------------------------------------------------------------- #
# Activity boundary: granular failure classes surface as ApplicationError types.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_activity_translates_image_error_to_application_error() -> None:
    class Backend:
        async def acquire_image(self, request):
            raise ImageAcquisitionError(
                "absent", failure_class=ContainerJobFailureClass.IMAGE_NOT_FOUND
            )

    activities = TemporalAgentRuntimeActivities(container_job_backend=Backend())
    req = _request("python:3.13", policy="never")
    payload = req.model_dump(mode="json", by_alias=True, exclude_none=True)
    with pytest.raises(ApplicationError) as excinfo:
        await activities.container_job_acquire_image(payload)
    assert excinfo.value.type == "image_not_found"
    assert excinfo.value.non_retryable is True


@pytest.mark.asyncio
async def test_activity_marks_transient_image_error_retryable() -> None:
    class Backend:
        async def acquire_image(self, request):
            raise ImageAcquisitionError(
                "backend down",
                failure_class=ContainerJobFailureClass.IMAGE_BACKEND_UNAVAILABLE,
            )

    activities = TemporalAgentRuntimeActivities(container_job_backend=Backend())
    payload = _request("python:3.13").model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    with pytest.raises(ApplicationError) as excinfo:
        await activities.container_job_acquire_image(payload)
    assert excinfo.value.type == "image_backend_unavailable"
    assert excinfo.value.non_retryable is False


@pytest.mark.asyncio
async def test_activity_preserves_image_diagnostics_reference() -> None:
    class Backend:
        async def acquire_image(self, request):
            raise ImageAcquisitionError(
                "pull failed",
                failure_class=ContainerJobFailureClass.IMAGE_PULL_AUTH_FAILED,
                diagnostics_ref="artifact://pull-diagnostics",
            )

    activities = TemporalAgentRuntimeActivities(container_job_backend=Backend())
    payload = _request("python:3.13").model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    with pytest.raises(ApplicationError) as excinfo:
        await activities.container_job_acquire_image(payload)
    assert excinfo.value.details == ({"diagnosticsRef": "artifact://pull-diagnostics"},)
