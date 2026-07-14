"""Typed, workflow-safe identities for workspace ownership boundaries."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


def _relative_subpath(value: str) -> str:
    candidate = str(value).strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if not candidate or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("relativePath must be a normalized relative path without traversal")
    return str(path)


class SandboxWorkspaceLocator(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["sandbox"] = "sandbox"
    workspace_id: str = Field(..., alias="workspaceId", min_length=1, max_length=200)
    relative_path: str = Field("repo", alias="relativePath", max_length=1000)

    _validate_relative_path = field_validator("relative_path", mode="before")(_relative_subpath)


class ManagedWorkspaceLocator(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["managed_runtime"] = "managed_runtime"
    runtime_id: str = Field(..., alias="runtimeId", min_length=1, max_length=200)
    agent_run_id: str = Field(..., alias="agentRunId", min_length=1, max_length=300)
    relative_path: str = Field("repo", alias="relativePath", max_length=1000)

    _validate_relative_path = field_validator("relative_path", mode="before")(_relative_subpath)


class ExternalStateLocator(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["external_state"] = "external_state"
    artifact_ref: str = Field(..., alias="artifactRef", min_length=1, max_length=2000)


WorkspaceLocator = Annotated[
    SandboxWorkspaceLocator | ManagedWorkspaceLocator | ExternalStateLocator,
    Field(discriminator="kind"),
]
WORKSPACE_LOCATOR_ADAPTER = TypeAdapter(WorkspaceLocator)


class WorkspaceLocatorResolutionError(ValueError):
    """Stable authority-boundary failure suitable for activity error handling."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


WORKSPACE_AUTHORITY_MISMATCH = "WORKSPACE_AUTHORITY_MISMATCH"
WORKSPACE_IDENTITY_MISMATCH = "WORKSPACE_IDENTITY_MISMATCH"
WORKSPACE_LOCATOR_UNSUPPORTED = "WORKSPACE_LOCATOR_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Container-job workspace source kinds (MoonLadderStudios/MoonMind#3255).
#
# These extend the canonical typed workspace-locator contract above for the
# public container-job submission surface rather than introducing a competing
# Docker-only identity model.  Every kind reuses the shared relative-subpath
# traversal guard and the ``WorkspaceLocatorResolutionError`` taxonomy so that
# owner-side resolution stays uniform across workflow, managed-session,
# Omnigent, and artifact workspaces.
# ---------------------------------------------------------------------------

# Stable, caller-visible classification codes for container-job workspace
# resolution.  Owner-side resolution must fail closed with exactly one of these
# before any image acquisition; resolved host/volume sources never leak.
CONTAINER_WORKSPACE_NOT_FOUND = "workspace_not_found"
CONTAINER_WORKSPACE_PERMISSION_DENIED = "permission_denied"
CONTAINER_WORKSPACE_NOT_VISIBLE = "workspace_not_visible"


class ContainerRunWorkspaceLocator(BaseModel):
    """MoonMind workflow/run workspace referenced by its authoritative run id."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["moonmind-run"] = "moonmind-run"
    run_id: str = Field(..., alias="runId", min_length=1, max_length=300)
    relative_path: str = Field("repo", alias="relativePath", max_length=1000)

    _validate_relative_path = field_validator("relative_path", mode="before")(_relative_subpath)


class ContainerManagedSessionWorkspaceLocator(BaseModel):
    """MoonMind managed-session repository/workspace referenced by session id."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["moonmind-session"] = "moonmind-session"
    session_id: str = Field(..., alias="sessionId", min_length=1, max_length=300)
    relative_path: str = Field("repo", alias="relativePath", max_length=1000)

    _validate_relative_path = field_validator("relative_path", mode="before")(_relative_subpath)


class ContainerOmnigentWorkspaceLocator(BaseModel):
    """Omnigent session/conversation worktree referenced by session id."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["omnigent-session"] = "omnigent-session"
    session_id: str = Field(..., alias="sessionId", min_length=1, max_length=300)
    conversation_id: str | None = Field(None, alias="conversationId", max_length=300)
    relative_path: str = Field("repo", alias="relativePath", max_length=1000)

    _validate_relative_path = field_validator("relative_path", mode="before")(_relative_subpath)


class ContainerArtifactWorkspaceLocator(BaseModel):
    """Approved artifact-materialization workspace referenced by artifact ref."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["artifact-workspace"] = "artifact-workspace"
    artifact_ref: str = Field(..., alias="artifactRef", min_length=1, max_length=2000)
    relative_path: str = Field("repo", alias="relativePath", max_length=1000)

    _validate_relative_path = field_validator("relative_path", mode="before")(_relative_subpath)


ContainerWorkspaceLocator = Annotated[
    ContainerRunWorkspaceLocator
    | ContainerManagedSessionWorkspaceLocator
    | ContainerOmnigentWorkspaceLocator
    | ContainerArtifactWorkspaceLocator,
    Field(discriminator="kind"),
]
CONTAINER_JOB_WORKSPACE_ADAPTER = TypeAdapter(ContainerWorkspaceLocator)
