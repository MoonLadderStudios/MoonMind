"""Admitted GitLab endpoint, project, and MR identity.

MoonLadderStudios/MoonMind#2616: reuse the existing connection/endpoint
contract (``normalize_endpoint`` from
``moonmind.workflows.executions.repository_contract``) and bind one
project-local merge-request IID to exactly one admitted GitLab instance.
Subgroup nesting and fork source/target identity are preserved. VCS ``git``
stays separate from code-host ``gitlab`` in the provider profile.

Endpoint identity is resolved through the canonical ``normalize_endpoint``
imported at call time (the same function-level reuse pattern as
``moonmind.publish.service``) so this module never reimplements routing
comparison. The endpoint must exactly match an admitted allowlist entry:
unapproved instances and arbitrary internal endpoints are rejected
fail-closed, and credentials embedded in an endpoint are never accepted.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Sequence
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.integrations.gitlab.errors import GitLabIdentityError

# ``group/subgroup/.../project`` segments: letters, digits, and the GitLab
# path characters ``-_.``. Numeric-only paths are project IDs, not names.
_PROJECT_PATH_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
_NUMERIC_ID_RE = re.compile(r"^[1-9][0-9]*$")


def _normalize_endpoint(value: str) -> str:
    from moonmind.workflows.executions.repository_contract import (
        normalize_endpoint,
    )

    return normalize_endpoint(value)


class GitLabProjectIdentity(BaseModel):
    """Stable identity of one GitLab project on one admitted instance."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    endpoint: str = Field(min_length=1)
    project_path: str | None = Field(None, alias="projectPath")
    project_id: int | None = Field(None, alias="projectId", gt=0)
    source_project: str | None = Field(None, alias="sourceProject")
    target_project: str | None = Field(None, alias="targetProject")

    @model_validator(mode="after")
    def _validate_identity(self) -> "GitLabProjectIdentity":
        has_path = bool((self.project_path or "").strip())
        has_id = self.project_id is not None
        if has_path == has_id:
            raise ValueError("exactly one of projectPath or projectId must be set")
        return self

    @property
    def api_project_path(self) -> str:
        """URL-encoded ``:id`` segment for ``/projects/:id`` routes."""

        if self.project_id is not None:
            return str(self.project_id)
        return quote(str(self.project_path).strip(), safe="")


class GitLabMRRef(BaseModel):
    """One project-local merge request IID bound to an admitted instance."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    project: GitLabProjectIdentity
    mr_iid: int = Field(alias="mrIid", gt=0)
    source_project: str | None = Field(None, alias="sourceProject")
    target_project: str | None = Field(None, alias="targetProject")

    @property
    def api_project_path(self) -> str:
        return self.project.api_project_path

    def as_provider_profile(self) -> dict[str, Any]:
        """Provider profile keeping VCS ``git`` separate from code-host ``gitlab``."""

        project = (
            self.project.project_path
            if self.project.project_path is not None
            else str(self.project.project_id)
        )
        return {
            "codeHost": "gitlab",
            "vcs": "git",
            "endpoint": self.project.endpoint,
            "project": project,
            "mrIid": self.mr_iid,
        }


def _parse_project(value: object) -> tuple[str | None, int | None]:
    raw = str(value or "").strip().strip("/")
    if not raw:
        raise GitLabIdentityError("GitLab project is required")
    if _NUMERIC_ID_RE.fullmatch(raw):
        return None, int(raw)
    if not _PROJECT_PATH_RE.fullmatch(raw):
        raise GitLabIdentityError("GitLab project must be a path or numeric id")
    return raw, None


def _parse_mr_iid(value: object) -> int:
    raw = str(value or "").strip()
    if not _NUMERIC_ID_RE.fullmatch(raw):
        raise GitLabIdentityError("GitLab MR IID must be a positive integer")
    return int(raw)


def _reject_internal_endpoint(normalized: str, *, action: str) -> None:
    """Reject arbitrary internal endpoints that are not plain public hosts."""

    try:
        host = (urlsplit(normalized).hostname or "").lower()
    except ValueError:
        raise GitLabIdentityError("GitLab endpoint is unparseable", action=action) from None
    if not host or host in {"localhost"} or host.endswith(".localhost"):
        raise GitLabIdentityError(
            "GitLab internal endpoint is not admitted", action=action
        )
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if parsed_ip.is_private or parsed_ip.is_loopback or parsed_ip.is_link_local:
        raise GitLabIdentityError(
            "GitLab internal endpoint is not admitted", action=action
        )


def resolve_gitlab_identity(
    *,
    endpoint: str,
    project: object,
    mr_iid: object,
    admitted_endpoints: Sequence[str],
    source_project: object | None = None,
    target_project: object | None = None,
    action: str | None = None,
) -> GitLabMRRef:
    """Bind one MR IID to one admitted GitLab instance (fail-closed).

    The endpoint is normalized with the canonical repository-contract
    comparison, then required to match an admitted allowlist entry exactly.
    Internal/loopback/link-local endpoints are never admitted.
    """

    raw_endpoint = str(endpoint or "").strip()
    if not raw_endpoint:
        raise GitLabIdentityError("GitLab endpoint is required", action=action)
    try:
        normalized = _normalize_endpoint(raw_endpoint)
    except ValueError as exc:
        raise GitLabIdentityError(
            "GitLab endpoint is not a usable instance", action=action
        ) from exc
    if urlsplit(normalized).scheme != "https":
        raise GitLabIdentityError(
            "GitLab endpoint must use https", action=action
        )
    admitted: set[str] = set()
    for candidate in admitted_endpoints:
        try:
            admitted.add(_normalize_endpoint(str(candidate or "")))
        except ValueError:
            continue
    if normalized not in admitted:
        raise GitLabIdentityError(
            "GitLab instance is not admitted for this connection", action=action
        )
    _reject_internal_endpoint(normalized, action=action)

    project_path, project_id = _parse_project(project)
    identity = GitLabProjectIdentity(
        endpoint=normalized,
        projectPath=project_path,
        projectId=project_id,
    )
    source = str(source_project or "").strip().strip("/") or None
    target = str(target_project or "").strip().strip("/") or None
    for label, fork_ref in (("source", source), ("target", target)):
        if fork_ref is not None and not _PROJECT_PATH_RE.fullmatch(fork_ref):
            raise GitLabIdentityError(
                f"GitLab {label} project must be a group/project path",
                action=action,
            )
    return GitLabMRRef(
        project=identity,
        mrIid=_parse_mr_iid(mr_iid),
        sourceProject=source,
        targetProject=target,
    )
