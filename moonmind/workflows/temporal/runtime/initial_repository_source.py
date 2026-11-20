"""Initial local Git sources exported by their existing filesystem owner.

The original source is never a runtime mount. Only a frozen, scanned artifact
crosses the worker boundary; native directory-copy requests remain distinct.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field

CONTENT_TYPE = "application/vnd.moonmind.initial-repository-source+json;version=1"


class LocalRepositorySourceUnavailable(ValueError):
    """A native-supported source feature outside this bounded export contract."""


class InitialRepositorySourceSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)
    artifact_ref: str = Field(alias="artifactRef", pattern=r"^artifact:")
    content_digest: str = Field(alias="contentDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    source_intent_digest: str = Field(
        alias="sourceIntentDigest", pattern=r"^sha256:[0-9a-f]{64}$"
    )


def source_intent(request):
    """Pure eligibility/identity, never permission to open a worker path."""
    from moonmind.omnigent.workspace_intent import authored_repository_source
    from moonmind.omnigent.workspace_sources import compile_workspace_source

    if (
        not request.canonical_repository_workspace_handoff
        or request.step_execution is None
    ):
        return None
    if request.checkpoint_recovery or request.remediation_workspace:
        return None
    parameters = request.parameters or {}
    if str(parameters.get("publishMode") or "none").lower() != "none":
        return None
    if parameters.get("repositoryToolCapabilities"):
        return None
    workspace = request.workspace_spec
    if (workspace.get("workspaceSource") or {}).get("kind") != "repository":
        return None
    compiled = compile_workspace_source(workspace)
    if not compiled.repository_branch:
        return None
    raw = authored_repository_source(request)
    if not raw:
        return None
    if raw.startswith("file://"):
        parsed = urlsplit(raw)
        if parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment:
            return None
        path = unquote(parsed.path)
    elif raw.startswith("/"):
        path = raw
    else:
        return None
    # Relative paths depend on a worker's current directory, not source identity.
    if not Path(path).is_absolute():
        return None
    return {
        "repository": raw,
        "sourcePath": path,
        "branch": compiled.repository_branch,
        "commitSha": compiled.repository_commit_sha,
        "workflowId": request.step_execution.workflow_id,
        "runId": request.step_execution.run_id,
        "logicalStepId": request.step_execution.logical_step_id,
    }


def supports_detached_local_pin(request, intent, *, frozen=False):
    """Select only the launcher's typed, detached, default-connection read."""
    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
        RepositoryContractError,
        authored_repository_access,
        compile_repository_target,
    )

    workspace = request.workspace_spec
    raw_target = workspace.get("repositoryTarget")
    if frozen and raw_target is None:
        # The original API draft may still use its typed repository alias.
        raw_target = workspace.get("repository")
    try:
        target = compile_repository_target(raw_target)
    except RepositoryContractError:
        return False
    if (
        target.provider != "git"
        or target.connection_ref != DEFAULT_GIT_CONNECTION_REF
        or target.revision is None
        or target.revision.commit_sha.lower() != intent["commitSha"]
        or target.branch.name != intent["branch"]
        or target.branch.name.startswith(
            ("refs/heads/", "refs/remotes/origin/", "origin/")
        )
        or target.repository.name != intent["repository"]
        or str(workspace.get("targetBranch") or "").strip()
    ):
        return False

    source = workspace.get("workspaceSource") or {}
    if not isinstance(source, Mapping):
        return False
    # Do not let a projection's preferred alias hide an explicit restriction
    # on the same source in another authored alias.
    declarations = (
        workspace,
        source,
        workspace.get("repository"),
        raw_target,
        source.get("repository"),
        source.get("repositoryTarget"),
    )
    default = (None, "", DEFAULT_GIT_CONNECTION_REF)
    connection, anonymous = authored_repository_access(request.parameters or {})
    return (
        connection in default
        and not anonymous
        and not any(
            isinstance(row, Mapping)
            and (
                row.get("connectionRef") not in default
                or row.get("accessMode") == "anonymous"
            )
            for row in declarations
        )
    )


def intent_digest(intent):
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


async def read_initial_source(artifact_service, snapshot, *, access):
    """Read the plan-bound initial source, never reinterpret a step checkpoint."""
    from moonmind.core.artifacts import temporal_artifact_id
    from moonmind.schemas.checkpoint_restore_models import WorktreeArchiveCheckpoint

    selected = InitialRepositorySourceSnapshot.model_validate(snapshot)
    metadata, *_ = await artifact_service.get_metadata(
        artifact_id=temporal_artifact_id(selected.artifact_ref),
        access=access,
    )
    # Use the existing bounded object read; retain type and byte identity checks
    # even when the backing store has already verified the content address.
    if (
        metadata.content_type != CONTENT_TYPE
        or not 0 < metadata.size_bytes <= 4 * 1024 * 1024
    ):
        raise ValueError(
            "initial repository source has an invalid descriptor type or size"
        )
    _, body = await artifact_service.read(
        artifact_id=temporal_artifact_id(selected.artifact_ref),
        access=access,
    )
    if "sha256:" + hashlib.sha256(body).hexdigest() != selected.content_digest:
        raise ValueError("initial repository source descriptor digest differs")
    descriptor = json.loads(body)
    if (
        descriptor.get("schemaVersion") != "v1"
        or descriptor.get("kind") != "initial_repository_source"
        or descriptor.get("sourceIntentDigest") != selected.source_intent_digest
        or not isinstance(descriptor.get("source"), dict)
        or not isinstance(descriptor.get("gitRefs"), dict)
    ):
        raise ValueError("initial repository source descriptor is invalid")
    WorktreeArchiveCheckpoint.model_validate(descriptor.get("workspace"))
    return descriptor
