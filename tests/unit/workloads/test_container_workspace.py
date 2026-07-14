"""Owner-side container-job workspace resolution coverage (MoonMind#3255)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobFailureClass,
)
from moonmind.schemas.workspace_locator_models import (
    CONTAINER_WORKSPACE_NOT_FOUND,
    CONTAINER_WORKSPACE_PERMISSION_DENIED,
)
from moonmind.workloads.container_workspace import (
    ARTIFACTS_TARGET,
    SCRATCH_TARGET,
    WORKSPACE_TARGET,
    ApprovedWorkspaceMapping,
    ContainerWorkspaceError,
    ContainerWorkspaceResolver,
)

JOB_ID = "container-job:" + "a" * 32


def _request(
    *,
    workspace_ref: dict,
    source: dict,
    caches: list | None = None,
) -> ContainerJobActivityRequest:
    spec = {
        "image": "python:3.13",
        "workspaceRef": workspace_ref,
        "resources": {"cpuMillis": 1000, "memoryMiB": 512},
    }
    if caches is not None:
        spec["caches"] = caches
    return ContainerJobActivityRequest.model_validate(
        {
            "jobId": JOB_ID,
            "ownershipToken": f"{JOB_ID}:v1",
            "request": {
                "idempotencyKey": "k",
                "source": source,
                "spec": spec,
            },
        }
    )


def _resolver(root: Path, **kwargs) -> ContainerWorkspaceResolver:
    return ContainerWorkspaceResolver(
        mapping=ApprovedWorkspaceMapping.from_workspace_root(root, **kwargs)
    )


def test_resolves_correlated_omnigent_workspace_to_fixed_targets(tmp_path) -> None:
    omnigent = tmp_path / "omni"
    (omnigent / "sess" / "repo").mkdir(parents=True)
    resolver = _resolver(tmp_path, omnigent_worktree_root=omnigent)
    request = _request(
        workspace_ref={"kind": "omnigent-session", "sessionId": "sess"},
        source={"source": "omnigent", "omnigentSessionId": "sess"},
    )
    plan = resolver.resolve(request)

    targets = {mount.target for mount in plan.mounts}
    assert {WORKSPACE_TARGET, ARTIFACTS_TARGET, SCRATCH_TARGET} <= targets
    assert plan.workspace_source == str((omnigent / "sess" / "repo").resolve())
    assert Path(plan.artifacts_source).is_dir()
    assert Path(plan.scratch_source).is_dir()
    # The opaque handle never encodes a host path.
    handle = resolver.opaque_handle(request)
    assert handle.startswith("container-workspace://")
    assert str(tmp_path) not in handle


def test_cross_session_omnigent_reference_is_permission_denied(tmp_path) -> None:
    omnigent = tmp_path / "omni"
    (omnigent / "victim" / "repo").mkdir(parents=True)
    resolver = _resolver(tmp_path, omnigent_worktree_root=omnigent)
    request = _request(
        workspace_ref={"kind": "omnigent-session", "sessionId": "victim"},
        source={"source": "omnigent", "omnigentSessionId": "attacker"},
    )
    with pytest.raises(ContainerWorkspaceError) as excinfo:
        resolver.resolve(request)
    assert excinfo.value.code == CONTAINER_WORKSPACE_PERMISSION_DENIED
    assert excinfo.value.failure_class == ContainerJobFailureClass.AUTHORIZATION


def test_absent_workspace_is_workspace_not_found(tmp_path) -> None:
    resolver = _resolver(tmp_path)
    request = _request(
        workspace_ref={"kind": "artifact-workspace", "artifactRef": "missing"},
        source={"source": "workflow"},
    )
    with pytest.raises(ContainerWorkspaceError) as excinfo:
        resolver.resolve(request)
    assert excinfo.value.code == CONTAINER_WORKSPACE_NOT_FOUND
    assert excinfo.value.failure_class == ContainerJobFailureClass.WORKSPACE


def test_symlink_escape_is_rejected(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").mkdir()
    root = tmp_path / "root"
    root.mkdir()
    # An identity directory whose "repo" child is a symlink pointing outside.
    (root / "art").mkdir()
    os.symlink(outside / "secret", root / "art" / "repo")
    resolver = ContainerWorkspaceResolver(
        mapping=ApprovedWorkspaceMapping(
            run_root=root,
            managed_session_root=root,
            omnigent_worktree_root=root,
            artifact_workspace_root=root,
            job_scratch_root=tmp_path / "scratch",
        )
    )
    request = _request(
        workspace_ref={"kind": "artifact-workspace", "artifactRef": "art"},
        source={"source": "workflow"},
    )
    with pytest.raises(ContainerWorkspaceError) as excinfo:
        resolver.resolve(request)
    assert excinfo.value.code == CONTAINER_WORKSPACE_PERMISSION_DENIED


def test_cache_target_collision_with_reserved_target_is_rejected(tmp_path) -> None:
    (tmp_path / "art" / "repo").mkdir(parents=True)
    resolver = _resolver(tmp_path)
    request = _request(
        workspace_ref={"kind": "artifact-workspace", "artifactRef": "art"},
        source={"source": "workflow"},
        caches=[{"cacheRef": "pip", "target": ARTIFACTS_TARGET}],
    )
    with pytest.raises(ContainerWorkspaceError) as excinfo:
        resolver.resolve(request)
    assert excinfo.value.code == CONTAINER_WORKSPACE_PERMISSION_DENIED


def test_cache_mounts_get_distinct_job_owned_sources(tmp_path) -> None:
    (tmp_path / "art" / "repo").mkdir(parents=True)
    resolver = _resolver(tmp_path)
    request = _request(
        workspace_ref={"kind": "artifact-workspace", "artifactRef": "art"},
        source={"source": "workflow"},
        caches=[
            {"cacheRef": "pip", "target": "/root/.cache/pip"},
            {"cacheRef": "npm", "target": "/root/.cache/npm", "readOnly": True},
        ],
    )
    plan = resolver.resolve(request)
    cache_mounts = [m for m in plan.mounts if m.mount_class == "cache"]
    assert len(cache_mounts) == 2
    assert {m.target for m in cache_mounts} == {"/root/.cache/pip", "/root/.cache/npm"}
    assert cache_mounts[1].read_only is True
    # No cache source escapes the job-owned scratch root.
    for mount in cache_mounts:
        assert str((tmp_path / ".container-job-scratch").resolve()) in mount.source
