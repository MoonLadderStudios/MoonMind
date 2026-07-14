"""Owner-side container-job workspace resolution and mount planning.

MoonLadderStudios/MoonMind#3255.

The public container-job contract carries only a typed logical workspace
reference (:mod:`moonmind.schemas.workspace_locator_models`).  This module is
the *owner-side* authority that converts an authorized locator into a trusted,
daemon-visible mount plan for the selected system Docker backend.  It:

* correlates the referenced run/session/artifact against the authenticated
  owner and source recorded on the trusted workflow input, failing closed with
  a stable classification for cross-user / cross-session / absent references;
* maps each supported source kind to an approved bind root or named volume
  (including the ``agent_workspaces`` volume and the Omnigent worktree root);
* normalizes the relative subpath and rejects parent traversal, absolute
  host-source injection, symlink escape, and duplicate target collisions;
* produces deterministic job-owned ``/artifacts`` and ``/scratch`` targets so a
  workload can never choose an arbitrary output destination; and
* never returns a host/volume source to the caller — only the fixed
  in-container targets and an opaque resolution handle cross the boundary.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobFailureClass,
)
from moonmind.schemas.workspace_locator_models import (
    CONTAINER_JOB_WORKSPACE_ADAPTER,
    CONTAINER_WORKSPACE_NOT_FOUND,
    CONTAINER_WORKSPACE_NOT_VISIBLE,
    CONTAINER_WORKSPACE_PERMISSION_DENIED,
    ContainerArtifactWorkspaceLocator,
    ContainerManagedSessionWorkspaceLocator,
    ContainerOmnigentWorkspaceLocator,
    ContainerRunWorkspaceLocator,
)

# Fixed in-container mount targets. Neither the caller nor the agent learns or
# selects the daemon-visible source path behind any of these.
WORKSPACE_TARGET = "/workspace"
ARTIFACTS_TARGET = "/artifacts"
SCRATCH_TARGET = "/scratch"

# Deterministic, job-owned marker written into the artifacts area during the
# visibility probe. It is created and removed owner-side and is the only thing
# a writable probe is permitted to mutate.
_VISIBILITY_MARKER_PREFIX = ".mm-container-visibility-"


class ContainerWorkspaceError(RuntimeError):
    """Stable owner-side workspace resolution / visibility failure.

    ``code`` is one of the caller-visible classification strings and
    ``failure_class`` maps the code onto the durable container-job taxonomy so
    a terminal outcome can carry the right class without leaking host paths.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.failure_class = _FAILURE_CLASS_BY_CODE.get(
            code, ContainerJobFailureClass.WORKSPACE
        )
        super().__init__(f"{code}: {message}")


_FAILURE_CLASS_BY_CODE = {
    CONTAINER_WORKSPACE_NOT_FOUND: ContainerJobFailureClass.WORKSPACE,
    CONTAINER_WORKSPACE_PERMISSION_DENIED: ContainerJobFailureClass.AUTHORIZATION,
    CONTAINER_WORKSPACE_NOT_VISIBLE: ContainerJobFailureClass.WORKSPACE,
}

# Non-retryable classification codes surfaced to the activity/worker boundary.
CONTAINER_WORKSPACE_ERROR_CODES = (
    CONTAINER_WORKSPACE_NOT_FOUND,
    CONTAINER_WORKSPACE_PERMISSION_DENIED,
    CONTAINER_WORKSPACE_NOT_VISIBLE,
)


@dataclass(frozen=True, slots=True)
class WorkspaceMount:
    """One resolved bind mount for the selected Docker backend."""

    source: str
    target: str
    read_only: bool
    mount_class: str  # "workspace" | "artifacts" | "scratch" | "cache"

    def to_mount_arg(self) -> str:
        parts = [
            "type=bind",
            f"src={self.source}",
            f"dst={self.target}",
        ]
        if self.read_only:
            parts.append("readonly")
        return ",".join(parts)


@dataclass(frozen=True, slots=True)
class ContainerMountPlan:
    """Trusted, owner-only mount plan. Only fixed targets cross the boundary."""

    workspace_source: str
    artifacts_source: str
    scratch_source: str
    mounts: tuple[WorkspaceMount, ...]

    def mount_args(self) -> list[str]:
        args: list[str] = []
        for mount in self.mounts:
            args.extend(("--mount", mount.to_mount_arg()))
        return args

    @property
    def in_container_targets(self) -> tuple[str, ...]:
        return tuple(mount.target for mount in self.mounts)


@dataclass(frozen=True, slots=True)
class ApprovedWorkspaceMapping:
    """Deployment-owned mapping of source kinds to approved host roots.

    Sources are resolved under approved bind roots only; a caller can never
    inject an arbitrary host path.  ``agent_workspaces_volume`` records the
    canonical named volume the trusted agent-runtime worker mounts, and
    ``omnigent_worktree_root`` records the Omnigent worktree bind root.
    """

    run_root: Path
    managed_session_root: Path
    omnigent_worktree_root: Path
    artifact_workspace_root: Path
    job_scratch_root: Path
    agent_workspaces_volume: str = "agent_workspaces"

    @classmethod
    def from_workspace_root(
        cls,
        workspace_root: str | Path,
        *,
        omnigent_worktree_root: str | Path | None = None,
        agent_workspaces_volume: str = "agent_workspaces",
    ) -> "ApprovedWorkspaceMapping":
        """Derive the canonical local-first mapping from one workspace root.

        The default Docker Compose topology gives the trusted agent-runtime
        worker a single ``agent_workspaces`` volume mounted at ``workspace_root``
        (``/work/agent_jobs``); Omnigent hosts use ``./omnigent_workspaces``.
        """

        root = Path(workspace_root).resolve()
        omnigent = (
            Path(omnigent_worktree_root).resolve()
            if omnigent_worktree_root is not None
            else root / "omnigent_workspaces"
        )
        return cls(
            run_root=root,
            managed_session_root=root,
            omnigent_worktree_root=omnigent,
            artifact_workspace_root=root,
            job_scratch_root=root / ".container-job-scratch",
            agent_workspaces_volume=agent_workspaces_volume,
        )

    def approved_root_for_kind(self, kind: str) -> Path:
        roots = {
            "moonmind-run": self.run_root,
            "moonmind-session": self.managed_session_root,
            "omnigent-session": self.omnigent_worktree_root,
            "artifact-workspace": self.artifact_workspace_root,
        }
        try:
            return roots[kind]
        except KeyError as exc:  # pragma: no cover - guarded by locator adapter
            raise ContainerWorkspaceError(
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                f"unsupported container-job workspace kind: {kind!r}",
            ) from exc


def _sanitize_identity(identity: str) -> str:
    """Contain a logical identity to a single safe path component.

    A logical identifier never becomes an arbitrary path: traversal,
    separators, and absolute markers collapse to ``_`` so it can only ever
    name a direct child of an approved root.
    """

    safe = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in identity)
    safe = safe.strip("._") or "_"
    if safe in {".", ".."}:
        safe = "_"
    return safe


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContainerWorkspaceError(code, message)


class ContainerWorkspaceResolver:
    """Resolve authorized locators into trusted, daemon-visible mount plans."""

    def __init__(self, *, mapping: ApprovedWorkspaceMapping) -> None:
        self._mapping = mapping

    @property
    def mapping(self) -> ApprovedWorkspaceMapping:
        return self._mapping

    def opaque_handle(self, request: ContainerJobActivityRequest) -> str:
        """Return an opaque, non-reversible handle for the resolved workspace.

        The handle is derived from the ownership token only; it never encodes a
        host or volume path, so it is safe to carry in MCP/HTTP responses,
        activity results, Temporal history, and ordinary logs.
        """

        digest = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:32]
        return f"container-workspace://{digest}"

    def resolve(self, request: ContainerJobActivityRequest) -> ContainerMountPlan:
        """Produce the deterministic mount plan for a job's authorized locator.

        Resolution is deterministic and idempotent: it is re-run at every
        owner-side boundary (resolve, probe, create, publish) so no host path
        ever has to cross workflow history to be reused.
        """

        spec = request.request.spec
        locator = CONTAINER_JOB_WORKSPACE_ADAPTER.validate_python(spec.workspace_ref)
        self._authorize(locator, request)

        approved_root = self._mapping.approved_root_for_kind(locator.kind).resolve()
        identity, relative_path = _identity_and_relative(locator)
        workspace_source = self._contained_source(
            approved_root, identity, relative_path
        )
        _require(
            workspace_source.is_dir(),
            CONTAINER_WORKSPACE_NOT_FOUND,
            "authorized container-job workspace does not exist",
        )

        artifacts_source = self._job_owned_dir(request, "artifacts")
        scratch_source = self._job_owned_dir(request, "scratch")

        mounts = [
            WorkspaceMount(
                source=str(workspace_source),
                target=WORKSPACE_TARGET,
                read_only=False,
                mount_class="workspace",
            ),
            WorkspaceMount(
                source=str(artifacts_source),
                target=ARTIFACTS_TARGET,
                read_only=False,
                mount_class="artifacts",
            ),
            WorkspaceMount(
                source=str(scratch_source),
                target=SCRATCH_TARGET,
                read_only=False,
                mount_class="scratch",
            ),
        ]
        mounts.extend(self._cache_mounts(request))
        _reject_target_collisions(mounts)

        return ContainerMountPlan(
            workspace_source=str(workspace_source),
            artifacts_source=str(artifacts_source),
            scratch_source=str(scratch_source),
            mounts=tuple(mounts),
        )

    # -- authorization ----------------------------------------------------

    def _authorize(self, locator, request: ContainerJobActivityRequest) -> None:
        """Fail closed unless the locator correlates with the trusted owner.

        The API authenticates the principal and records the owner and source
        correlation on the workflow input.  Here we prove the referenced
        session/run identity matches that trusted correlation, so a caller
        cannot reference another user's or another session's workspace.
        """

        source = request.request.source
        if isinstance(locator, ContainerOmnigentWorkspaceLocator):
            _require(
                source.source == "omnigent"
                and bool(source.omnigent_session_id)
                and source.omnigent_session_id == locator.session_id,
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "omnigent workspace does not correlate with the authenticated session",
            )
            if locator.conversation_id and source.omnigent_conversation_id:
                _require(
                    locator.conversation_id == source.omnigent_conversation_id,
                    CONTAINER_WORKSPACE_PERMISSION_DENIED,
                    "omnigent workspace conversation does not correlate",
                )
        elif isinstance(locator, ContainerManagedSessionWorkspaceLocator):
            _require(
                bool(source.managed_session_id)
                and source.managed_session_id == locator.session_id,
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "managed-session workspace does not correlate with the caller",
            )
        elif isinstance(locator, ContainerRunWorkspaceLocator):
            correlated = {source.run_id, source.workflow_id, source.agent_run_id}
            _require(
                locator.run_id in correlated and locator.run_id is not None,
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "run workspace does not correlate with the caller's run identity",
            )
        elif isinstance(locator, ContainerArtifactWorkspaceLocator):
            # Artifact-materialization workspaces are owner-scoped only; the
            # owner principal on the trusted input is the authority.
            _require(
                bool(request.owner.principal_id),
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "artifact workspace requires an authenticated owner",
            )

    # -- containment ------------------------------------------------------

    def _contained_source(
        self, approved_root: Path, identity: str, relative_path: str
    ) -> Path:
        base = (approved_root / _sanitize_identity(identity)).resolve()
        _require(
            base == approved_root or approved_root in base.parents,
            CONTAINER_WORKSPACE_PERMISSION_DENIED,
            "workspace identity escapes its approved root",
        )
        candidate = (base / relative_path).resolve()
        _require(
            candidate == base or base in candidate.parents,
            CONTAINER_WORKSPACE_PERMISSION_DENIED,
            "workspace relative path escapes its approved root",
        )
        # Symlink escape: the real (symlink-followed) path must remain under the
        # approved root even when intermediate components are symlinks.
        real = Path(os.path.realpath(candidate))
        real_root = Path(os.path.realpath(approved_root))
        _require(
            real == real_root or real_root in real.parents,
            CONTAINER_WORKSPACE_PERMISSION_DENIED,
            "workspace path resolves outside its approved root via symlink",
        )
        return candidate

    def _job_owned_dir(
        self, request: ContainerJobActivityRequest, leaf: str
    ) -> Path:
        digest = hashlib.sha256(request.ownership_token.encode()).hexdigest()[:20]
        job_dir = (self._mapping.job_scratch_root / digest / leaf).resolve()
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def _cache_mounts(
        self, request: ContainerJobActivityRequest
    ) -> list[WorkspaceMount]:
        mounts: list[WorkspaceMount] = []
        for index, cache in enumerate(request.request.spec.caches):
            safe = _sanitize_identity(cache.cache_ref)
            source = self._job_owned_dir(request, f"cache/{safe}-{index}")
            mounts.append(
                WorkspaceMount(
                    source=str(source),
                    target=cache.target,
                    read_only=cache.read_only,
                    mount_class="cache",
                )
            )
        return mounts

    # -- visibility probe -------------------------------------------------

    def visibility_marker_name(self, request: ContainerJobActivityRequest) -> str:
        digest = hashlib.sha256(
            f"{request.ownership_token}:visibility".encode()
        ).hexdigest()[:20]
        return f"{_VISIBILITY_MARKER_PREFIX}{digest}"


def _identity_and_relative(locator) -> tuple[str, str]:
    if isinstance(locator, ContainerArtifactWorkspaceLocator):
        return locator.artifact_ref, locator.relative_path
    if isinstance(locator, ContainerRunWorkspaceLocator):
        return locator.run_id, locator.relative_path
    # managed-session and omnigent-session both key on session_id.
    return locator.session_id, locator.relative_path


def _reject_target_collisions(mounts: Iterable[WorkspaceMount]) -> None:
    seen: set[str] = set()
    for mount in mounts:
        _require(
            mount.target not in seen,
            CONTAINER_WORKSPACE_PERMISSION_DENIED,
            f"duplicate mount target collision: {mount.target}",
        )
        seen.add(mount.target)
