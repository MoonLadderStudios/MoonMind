"""Typed, workflow-safe identities for workspace ownership boundaries."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


def _relative_subpath(value: str) -> str:
    candidate = str(value).strip().replace("\\", "/")
    decoded = candidate
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if decoded != candidate:
        raise ValueError("relativePath must not contain percent-encoding")
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
