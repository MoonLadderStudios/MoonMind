"""Single workspace-source compiler for contained workspace lifecycles.

Implements ``CONTRACT-002`` (exactly one workspace source is active) and the
authorized ``existing_workspace`` source from
``docs/RepositoryAccessAndWorkspaceDesign.md`` (``CONTRACT-011``/``INV-006``).

Every source kind — ``scratch``, ``repository``, ``artifact``, ``checkpoint``,
``existing_workspace`` — compiles through this module into one
:class:`CompiledWorkspaceSource` consumed by the single materializer. Raw
server paths (``workspacePath``/``path``) are never a normal authoring route:
they are rejected for new writes and only interpretable through the explicit
historical decoder :func:`decode_historical_workspace_path`, so recorded
histories keep their meaning without reopening a host-path shortcut.

Conflicting source aliases are rejected before any read or filesystem
mutation. Unsupported backend/source combinations fail here, before launch.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping

WorkspaceSourceKind = Literal[
    "scratch", "repository", "artifact", "checkpoint", "existing_workspace"
]

# Supported workspace-restore contract carried by checkpoint sources.
# Workspace restore is separate from provider-session reattachment: a
# continuation creates a fresh execution owner and re-admits external
# operations rather than reviving the old session.
WORKSPACE_RESTORE_CONTRACT_V1 = "moonmind.workspace-restore.v1"
SUPPORTED_RESTORE_CONTRACTS = frozenset({WORKSPACE_RESTORE_CONTRACT_V1})

# Contract identifying an authorized immutable artifact import.
ARTIFACT_IMPORT_CONTRACT_V1 = "moonmind.artifact-import.v1"

# Overlay policy: an authoritative snapshot replaces destination content while
# an additive attachment overlay only adds declared inputs.
OVERLAY_AUTHORITATIVE = "authoritative"
OVERLAY_ADDITIVE = "additive"

# Historical raw-path decoding version. New-write producers must never emit
# these aliases; loaders for already-recorded payloads go through
# :func:`decode_historical_workspace_path`.
HISTORICAL_WORKSPACE_PATH_DECODER = "moonmind.workspace-legacy-path.v1"

_WORKSPACE_SOURCE_CONFLICT = "WORKSPACE_SOURCE_CONFLICT"
_WORKSPACE_SOURCE_INVALID = "WORKSPACE_SOURCE_INVALID"
_WORKSPACE_GRANT_MISMATCH = "WORKSPACE_GRANT_MISMATCH"
_WORKSPACE_RUNTIME_UNSUPPORTED = "WORKSPACE_RUNTIME_UNSUPPORTED"

_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_GRANT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# Grant HMAC secret lives in the server environment only; agents never see it.
_GRANT_HMAC_ENV = "MOONMIND_WORKSPACE_GRANT_SECRET"
_GRANT_VERSION = "grant-v1"


class WorkspaceSourceCompilationError(ValueError):
    """Exactly-one-source compilation failed before any host mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ExistingWorkspaceGrant:
    """Server-issued ownership/use grant for one existing workspace.

    An advanced selected directory is not permission for an arbitrary host
    mount: use requires this grant, which names the source workspace, the
    owning execution, the grantee workflow, the sharing mode, the expected
    generation, and a bounded lifetime. The HMAC authenticates issuance; the
    materializer additionally verifies owner/generation/expiry against the
    authoritative record store at the owning worker boundary.
    """

    grant_id: str
    source_workspace_id: str
    owner_workflow_id: str
    owner_step_execution_id: str
    grantee_workflow_id: str
    mode: str  # "exclusive" (writable) or "read_only" (explicitly shared)
    expected_generation: int
    issued_at: int
    expires_at: int
    grant_digest: str

    def payload_dict(self) -> dict[str, Any]:
        return {
            "version": _GRANT_VERSION,
            "grantId": self.grant_id,
            "sourceWorkspaceId": self.source_workspace_id,
            "ownerWorkflowId": self.owner_workflow_id,
            "ownerStepExecutionId": self.owner_step_execution_id,
            "granteeWorkflowId": self.grantee_workflow_id,
            "mode": self.mode,
            "expectedGeneration": self.expected_generation,
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
        }


@dataclass(frozen=True)
class CompiledWorkspaceSource:
    """One admitted workspace source plus its explicit overlay/input policy."""

    kind: WorkspaceSourceKind
    # Artifact sources name authorized immutable content and its digest.
    artifact_ref: str | None = None
    expected_digest: str | None = None
    # Checkpoint sources name a supported workspace-restore contract.
    checkpoint_ref: str | None = None
    restore_contract: str | None = None
    # Existing-workspace sources carry a verified locator/use grant.
    grant: ExistingWorkspaceGrant | None = None
    # Overlay/input policy.
    overlay: str = OVERLAY_AUTHORITATIVE
    input_manifest_digest: str | None = None
    # Attempt/generation the import is bound to; retries reconcile the same
    # generation while changed inputs require an explicit new import.
    attempt_id: str | None = None
    generation: int = 1


def _fail(code: str, message: str) -> WorkspaceSourceCompilationError:
    return WorkspaceSourceCompilationError(code, message)


def normalize_digest(value: str) -> str:
    """Normalize an expected content digest to ``sha256:<hex>`` form."""

    candidate = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(candidate):
        raise _fail(
            _WORKSPACE_SOURCE_INVALID,
            "workspace source digest must be a sha256 hex value",
        )
    return candidate if candidate.startswith("sha256:") else f"sha256:{candidate}"


def _artifact_id(ref: str, *, noun: str) -> str:
    value = str(ref or "").strip()
    if not value.startswith("artifact://") or not value[len("artifact://"):]:
        raise _fail(
            _WORKSPACE_SOURCE_INVALID,
            f"{noun} must be a durable artifact ref, not a local path",
        )
    return value[len("artifact://"):]


def issue_existing_workspace_grant(
    *,
    source_workspace_id: str,
    owner_workflow_id: str,
    owner_step_execution_id: str,
    grantee_workflow_id: str,
    mode: str = "exclusive",
    expected_generation: int = 1,
    lifetime_seconds: int = 3600,
    secret: str | None = None,
) -> ExistingWorkspaceGrant:
    """Issue a server-side ownership/use grant for an existing workspace."""

    if mode not in {"exclusive", "read_only"}:
        raise _fail(_WORKSPACE_SOURCE_INVALID, "grant mode must be exclusive|read_only")
    for label, value in (
        ("source workspace", source_workspace_id),
        ("owner workflow", owner_workflow_id),
        ("owner step execution", owner_step_execution_id),
        ("grantee workflow", grantee_workflow_id),
    ):
        if not str(value or "").strip():
            raise _fail(_WORKSPACE_SOURCE_INVALID, f"grant {label} is required")
    if expected_generation < 1:
        raise _fail(_WORKSPACE_SOURCE_INVALID, "grant generation must be >= 1")
    if lifetime_seconds <= 0 or lifetime_seconds > 86400:
        raise _fail(_WORKSPACE_SOURCE_INVALID, "grant lifetime must be within one day")
    now = int(time.time())
    raw_id = (
        f"{source_workspace_id}:{owner_workflow_id}:{owner_step_execution_id}:"
        f"{grantee_workflow_id}:{mode}:{expected_generation}:{now}"
    )
    grant_id = "grant_" + hashlib.sha256(raw_id.encode()).hexdigest()[:24]
    return _sign_grant(
        grant_id=grant_id,
        source_workspace_id=str(source_workspace_id).strip(),
        owner_workflow_id=str(owner_workflow_id).strip(),
        owner_step_execution_id=str(owner_step_execution_id).strip(),
        grantee_workflow_id=str(grantee_workflow_id).strip(),
        mode=mode,
        expected_generation=expected_generation,
        issued_at=now,
        expires_at=now + lifetime_seconds,
        secret=secret,
    )


def _sign_grant(*, secret: str | None = None, **fields: Any) -> ExistingWorkspaceGrant:
    key = secret if secret is not None else os.getenv(_GRANT_HMAC_ENV, "")
    if not key:
        raise _fail(
            _WORKSPACE_SOURCE_INVALID,
            "workspace grant issuance requires a server grant secret",
        )
    grant = ExistingWorkspaceGrant(grant_digest="", **fields)  # type: ignore[arg-type]
    encoded = json.dumps(grant.payload_dict(), sort_keys=True, separators=(",", ":"))
    digest = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return ExistingWorkspaceGrant(grant_digest=f"hmac-sha256:{digest}", **fields)  # type: ignore[arg-type]


def parse_existing_workspace_grant(value: Mapping[str, Any]) -> ExistingWorkspaceGrant:
    """Parse (not yet verify) a grant mapping carried in a workspace spec."""

    try:
        mode = str(value.get("mode") or "exclusive").strip()
        return ExistingWorkspaceGrant(
            grant_id=str(value.get("grantId") or "").strip(),
            source_workspace_id=str(value.get("sourceWorkspaceId") or "").strip(),
            owner_workflow_id=str(value.get("ownerWorkflowId") or "").strip(),
            owner_step_execution_id=str(value.get("ownerStepExecutionId") or "").strip(),
            grantee_workflow_id=str(value.get("granteeWorkflowId") or "").strip(),
            mode=mode,
            expected_generation=int(value.get("expectedGeneration") or 0),
            issued_at=int(value.get("issuedAt") or 0),
            expires_at=int(value.get("expiresAt") or 0),
            grant_digest=str(value.get("grantDigest") or "").strip(),
        )
    except (TypeError, ValueError) as exc:
        raise _fail(_WORKSPACE_SOURCE_INVALID, "existing-workspace grant is malformed") from exc


def verify_existing_workspace_grant(
    grant: ExistingWorkspaceGrant,
    *,
    grantee_workflow_id: str,
    expected_generation: int | None = None,
    now: int | None = None,
    secret: str | None = None,
) -> None:
    """Verify issuance authenticity, grantee binding, generation, and lifetime."""

    if not _GRANT_ID_RE.fullmatch(grant.grant_id):
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant identity is invalid")
    if grant.mode not in {"exclusive", "read_only"}:
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant mode is unsupported")
    key = secret if secret is not None else os.getenv(_GRANT_HMAC_ENV, "")
    if not key:
        raise _fail(
            _WORKSPACE_GRANT_MISMATCH,
            "workspace grant verification requires a server grant secret",
        )
    encoded = json.dumps(grant.payload_dict(), sort_keys=True, separators=(",", ":"))
    expected = hmac.new(key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(grant.grant_digest, f"hmac-sha256:{expected}"):
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant signature is invalid")
    if grant.grantee_workflow_id != str(grantee_workflow_id or "").strip():
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant is for another workflow")
    if grant.expected_generation < 1:
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant generation is invalid")
    if expected_generation is not None and grant.expected_generation != expected_generation:
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant generation is stale")
    instant = int(time.time()) if now is None else int(now)
    if not grant.issued_at or instant < grant.issued_at - 300:
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant is not yet valid")
    if instant >= grant.expires_at:
        raise _fail(_WORKSPACE_GRANT_MISMATCH, "workspace grant has expired")


def decode_historical_workspace_path(spec: Mapping[str, Any]) -> str:
    """Explicit frozen decoder for already-recorded raw workspace paths.

    Never called for new authoring; the compiler rejects ``workspacePath`` /
    ``path`` unless the caller explicitly opts into historical decoding.
    """

    for key in ("workspacePath", "path"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise _fail(_WORKSPACE_SOURCE_INVALID, "recorded workspace path is absent")


# Backend/source support matrix. An advanced selected directory is not
# permission for arbitrary host mounts, and not every source is meaningful on
# every backend. Unsupported combinations fail before launch.
_SUPPORTED_BACKENDS = ("docker_local", "docker_remote", "managed")
_BACKEND_BY_SOURCE: dict[str, frozenset[str]] = {
    "scratch": frozenset({"docker_local", "docker_remote", "managed"}),
    "repository": frozenset({"docker_local", "docker_remote", "managed"}),
    "artifact": frozenset({"docker_local", "docker_remote", "managed"}),
    "checkpoint": frozenset({"docker_local", "docker_remote", "managed"}),
    # An exclusive writable grant cannot be honored on a remote daemon view
    # that cannot fence the owner's live checkout; read-only sharing is
    # supported there through the qualified locator mapping.
    "existing_workspace": frozenset({"docker_local", "docker_remote", "managed"}),
}


def check_source_backend_supported(
    kind: WorkspaceSourceKind,
    backend: str,
    *,
    grant_mode: str | None = None,
) -> None:
    """Fail before execution when a source/backend combination is unsupported."""

    normalized = str(backend or "").strip().lower() or "docker_local"
    if normalized not in _SUPPORTED_BACKENDS:
        raise _fail(
            _WORKSPACE_RUNTIME_UNSUPPORTED,
            f"workspace backend {backend!r} is not a supported runtime",
        )
    allowed = _BACKEND_BY_SOURCE.get(str(kind))
    if allowed is None or normalized not in allowed:
        raise _fail(
            _WORKSPACE_RUNTIME_UNSUPPORTED,
            f"workspace source {kind!r} is unsupported on backend {normalized!r}",
        )
    if (
        kind == "existing_workspace"
        and normalized == "docker_remote"
        and (grant_mode or "exclusive") == "exclusive"
    ):
        raise _fail(
            _WORKSPACE_RUNTIME_UNSUPPORTED,
            "exclusive existing-workspace use is unsupported on a remote daemon; "
            "request read_only sharing or a local backend",
        )


def compile_workspace_source(
    spec: Mapping[str, Any],
    *,
    allow_historical_path: bool = False,
) -> CompiledWorkspaceSource:
    """Compile exactly one workspace source plus its overlay/input policy.

    Detects every authored alias — the ``workspaceSource`` union, the flat
    ``workspaceArtifactRef`` / ``workspaceCheckpointRestoreRef`` /
    ``workspaceExistingGrant`` keys, repository authoring, and the historical
    raw-path aliases — and rejects conflicting aliases before any read or
    filesystem mutation.
    """

    if not isinstance(spec, Mapping):
        raise _fail(_WORKSPACE_SOURCE_INVALID, "workspace spec must be a mapping")

    candidates: list[tuple[str, CompiledWorkspaceSource]] = []

    union = spec.get("workspaceSource")
    if isinstance(union, Mapping) and union:
        candidates.append(("workspaceSource", _compile_union(dict(union))))

    flat_artifact = str(spec.get("workspaceArtifactRef") or "").strip()
    flat_digest = str(spec.get("workspaceArtifactDigest") or "").strip()
    if flat_artifact or flat_digest:
        if not flat_artifact or not flat_digest:
            raise _fail(
                _WORKSPACE_SOURCE_INVALID,
                "workspaceArtifactRef requires workspaceArtifactDigest",
            )
        candidates.append(
            (
                "workspaceArtifactRef",
                CompiledWorkspaceSource(
                    kind="artifact",
                    artifact_ref=flat_artifact,
                    expected_digest=normalize_digest(flat_digest),
                    overlay=_overlay_policy(spec, default=OVERLAY_AUTHORITATIVE),
                    input_manifest_digest=_optional_digest(
                        spec.get("workspaceInputManifestDigest")
                    ),
                    attempt_id=_optional_text(spec.get("workspaceImportAttemptId")),
                    generation=_generation(spec.get("workspaceImportGeneration")),
                ),
            )
        )

    flat_checkpoint = str(spec.get("workspaceCheckpointRestoreRef") or "").strip()
    if flat_checkpoint:
        candidates.append(
            (
                "workspaceCheckpointRestoreRef",
                CompiledWorkspaceSource(
                    kind="checkpoint",
                    artifact_ref=flat_checkpoint,
                    checkpoint_ref=flat_checkpoint,
                    expected_digest=(
                        normalize_digest(str(spec.get("workspaceCheckpointDigest") or ""))
                        if str(spec.get("workspaceCheckpointDigest") or "").strip()
                        else None
                    ),
                    restore_contract=_restore_contract(spec),
                    overlay=_overlay_policy(spec, default=OVERLAY_AUTHORITATIVE),
                    input_manifest_digest=_optional_digest(
                        spec.get("workspaceInputManifestDigest")
                    ),
                    attempt_id=_optional_text(spec.get("workspaceImportAttemptId")),
                    generation=_generation(spec.get("workspaceImportGeneration")),
                ),
            )
        )

    flat_grant = spec.get("workspaceExistingGrant")
    if isinstance(flat_grant, Mapping) and flat_grant:
        grant = parse_existing_workspace_grant(flat_grant)
        candidates.append(
            (
                "workspaceExistingGrant",
                CompiledWorkspaceSource(
                    kind="existing_workspace",
                    grant=grant,
                    overlay=_overlay_policy(spec, default=OVERLAY_ADDITIVE),
                    input_manifest_digest=_optional_digest(
                        spec.get("workspaceInputManifestDigest")
                    ),
                    attempt_id=_optional_text(spec.get("workspaceImportAttemptId")),
                    generation=_generation(spec.get("workspaceImportGeneration")),
                ),
            )
        )

    if _authored_repository(spec):
        candidates.append(("repository", CompiledWorkspaceSource(kind="repository")))

    raw_path = _raw_path_alias(spec)
    if raw_path is not None:
        if not allow_historical_path:
            raise _fail(
                _WORKSPACE_SOURCE_CONFLICT,
                "workspacePath/path is a historical alias, not an authoring route; "
                "compile through decode_historical_workspace_path for recorded "
                "histories or author a workspaceSource instead",
            )
        # Historical decoding preserves the recorded meaning and nothing else:
        # a recorded raw path was preexisting authority for its own execution.
        candidates.append(
            ("workspacePath(historical)", CompiledWorkspaceSource(kind="scratch"))
        )

    # The additive overlay inputs (restoreInputRefs/attachmentRefs) are input
    # policy, not competing sources; they ride on the single compiled source.
    kinds = {compiled.kind for _, compiled in candidates}
    if len(candidates) > 1:
        names = ", ".join(name for name, _ in candidates)
        raise _fail(
            _WORKSPACE_SOURCE_CONFLICT,
            f"conflicting workspace sources before any read or mutation: {names}",
        )
    if not candidates:
        # The default experience is a new blank workspace.
        overlay = _overlay_policy(spec, default=OVERLAY_AUTHORITATIVE)
        return CompiledWorkspaceSource(kind="scratch", overlay=overlay)
    return candidates[0][1]


def _compile_union(union: dict[str, Any]) -> CompiledWorkspaceSource:
    kind = str(union.get("kind") or "").strip()
    if kind == "scratch":
        return CompiledWorkspaceSource(
            kind="scratch",
            overlay=_overlay_policy(union, default=OVERLAY_AUTHORITATIVE),
        )
    if kind == "repository":
        if not _authored_repository(union):
            raise _fail(
                _WORKSPACE_SOURCE_INVALID,
                "workspaceSource repository requires a repository target",
            )
        return CompiledWorkspaceSource(kind="repository")
    if kind == "artifact":
        ref = str(union.get("artifactRef") or "").strip()
        digest = str(union.get("expectedDigest") or union.get("digest") or "").strip()
        if not ref or not digest:
            raise _fail(
                _WORKSPACE_SOURCE_INVALID,
                "workspaceSource artifact requires artifactRef and expectedDigest",
            )
        return CompiledWorkspaceSource(
            kind="artifact",
            artifact_ref=ref,
            expected_digest=normalize_digest(digest),
            overlay=_overlay_policy(union, default=OVERLAY_AUTHORITATIVE),
            input_manifest_digest=_optional_digest(union.get("inputManifestDigest")),
            attempt_id=_optional_text(union.get("attemptId")),
            generation=_generation(union.get("generation")),
        )
    if kind == "checkpoint":
        ref = str(union.get("checkpointRef") or "").strip()
        if not ref:
            raise _fail(
                _WORKSPACE_SOURCE_INVALID,
                "workspaceSource checkpoint requires checkpointRef",
            )
        contract = str(
            union.get("restoreContract") or WORKSPACE_RESTORE_CONTRACT_V1
        ).strip()
        if contract not in SUPPORTED_RESTORE_CONTRACTS:
            raise _fail(
                _WORKSPACE_SOURCE_INVALID,
                f"unsupported workspace-restore contract {contract!r}",
            )
        digest_raw = str(union.get("expectedDigest") or union.get("digest") or "").strip()
        return CompiledWorkspaceSource(
            kind="checkpoint",
            artifact_ref=ref,
            checkpoint_ref=ref,
            expected_digest=normalize_digest(digest_raw) if digest_raw else None,
            restore_contract=contract,
            overlay=_overlay_policy(union, default=OVERLAY_AUTHORITATIVE),
            input_manifest_digest=_optional_digest(union.get("inputManifestDigest")),
            attempt_id=_optional_text(union.get("attemptId")),
            generation=_generation(union.get("generation")),
        )
    if kind == "existing_workspace":
        grant_raw = union.get("grant")
        if not isinstance(grant_raw, Mapping) or not grant_raw:
            raise _fail(
                _WORKSPACE_SOURCE_INVALID,
                "workspaceSource existing_workspace requires a server-issued grant",
            )
        return CompiledWorkspaceSource(
            kind="existing_workspace",
            grant=parse_existing_workspace_grant(grant_raw),
            overlay=_overlay_policy(union, default=OVERLAY_ADDITIVE),
            input_manifest_digest=_optional_digest(union.get("inputManifestDigest")),
            attempt_id=_optional_text(union.get("attemptId")),
            generation=_generation(union.get("generation")),
        )
    raise _fail(_WORKSPACE_SOURCE_INVALID, f"unknown workspaceSource kind {kind!r}")


def _authored_repository(spec: Mapping[str, Any]) -> bool:
    for key in ("repositoryTarget", "repository", "repo"):
        value = spec.get(key)
        if isinstance(value, Mapping):
            if str(value.get("repository", value.get("name", "")) or "").strip():
                return True
            # A repositoryTarget/repository mapping with branch-only content
            # still declares repository intent.
            if any(
                str(value.get(nested) or "").strip()
                for nested in ("branch", "startingBranch", "connectionRef")
            ):
                return True
        elif isinstance(value, str) and value.strip():
            return True
    # A bare branch without repository identity is incomplete, not a
    # repository source; it must not silently become one.
    return False


def _raw_path_alias(spec: Mapping[str, Any]) -> str | None:
    for key in ("workspacePath", "path"):
        value = spec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _overlay_policy(spec: Mapping[str, Any], *, default: str) -> str:
    raw = str(spec.get("workspaceOverlayPolicy") or spec.get("overlay") or "").strip().lower()
    if not raw:
        return default
    if raw not in {OVERLAY_AUTHORITATIVE, OVERLAY_ADDITIVE}:
        raise _fail(
            _WORKSPACE_SOURCE_INVALID,
            "workspace overlay policy must be authoritative|additive",
        )
    return raw


def _restore_contract(spec: Mapping[str, Any]) -> str:
    contract = str(
        spec.get("workspaceRestoreContract") or WORKSPACE_RESTORE_CONTRACT_V1
    ).strip()
    if contract not in SUPPORTED_RESTORE_CONTRACTS:
        raise _fail(
            _WORKSPACE_SOURCE_INVALID,
            f"unsupported workspace-restore contract {contract!r}",
        )
    return contract


def _optional_digest(value: Any) -> str | None:
    text = str(value or "").strip()
    return normalize_digest(text) if text else None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _generation(value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 1
    try:
        generation = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise _fail(_WORKSPACE_SOURCE_INVALID, "workspace import generation is invalid") from exc
    if generation < 1:
        raise _fail(_WORKSPACE_SOURCE_INVALID, "workspace import generation must be >= 1")
    return generation


def ready_binding(
    source: CompiledWorkspaceSource,
    *,
    observed_digest: str,
    target_owner: str,
) -> dict[str, Any]:
    """Bind the ready marker to source digest, contract, inputs, owner, attempt."""

    return {
        "sourceKind": source.kind,
        "sourceDigest": normalize_digest(observed_digest),
        "restoreContract": source.restore_contract
        or (ARTIFACT_IMPORT_CONTRACT_V1 if source.kind == "artifact" else None),
        "restoreContractVersion": 1,
        "inputManifestDigest": source.input_manifest_digest,
        "targetOwner": str(target_owner or "").strip(),
        "attemptId": source.attempt_id,
        "generation": source.generation,
        "overlay": source.overlay,
    }


__all__ = [
    "ARTIFACT_IMPORT_CONTRACT_V1",
    "HISTORICAL_WORKSPACE_PATH_DECODER",
    "OVERLAY_ADDITIVE",
    "OVERLAY_AUTHORITATIVE",
    "SUPPORTED_RESTORE_CONTRACTS",
    "WORKSPACE_RESTORE_CONTRACT_V1",
    "CompiledWorkspaceSource",
    "ExistingWorkspaceGrant",
    "WorkspaceSourceCompilationError",
    "WorkspaceSourceKind",
    "check_source_backend_supported",
    "compile_workspace_source",
    "decode_historical_workspace_path",
    "issue_existing_workspace_grant",
    "normalize_digest",
    "parse_existing_workspace_grant",
    "ready_binding",
    "verify_existing_workspace_grant",
]
