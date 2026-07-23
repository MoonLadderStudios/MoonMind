from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from moonmind.config.container_backend_settings import (
    LocalImageRecipe,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import ContainerJobActivityRequest
from moonmind.workflows.temporal.container_job_backend import (
    LABEL_IMAGE_BUILT_AT,
    DockerContainerJobBackend,
)

JOB_ID = "container-job:0123456789abcdef0123456789abcdef"
IMAGE_ID = "sha256:" + "a" * 64


def _recipe(tmp_path) -> LocalImageRecipe:
    (tmp_path / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (tmp_path / "dependencies.lock").write_text("pytest==1\n", encoding="utf-8")
    (tmp_path / "runtime.py").write_text("print('one')\n", encoding="utf-8")
    return LocalImageRecipe(
        source_ref="moonmind-python-tests",
        image="moonmind-python-tests:local",
        context_root=tmp_path,
        dockerfile="Dockerfile",
        target="test-runtime",
        build_args=(("INSTALL_TEST_DEPS", "true"),),
        fingerprint_inputs=("Dockerfile", "dependencies.lock"),
        recipe_version="test-v1",
        max_age_seconds=None,
        validation_command=("python", "-c", "import pytest"),
    )


def _request() -> ContainerJobActivityRequest:
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "resolvedWorkspaceRef": "workspace:resolved",
            "request": {
                "idempotencyKey": "local-build",
                "source": {"source": "workflow", "workflowId": "mm:local-build"},
                "spec": {
                    "imageSourceRef": "moonmind-python-tests",
                    "workspaceRef": {
                        "kind": "sandbox",
                        "workspaceId": "workspace",
                    },
                    "resources": {"cpuMillis": 1000, "memoryMiB": 512},
                },
            },
        }
    )


class _LocalBuildDaemon:
    def __init__(self, *, build_delay: float = 0.0) -> None:
        self.labels: dict[str, str] | None = None
        self.builds = 0
        self.validations = 0
        self.build_delay = build_delay

    async def runner(self, args):
        args = tuple(args)
        if args[:2] == ("version", "--format"):
            return 0, b"linux/amd64", b""
        if args[:2] == ("image", "inspect"):
            if self.labels is None:
                return 1, b"", b"No such image"
            return (
                0,
                json.dumps(
                    {
                        "id": IMAGE_ID,
                        "repoDigests": [],
                        "created": "2026-07-22T00:00:00Z",
                        "os": "linux",
                        "architecture": "amd64",
                        "labels": self.labels,
                    }
                ).encode(),
                b"",
            )
        if args[0] == "build":
            self.builds += 1
            if self.build_delay:
                await asyncio.sleep(self.build_delay)
            labels: dict[str, str] = {}
            for index, token in enumerate(args):
                if token == "--label":
                    name, _, value = args[index + 1].partition("=")
                    labels[name] = value
            self.labels = labels
            return 0, b"build complete", b""
        if args[0] == "run":
            self.validations += 1
            return 0, b"", b""
        raise AssertionError(f"unexpected Docker command: {args}")


def _backend(tmp_path, recipe, daemon, **kwargs) -> DockerContainerJobBackend:
    settings = replace(
        resolve_container_backend_settings({}),
        image_sources=(recipe,),
    )
    return DockerContainerJobBackend(
        workspace_root=tmp_path / "workspaces",
        settings=settings,
        command_runner=daemon.runner,
        image_lock_root=tmp_path / "locks",
        pull_lock_poll_seconds=0.001,
        pull_lock_max_wait_seconds=2.0,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_local_recipe_builds_once_and_reuses_matching_build_key(
    tmp_path,
) -> None:
    recipe = _recipe(tmp_path)
    daemon = _LocalBuildDaemon()
    backend = _backend(tmp_path, recipe, daemon)

    first = await backend.acquire_image(_request())
    second = await backend.acquire_image(_request())

    assert daemon.builds == 1
    assert daemon.validations == 2
    assert first.resolved_image_ref == IMAGE_ID
    assert first.image_observation.provision_action == "build"
    assert first.image_observation.fresh_at_start is False
    assert second.image_observation.provision_action == "reuse"
    assert second.image_observation.fresh_at_start is True
    assert first.image_observation.build_key == second.image_observation.build_key


@pytest.mark.asyncio
async def test_only_declared_effective_inputs_invalidate_local_image(
    tmp_path,
) -> None:
    recipe = _recipe(tmp_path)
    daemon = _LocalBuildDaemon()
    backend = _backend(tmp_path, recipe, daemon)

    first = await backend.acquire_image(_request())
    (tmp_path / "runtime.py").write_text("print('two')\n", encoding="utf-8")
    source_only = await backend.acquire_image(_request())
    (tmp_path / "dependencies.lock").write_text("pytest==2\n", encoding="utf-8")
    dependency_change = await backend.acquire_image(_request())

    assert daemon.builds == 2
    assert first.image_observation.build_key == source_only.image_observation.build_key
    assert (
        dependency_change.image_observation.build_key
        != first.image_observation.build_key
    )


@pytest.mark.asyncio
async def test_configured_max_age_refreshes_an_unchanged_recipe(tmp_path) -> None:
    recipe = replace(_recipe(tmp_path), max_age_seconds=60)
    daemon = _LocalBuildDaemon()
    backend = _backend(tmp_path, recipe, daemon)

    await backend.acquire_image(_request())
    assert daemon.labels is not None
    daemon.labels[LABEL_IMAGE_BUILT_AT] = "2000-01-01T00:00:00Z"
    refreshed = await backend.acquire_image(_request())

    assert daemon.builds == 2
    assert refreshed.image_observation.provision_action == "build"


@pytest.mark.asyncio
async def test_concurrent_local_provisioning_is_coalesced_by_build_key(
    tmp_path,
) -> None:
    recipe = _recipe(tmp_path)
    daemon = _LocalBuildDaemon(build_delay=0.02)
    first_backend = _backend(tmp_path, recipe, daemon)
    second_backend = _backend(tmp_path, recipe, daemon)

    results = await asyncio.gather(
        first_backend.acquire_image(_request()),
        second_backend.acquire_image(_request()),
    )

    assert daemon.builds == 1
    assert sorted(result.image_observation.provision_action for result in results) == [
        "build",
        "reuse",
    ]


@pytest.mark.asyncio
async def test_build_status_projection_failure_does_not_replace_provision_success(
    tmp_path,
) -> None:
    recipe = _recipe(tmp_path)
    daemon = _LocalBuildDaemon()

    async def fail_projection(_request) -> None:
        raise RuntimeError("projection unavailable")

    backend = _backend(
        tmp_path,
        recipe,
        daemon,
        projection_writer=fail_projection,
    )

    result = await backend.acquire_image(_request())

    assert result.image_observation.provision_action == "build"
    assert daemon.builds == 1
