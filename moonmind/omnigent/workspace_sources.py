"""Unified workspace-source compiler for artifact, checkpoint, and existing workspaces.

Implements CONTRACT-002 / CONTRACT-011 / INV-005 / INV-006 target behavior for
MoonLadderStudios/MoonMind#4014 (plan slice 3):

- Exactly one source plus an explicit overlay/input policy is admitted.
  A repository base with a checkpoint/artifact overlay is one source (base
  plus overlay policy), not two competing sources.
- ``artifact`` names authorized immutable content and its expected digest.
- ``checkpoint`` names a supported workspace-restore contract.
- ``existing_workspace`` names a verified server-issued locator/use grant.
- Raw ``workspacePath``/``path`` precedence is removed as a normal authoring
  route; :func:`decode_legacy_workspace_path` preserves the one explicit
  historical decoding path for already-recorded payloads.
- Conflicting source aliases are rejected before any read or filesystem
  mutation.

This module owns compilation only. Admission against the real artifact
service, byte verification, staging, and promotion live in
:mod:`moonmind.omnigent.workspace_artifacts` and the host materializer, so
consumers cannot drift from the compiled decision.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

WorkspaceSourceKind = Literal[
    "scratch", "repository", "artifact", "checkpoint", "existing_workspace"
]

SUPPORTED_RESTORE_CONTRACTS: tuple[str, ...] = (
    "moonmind.worktree-archive.v1",
    "moonmind.workspace-snapshot.v1",
)

SUPPORTED_SOURCE_RUNTIMES: dict[str, tuple[str, ...]] = {
    "scratch": ("omnigent", "managed", "codex_cli"),
    "repository": ("omnigent", "managed", "codex_cli"),
    "artifact": ("omnigent", "managed"),
    "checkpoint": ("omnigent", "managed", "codex_cli"),
    "existing_workspace": ("omnigent", "managed"),
}

OVERLAY_AUTHORITATIVE = "authoritative_restore"
OVERLAY_ADDITIVE = "additive_overlay"

# Resource budgets shared by the compiler (authored claims) and the
# materializer (enforced streamed/expanded limits). Kept in one place so a
# test can assert the advertised bound equals the enforced bound.
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_FILE_COUNT = 50_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_PATH_DEPTH = 32
MAX_PROCESSING_SECONDS = 300.0

WORKSPACE_SOURCE_CONFLICT = "WORKSPACE_SOURCE_CONFLICT"
WORKSPACE_SOURCE_INVALID = "WORKSPACE_SOURCE_INVALID"
WORKSPACE_SOURCE_RAW_PATH_REJECTED = "WORKSPACE_SOURCE_RAW_PATH_REJECTED"
WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED = "WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED"
WORKSPACE_SOURCE_GRANT_INVALID = "WORKSPACE_SOURCE_GRANT_INVALID"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GRANT_DIGEST_RE = re.compile(r"^(?:sha256:[0-9a-f]{64}|hmac-sha256:[0-9a-f]{64})$")
_ARTIFACT_REF_RE = re.compile(r"^artifact://[A-Za-z0-9_.\-]{1,200}$")

# Grant HMAC secret lives in the server environment only; agents never see it.
# A ``hmac-sha256:`` grant digest authenticates issuance; plain ``sha256:``
# digests remain format-checked only, for historical grants.
_GRANT_HMAC_ENV = "MOONMIND_WORKSPACE_GRANT_SECRET"

# Backend/source support matrix. An advanced selected directory is not
# permission for arbitrary host mounts, and not every sharing mode is
# meaningful on every backend. Unsupported combinations fail before launch.
_SUPPORTED_BACKENDS = ("docker_local", "docker_remote", "managed")
_BACKEND_BY_SOURCE: dict[str, frozenset[str]] = {
    "scratch": frozenset({"docker_local", "docker_remote", "managed"}),
    "repository": frozenset({"docker_local", "docker_remote", "managed"}),
    "artifact": frozenset({"docker_local", "docker_remote", "managed"}),
    "checkpoint": frozenset({"docker_local", "docker_remote", "managed"}),
    "existing_workspace": frozenset(
        {"docker_local", "docker_remote", "managed"}
    ),
}


class WorkspaceSourceError(ValueError):
    """Fail-closed source-compilation error raised before any host mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ExistingWorkspaceGrant:
    """Server-issued ownership/use grant for an existing workspace.

    A raw directory path is never permission to use a sibling workflow's
    directory. Only this grant — issued by the server, bound to the owning
    workflow/step, a generation, and an access mode — authorizes reuse.
    """

    workspace_id: str
    owner_workflow_id: str
    owner_step_execution_id: str
    generation: int
    mode: Literal["exclusive", "read_only"]
    expires_at: datetime | None = None
    grant_digest: str | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        moment = now or datetime.now(tz=UTC)
        candidate = self.expires_at
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=UTC)
        return candidate <= moment


@dataclass(frozen=True)
class CompiledWorkspaceSource:
    """The single admitted source plus its explicit overlay/input policy."""

    kind: WorkspaceSourceKind
    overlay_policy: str = OVERLAY_AUTHORITATIVE
    # Artifact source: authorized immutable content + expected digest.
    artifact_ref: str | None = None
    artifact_digest: str | None = None
    # Checkpoint source: supported workspace-restore contract + version.
    checkpoint_ref: str | None = None
    restore_contract: str | None = None
    restore_contract_version: str = "v1"
    # Existing-workspace source: verified locator/use grant.
    existing_grant: ExistingWorkspaceGrant | None = None
    # Repository identity. For a repository source this is the clone target
    # (owned by repository_contract); for a checkpoint/artifact source it is
    # the optional clone-to base. A self-contained authored content source
    # omits it entirely: no clone and no source-credential lookup.
    repository_ref: str | None = None
    repository_branch: str | None = None
    # Explicit input policy: additive refs applied after an authoritative
    # restore, and the digest binding the admitted input set.
    additive_refs: tuple[str, ...] = ()
    input_manifest_digest: str = "sha256:" + "0" * 64
    historical: bool = False

    def readiness_fingerprint(
        self,
        *,
        target_owner_workflow_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Bind completion to source/digest/contract/inputs/owner/attempt."""

        if self.existing_grant is not None:
            source_digest = f"existing:{self.existing_grant.workspace_id}"
        elif self.kind == "repository":
            source_digest = (
                f"repository:{self.repository_ref or ''}"
                f"@{self.repository_branch or ''}"
            )
        else:
            source_digest = (
                self.artifact_digest
                or self.checkpoint_ref
                or "scratch"
            )
        return {
            "kind": self.kind,
            "sourceDigest": source_digest,
            "repositoryBase": (
                f"{self.repository_ref or ''}@{self.repository_branch or ''}"
                if self.kind in {"artifact", "checkpoint"} and self.repository_ref
                else None
            ),
            "restoreContract": self.restore_contract,
            "restoreContractVersion": self.restore_contract_version,
            "inputManifestDigest": self.input_manifest_digest,
            "targetOwnerWorkflowId": target_owner_workflow_id,
            "attemptId": attempt_id,
            "overlayPolicy": self.overlay_policy,
            "existingGeneration": (
                self.existing_grant.generation if self.existing_grant else None
            ),
        }


def compute_input_manifest_digest(refs: tuple[str, ...] | list[str]) -> str:
    cleaned = sorted({str(ref).strip() for ref in refs if str(ref).strip()})
    encoded = json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def decode_legacy_workspace_path(spec: Mapping[str, Any]) -> str | None:
    """Explicit historical decoding for already-recorded raw-path payloads.

    New authoring must use ``workspaceSource``; the materializer rejects raw
    paths through the normal route and only this named decoder may interpret
    them for migration/diagnostics of in-flight histories.
    """

    for key in ("workspacePath", "path"):
        value = _clean_str(spec.get(key))
        if value:
            return value
    return None


def parse_existing_workspace_grant(value: Any) -> ExistingWorkspaceGrant:
    if not isinstance(value, Mapping):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existing_workspace source requires an existingWorkspaceGrant mapping",
        )
    workspace_id = _clean_str(value.get("workspaceId") or value.get("workspace_id"))
    owner_workflow = _clean_str(
        value.get("ownerWorkflowId") or value.get("owner_workflow_id")
    )
    owner_step = _clean_str(
        value.get("ownerStepExecutionId") or value.get("owner_step_execution_id")
    )
    mode = _clean_str(value.get("mode") or "exclusive").lower()
    try:
        generation = int(value.get("generation", 1))
    except (TypeError, ValueError) as exc:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant.generation must be an integer",
        ) from exc
    expires_raw = _clean_str(value.get("expiresAt") or value.get("expires_at"))
    expires_at: datetime | None = None
    if expires_raw:
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_GRANT_INVALID,
                "existingWorkspaceGrant.expiresAt is not a valid timestamp",
            ) from exc
    grant_digest = _clean_str(value.get("grantDigest") or value.get("grant_digest"))
    if not workspace_id or not owner_workflow or not owner_step:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant requires workspaceId, ownerWorkflowId, "
            "and ownerStepExecutionId",
        )
    if mode not in {"exclusive", "read_only"}:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant.mode must be 'exclusive' or 'read_only'",
        )
    if generation < 1:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant.generation must be >= 1",
        )
    if grant_digest and not _GRANT_DIGEST_RE.match(grant_digest):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant.grantDigest must be a sha256: digest",
        )
    return ExistingWorkspaceGrant(
        workspace_id=workspace_id,
        owner_workflow_id=owner_workflow,
        owner_step_execution_id=owner_step,
        generation=generation,
        mode=mode,  # type: ignore[arg-type]
        expires_at=expires_at,
        grant_digest=grant_digest or None,
    )


def check_source_runtime_supported(kind: str, runtime: str) -> None:
    supported = SUPPORTED_SOURCE_RUNTIMES.get(kind, ())
    if runtime not in supported:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED,
            f"workspace source {kind!r} is not supported on runtime {runtime!r}; "
            f"supported: {', '.join(supported) or 'none'}",
        )


def check_source_backend_supported(
    kind: str,
    backend: str,
    *,
    grant_mode: str | None = None,
) -> None:
    """Fail before execution when a source/backend combination is unsupported.

    An exclusive writable grant cannot be honored on a remote daemon view
    that cannot fence the owner's live checkout; read-only sharing is
    supported there through the qualified locator mapping.
    """

    normalized = str(backend or "").strip().lower() or "docker_local"
    if normalized not in _SUPPORTED_BACKENDS:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED,
            f"workspace backend {backend!r} is not a supported runtime",
        )
    allowed = _BACKEND_BY_SOURCE.get(str(kind))
    if allowed is None or normalized not in allowed:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED,
            f"workspace source {kind!r} is unsupported on backend {normalized!r}",
        )
    if (
        kind == "existing_workspace"
        and normalized == "docker_remote"
        and (grant_mode or "exclusive") == "exclusive"
    ):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED,
            "exclusive existing-workspace use is unsupported on a remote daemon; "
            "request read_only sharing or a local backend",
        )


def resolve_workspace_backend() -> str:
    """Map the deployment daemon selection onto the backend support matrix."""

    mode = os.getenv("WORKFLOW_DOCKER_DAEMON_MODE", "").strip().lower()
    if mode == "remote":
        return "docker_remote"
    if mode == "local":
        return "docker_local"
    if not mode:
        daemon_root = os.getenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", "").strip()
        return "docker_remote" if daemon_root else "docker_local"
    # An unknown explicit mode is a configuration error, not a local daemon:
    # silently treating a typo (e.g. ``sidecar``) as local would pass the
    # pre-launch backend check and allow workspace mutation before the
    # daemon-path resolver rejects the configuration.
    raise WorkspaceSourceError(
        WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED,
        "WORKFLOW_DOCKER_DAEMON_MODE must be 'local' or 'remote'",
    )


def _grant_signature_payload(
    *,
    workspace_id: str,
    owner_workflow_id: str,
    owner_step_execution_id: str,
    grantee_workflow_id: str,
    mode: str,
    generation: int,
    expires_at: int,
) -> str:
    return json.dumps(
        {
            "version": "grant-v1",
            "workspaceId": workspace_id,
            "ownerWorkflowId": owner_workflow_id,
            "ownerStepExecutionId": owner_step_execution_id,
            "granteeWorkflowId": grantee_workflow_id,
            "mode": mode,
            "generation": generation,
            "expiresAt": expires_at,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def issue_existing_workspace_grant(
    *,
    workspace_id: str,
    owner_workflow_id: str,
    owner_step_execution_id: str,
    grantee_workflow_id: str,
    mode: str = "exclusive",
    generation: int = 1,
    lifetime_seconds: int = 3600,
    secret: str | None = None,
) -> ExistingWorkspaceGrant:
    """Issue a server-side HMAC-authenticated ownership/use grant.

    The HMAC in ``grant_digest`` authenticates issuance so a forged grant
    cannot pass verification; agents never see the server secret. The
    grantee is required explicitly because the HMAC binds it: a defaulted
    (empty) grantee would issue a grant that fails verification for every
    executable request, whose materializer always verifies against the
    nonempty target workflow.
    """

    key = secret if secret is not None else os.getenv(_GRANT_HMAC_ENV, "")
    if not key:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "workspace grant issuance requires a server grant secret",
        )
    normalized_mode = str(mode or "exclusive").strip().lower()
    if normalized_mode not in {"exclusive", "read_only"}:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant.mode must be 'exclusive' or 'read_only'",
        )
    if not str(workspace_id or "").strip() or not str(
        owner_workflow_id or ""
    ).strip():
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "workspace grant requires a workspace and owner workflow identity",
        )
    if generation < 1:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant.generation must be >= 1",
        )
    if lifetime_seconds <= 0 or lifetime_seconds > 86400:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "workspace grant lifetime must be within one day",
        )
    expires_epoch = int(time.time()) + int(lifetime_seconds)
    payload = _grant_signature_payload(
        workspace_id=str(workspace_id).strip(),
        owner_workflow_id=str(owner_workflow_id).strip(),
        owner_step_execution_id=str(owner_step_execution_id).strip(),
        grantee_workflow_id=str(grantee_workflow_id or "").strip(),
        mode=normalized_mode,
        generation=generation,
        expires_at=expires_epoch,
    )
    digest = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return ExistingWorkspaceGrant(
        workspace_id=str(workspace_id).strip(),
        owner_workflow_id=str(owner_workflow_id).strip(),
        owner_step_execution_id=str(owner_step_execution_id).strip(),
        generation=generation,
        mode=normalized_mode,  # type: ignore[arg-type]
        expires_at=datetime.fromtimestamp(expires_epoch, tz=UTC),
        grant_digest=f"hmac-sha256:{digest}",
    )


def verify_existing_workspace_grant_signature(
    grant: ExistingWorkspaceGrant,
    *,
    grantee_workflow_id: str = "",
    secret: str | None = None,
) -> None:
    """Verify the HMAC issuance signature of a grant, when one is carried.

    Grants without an ``hmac-sha256:`` digest are historical grants and keep
    the owner/generation/expiry checks of :func:`verify_existing_workspace_grant`;
    forged or mismatched signatures fail closed here.
    """

    digest = str(grant.grant_digest or "")
    if not digest.startswith("hmac-sha256:"):
        return
    key = secret if secret is not None else os.getenv(_GRANT_HMAC_ENV, "")
    if not key:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "workspace grant verification requires a server grant secret",
        )
    if grant.expires_at is None:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "signed workspace grant requires a bounded lifetime",
        )
    expires_epoch = int(grant.expires_at.timestamp())
    payload = _grant_signature_payload(
        workspace_id=grant.workspace_id,
        owner_workflow_id=grant.owner_workflow_id,
        owner_step_execution_id=grant.owner_step_execution_id,
        grantee_workflow_id=str(grantee_workflow_id or "").strip(),
        mode=grant.mode,
        generation=grant.generation,
        expires_at=expires_epoch,
    )
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(digest, f"hmac-sha256:{expected}"):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant signature is invalid",
        )


def _validate_artifact_ref(ref: str, *, noun: str) -> str:
    value = _clean_str(ref)
    if not _ARTIFACT_REF_RE.match(value):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_INVALID,
            f"{noun} must be an artifact:// ref, got {value!r}",
        )
    return value


def _validate_digest(digest: Any, *, noun: str, required: bool) -> str | None:
    value = _clean_str(digest)
    if not value:
        if required:
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_INVALID,
                f"{noun} requires an expected sha256: digest",
            )
        return None
    if not _DIGEST_RE.match(value):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_INVALID,
            f"{noun} digest must match sha256:<64 hex>, got {value!r}",
        )
    return value


def compile_workspace_source(
    spec: Mapping[str, Any] | None,
    *,
    workflow_id: str = "",
    step_execution_id: str = "",
    runtime: str = "omnigent",
) -> CompiledWorkspaceSource:
    """Compile exactly one source plus an explicit overlay/input policy.

    Reads the canonical ``workspaceSource`` union first; when absent, derives
    one historical source from legacy aliases (repository/branch,
    workspaceCheckpointRestoreRef, restoreInputRefs, workspaceLocator) so
    in-flight payloads keep deterministic meaning. Raw ``workspacePath`` /
    ``path`` aliases are never a normal source: they raise
    ``WORKSPACE_SOURCE_RAW_PATH_REJECTED`` and must go through
    :func:`decode_legacy_workspace_path` explicitly.

    Raises before any read or filesystem mutation on conflict/invalid input.
    """

    spec_map = _as_mapping(spec)

    raw_path = _clean_str(spec_map.get("workspacePath") or spec_map.get("path"))
    if raw_path:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_RAW_PATH_REJECTED,
            "authored workspacePath/path is not an authorized workspace source; "
            "use workspaceSource with an existing_workspace grant",
        )

    overlay_raw = _clean_str(
        spec_map.get("overlayPolicy") or spec_map.get("overlay_policy")
    ).lower()
    overlay_policy = (
        OVERLAY_ADDITIVE
        if overlay_raw in {"additive", "additive_overlay", "overlay"}
        else OVERLAY_AUTHORITATIVE
    )

    additive_refs = tuple(
        dict.fromkeys(
            _clean_str(ref)
            for ref in (
                list(spec_map.get("restoreInputRefs") or [])
                + list(spec_map.get("attachmentRefs") or [])
            )
            if _clean_str(ref)
        )
    )

    authored = _as_mapping(spec_map.get("workspaceSource"))

    authored_kind = _clean_str(authored.get("kind")).lower() or None
    if authored_kind not in (None, "", "scratch", "repository", "artifact",
                             "checkpoint", "existing_workspace", "existing-workspace"):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_INVALID,
            f"workspaceSource.kind {authored_kind!r} is not a supported source",
        )
    if authored_kind == "existing-workspace":
        authored_kind = "existing_workspace"

    # Legacy alias presence (historical decoding only).
    repository_target = _as_mapping(spec_map.get("repositoryTarget"))
    repo_alias = _clean_str(
        spec_map.get("repository") or spec_map.get("repo") or ""
    )
    if isinstance(spec_map.get("repository"), Mapping):
        repo_alias = _clean_str(spec_map["repository"].get("name") or "") or repo_alias
    if isinstance(repository_target.get("repository"), Mapping):
        repo_alias = (
            _clean_str(repository_target["repository"].get("name")) or repo_alias
        )
    branch_alias = _clean_str(
        spec_map.get("startingBranch")
        or spec_map.get("branch")
        or spec_map.get("headBranch")
        or ""
    )
    if isinstance(repository_target.get("branch"), Mapping):
        branch_alias = (
            _clean_str(repository_target["branch"].get("name")) or branch_alias
        )
    checkpoint_alias = _clean_str(
        spec_map.get("workspaceCheckpointRestoreRef") or ""
    )
    artifact_alias = _clean_str(
        authored.get("artifactRef")
        or authored.get("artifact_ref")
        or spec_map.get("workspaceArtifactRef")
        or ""
    )
    checkpoint_authored = _clean_str(
        authored.get("checkpointRef") or authored.get("checkpoint_ref") or ""
    )
    grant_authored = authored.get("existingWorkspaceGrant") or authored.get(
        "existing_workspace_grant"
    )

    wants_artifact = bool(artifact_alias)
    wants_checkpoint = bool(checkpoint_authored or checkpoint_alias)
    wants_grant = grant_authored is not None
    wants_repo = bool(repo_alias or branch_alias)

    def _conflict(*names: str) -> WorkspaceSourceError:
        return WorkspaceSourceError(
            WORKSPACE_SOURCE_CONFLICT,
            "exactly one workspace source is required; conflicting aliases for "
            + ", ".join(sorted(names)),
        )

    if authored_kind is not None:
        # Authored workspaceSource is explicit: any alias outside the declared
        # kind's base/overlay policy is a conflict, not a silent second source.
        if authored_kind == "scratch":
            if wants_artifact or wants_checkpoint or wants_grant or wants_repo:
                raise _conflict(
                    *[
                        name
                        for name, want in (
                            ("artifact", wants_artifact),
                            ("checkpoint", wants_checkpoint),
                            ("existing_workspace", wants_grant),
                            ("repository", wants_repo),
                        )
                        if want
                    ]
                )
        elif authored_kind == "repository":
            if wants_artifact or wants_checkpoint or wants_grant:
                raise _conflict(
                    *[
                        name
                        for name, want in (
                            ("artifact", wants_artifact),
                            ("checkpoint", wants_checkpoint),
                            ("existing_workspace", wants_grant),
                        )
                        if want
                    ]
                )
        elif authored_kind == "artifact":
            if wants_checkpoint or wants_grant:
                raise _conflict(
                    *[
                        name
                        for name, want in (
                            ("checkpoint", wants_checkpoint),
                            ("existing_workspace", wants_grant),
                        )
                        if want
                    ]
                )
        elif authored_kind == "checkpoint":
            if wants_artifact or wants_grant:
                raise _conflict(
                    *[
                        name
                        for name, want in (
                            ("artifact", wants_artifact),
                            ("existing_workspace", wants_grant),
                        )
                        if want
                    ]
                )
        elif authored_kind == "existing_workspace":
            if wants_artifact or wants_checkpoint or wants_repo:
                raise _conflict(
                    *[
                        name
                        for name, want in (
                            ("artifact", wants_artifact),
                            ("checkpoint", wants_checkpoint),
                            ("repository", wants_repo),
                        )
                        if want
                    ]
                )
    else:
        # Historical decoding: a repository base paired with a checkpoint or
        # artifact overlay is one source (base plus overlay), matching the
        # recorded clone-then-project production flows.
        if wants_artifact and wants_checkpoint:
            raise _conflict("artifact", "checkpoint")
        if wants_grant and (wants_artifact or wants_checkpoint or wants_repo):
            raise _conflict(
                *[
                    name
                    for name, want in (
                        ("artifact", wants_artifact),
                        ("checkpoint", wants_checkpoint),
                        ("existing_workspace", wants_grant),
                        ("repository", wants_repo),
                    )
                    if want
                ]
            )

    manifest_digest = compute_input_manifest_digest(additive_refs)

    def _authored_repo_base() -> tuple[str | None, str | None]:
        base = authored.get("baseRepository") or authored.get("base_repository")
        if isinstance(base, Mapping):
            name = _clean_str(base.get("name")) or None
            branch = _clean_str(base.get("branch")) or None
        else:
            name = _clean_str(base) or None
            branch = None
        if branch is None:
            branch = _clean_str(authored.get("baseBranch")) or None
        return name, branch

    if authored_kind == "artifact" or (not authored_kind and wants_artifact):
        ref = _validate_artifact_ref(
            authored.get("artifactRef")
            or authored.get("artifact_ref")
            or artifact_alias,
            noun="artifact source",
        )
        digest = _validate_digest(
            authored.get("artifactDigest")
            or authored.get("artifact_digest")
            or authored.get("expectedDigest")
            or spec_map.get("workspaceArtifactDigest"),
            noun="artifact source",
            required=bool(authored_kind),
        )
        check_source_runtime_supported("artifact", runtime)
        base_repo, base_branch = _authored_repo_base()
        if authored_kind:
            content_repo = base_repo
            content_branch = base_branch
        else:
            content_repo = repo_alias or None
            content_branch = branch_alias or None
        return CompiledWorkspaceSource(
            kind="artifact",
            overlay_policy=(
                overlay_policy
                if authored_kind and overlay_raw
                else (OVERLAY_ADDITIVE if not authored_kind else OVERLAY_AUTHORITATIVE)
            ),
            artifact_ref=ref,
            artifact_digest=digest,
            repository_ref=content_repo,
            repository_branch=content_branch,
            additive_refs=additive_refs,
            input_manifest_digest=manifest_digest,
            historical=not bool(authored_kind),
        )

    if authored_kind == "checkpoint" or (not authored_kind and wants_checkpoint):
        ref = _validate_artifact_ref(
            checkpoint_authored or checkpoint_alias,
            noun="checkpoint source",
        )
        contract = _clean_str(
            authored.get("restoreContract")
            or authored.get("restore_contract")
            or "moonmind.worktree-archive.v1"
        ).lower()
        if contract not in SUPPORTED_RESTORE_CONTRACTS:
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_INVALID,
                f"checkpoint restore contract {contract!r} is not supported",
            )
        version = _clean_str(
            authored.get("restoreContractVersion")
            or authored.get("restore_contract_version")
            or "v1"
        )
        digest = _validate_digest(
            authored.get("checkpointDigest") or authored.get("checkpoint_digest"),
            noun="checkpoint source",
            required=False,
        )
        check_source_runtime_supported("checkpoint", runtime)
        base_repo, base_branch = _authored_repo_base()
        if authored_kind:
            content_repo = base_repo
            content_branch = base_branch
        else:
            content_repo = repo_alias or None
            content_branch = branch_alias or None
        return CompiledWorkspaceSource(
            kind="checkpoint",
            overlay_policy=(
                overlay_policy
                if authored_kind and overlay_raw
                else (OVERLAY_ADDITIVE if not authored_kind else OVERLAY_AUTHORITATIVE)
            ),
            artifact_digest=digest,
            checkpoint_ref=ref,
            restore_contract=contract,
            restore_contract_version=version,
            repository_ref=content_repo,
            repository_branch=content_branch,
            additive_refs=additive_refs,
            input_manifest_digest=manifest_digest,
            historical=not bool(authored_kind),
        )

    if authored_kind == "existing_workspace" or (not authored_kind and wants_grant):
        grant = parse_existing_workspace_grant(grant_authored)
        if grant.is_expired():
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_GRANT_INVALID,
                "existingWorkspaceGrant has expired",
            )
        if authored_kind == "existing_workspace":
            # Newly authored grants must carry a server-issued HMAC: the
            # materializer treats every non-HMAC digest as a historical
            # grant, so accepting an unsigned (or digest-downgraded) grant
            # here would keep newly authored sources forgeable. In-flight
            # historical payloads without an explicit kind keep the
            # tolerant path; a carried signature is still verified below.
            digest = str(grant.grant_digest or "")
            if not digest.startswith("hmac-sha256:"):
                raise WorkspaceSourceError(
                    WORKSPACE_SOURCE_GRANT_INVALID,
                    "authored existingWorkspaceGrant requires a server-issued "
                    "HMAC grantDigest",
                )
            verify_existing_workspace_grant_signature(
                grant, grantee_workflow_id=workflow_id
            )
        check_source_runtime_supported("existing_workspace", runtime)
        return CompiledWorkspaceSource(
            kind="existing_workspace",
            overlay_policy=OVERLAY_AUTHORITATIVE,
            existing_grant=grant,
            additive_refs=additive_refs,
            input_manifest_digest=manifest_digest,
        )

    if authored_kind == "repository" or (
        not authored_kind and wants_repo and not wants_artifact and not wants_checkpoint
    ):
        target_repo = _clean_str(
            authored.get("repository")
            or (authored.get("repositoryTarget", {}) or {}).get("repository", {})
            if isinstance(authored.get("repositoryTarget"), Mapping)
            else repo_alias
        ) or repo_alias
        target_branch = _clean_str(
            authored.get("branch") or branch_alias
        ) or branch_alias
        check_source_runtime_supported("repository", runtime)
        return CompiledWorkspaceSource(
            kind="repository",
            overlay_policy=OVERLAY_AUTHORITATIVE,
            repository_ref=target_repo or None,
            repository_branch=target_branch or None,
            additive_refs=additive_refs,
            input_manifest_digest=manifest_digest,
            historical=not bool(authored_kind),
        )

    check_source_runtime_supported("scratch", runtime)
    return CompiledWorkspaceSource(
        kind="scratch",
        overlay_policy=(
            OVERLAY_ADDITIVE if overlay_policy == OVERLAY_ADDITIVE
            else OVERLAY_AUTHORITATIVE
        ),
        additive_refs=additive_refs,
        input_manifest_digest=manifest_digest,
        historical=False,
    )


def verify_existing_workspace_grant(
    grant: ExistingWorkspaceGrant,
    *,
    target_workflow_id: str,
    target_step_execution_id: str,
    expected_generation: int | None = None,
    record_owner_workflow_id: str | None = None,
) -> None:
    """Verify a locator/use grant before any filesystem use.

    The grant's recorded owner must match the durable owner record for the
    workspace; root containment alone never authorizes reuse. Stale
    generations and expired grants fail closed.
    """

    _ = target_step_execution_id
    if grant.is_expired():
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant has expired",
        )
    if expected_generation is not None and grant.generation != expected_generation:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant generation is stale for the target workspace",
        )
    if (
        record_owner_workflow_id is not None
        and grant.owner_workflow_id != record_owner_workflow_id
    ):
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existingWorkspaceGrant owner does not match the workspace owner record",
        )
    if not target_workflow_id:
        raise WorkspaceSourceError(
            WORKSPACE_SOURCE_GRANT_INVALID,
            "existing workspace use requires a target workflow owner",
        )


class ExistingWorkspaceGrantLedger:
    """Monotone generation ledger for existing-workspace grants.

    Lives beside the sandbox owner records (never inside a materialized
    workspace). Admitting a generation lower than the recorded one fails as a
    stale grant; higher generations advance the ledger. Only ledger files
    named ``<workspace_id>.grant.json`` are ever written or removed.
    """

    def __init__(self, authority_dir: str | Path) -> None:
        self._dir = Path(authority_dir)

    def _path(self, workspace_id: str) -> Path:
        candidate = (self._dir / f"{workspace_id}.grant.json").resolve()
        if candidate.parent != self._dir.resolve():
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_GRANT_INVALID,
                "existing workspace grant ledger escapes its authority",
            )
        return candidate

    def admitted_generation(self, workspace_id: str) -> int | None:
        path = self._path(workspace_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            generation = int(payload.get("generation", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_GRANT_INVALID,
                "existing workspace grant ledger is invalid",
            )
        return generation if generation >= 1 else None

    def admit(self, grant: ExistingWorkspaceGrant) -> None:
        if grant.is_expired():
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_GRANT_INVALID,
                "existingWorkspaceGrant has expired",
            )
        recorded = self.admitted_generation(grant.workspace_id)
        if recorded is not None and grant.generation < recorded:
            raise WorkspaceSourceError(
                WORKSPACE_SOURCE_GRANT_INVALID,
                "existingWorkspaceGrant generation is stale for the target "
                "workspace",
            )
        if recorded is not None and grant.generation == recorded:
            return
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(grant.workspace_id)
        payload = json.dumps(
            {"version": "grant-v1", "generation": grant.generation}, sort_keys=True
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)


__all__ = [
    "CompiledWorkspaceSource",
    "ExistingWorkspaceGrant",
    "ExistingWorkspaceGrantLedger",
    "MAX_COMPRESSED_BYTES",
    "MAX_EXPANDED_BYTES",
    "MAX_FILE_BYTES",
    "MAX_FILE_COUNT",
    "MAX_PATH_DEPTH",
    "MAX_PROCESSING_SECONDS",
    "OVERLAY_ADDITIVE",
    "OVERLAY_AUTHORITATIVE",
    "SUPPORTED_RESTORE_CONTRACTS",
    "SUPPORTED_SOURCE_RUNTIMES",
    "WORKSPACE_SOURCE_CONFLICT",
    "WORKSPACE_SOURCE_GRANT_INVALID",
    "WORKSPACE_SOURCE_INVALID",
    "WORKSPACE_SOURCE_RAW_PATH_REJECTED",
    "WORKSPACE_SOURCE_RUNTIME_UNSUPPORTED",
    "WorkspaceSourceError",
    "check_source_backend_supported",
    "check_source_runtime_supported",
    "compile_workspace_source",
    "compute_input_manifest_digest",
    "decode_legacy_workspace_path",
    "issue_existing_workspace_grant",
    "parse_existing_workspace_grant",
    "resolve_workspace_backend",
    "verify_existing_workspace_grant",
    "verify_existing_workspace_grant_signature",
]
