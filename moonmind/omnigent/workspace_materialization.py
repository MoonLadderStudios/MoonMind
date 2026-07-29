"""Owner-boundary materialization of the authored normal-workflow workspace.

The normal MoonMind Workflow path compiles a durable, path-free intent (the
:class:`WorkspaceMaterializationSpec`) describing the exact repository/branch/
attachment/input/checkpoint state the operator authored.  The owning worker
resolves the authorized :class:`SandboxWorkspaceLocator` first (identity,
containment, traversal, and symlink guards) and only then materializes that
intent into the resolved directory.

This module owns the materialization semantics that were previously missing on
the Omnigent normal path: the workflow payload carries refs and identity, never
an absolute worker path, Docker-daemon path, volume name, caller-selected bind
source, or credential body.  Cloning, checkout, attachment and input-ref
projection, and checkpoint/external-state restore all run here, behind the
locator boundary, using the shared bounded/redacted/cancellation-aware command
runner.  Materialization is idempotent so a retry cannot produce a second clone,
checkout, or restore.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A worker-side command executor with the same contract as
# ``OmnigentOAuthHostRuntime._run``: bounded, redacted, cancellation-aware, and
# returning decoded ``(return_code, stdout, stderr)``.
RunCommand = Callable[..., Awaitable["tuple[int, str, str]"]]

_ABSOLUTE_MARKER = ("/", "\\")
_VOLUME_MARKER = ("type=volume", "type=bind", "src=", "dst=")
_SOCKET_MARKER = ("docker.sock", "/var/run/docker", "unix://")
_CLONE_URL_SCHEMES = frozenset({"http", "https", "ssh", "git", "file"})


class WorkspaceMaterializationError(RuntimeError):
    """Stable materialization failure suitable for activity error handling."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _reject_pathlike(value: str, *, field_name: str) -> str:
    text = str(value).strip()
    if not text:
        return text
    lowered = text.lower()
    if text.startswith(_ABSOLUTE_MARKER) or (len(text) > 1 and text[1] == ":"):
        raise ValueError(f"{field_name} must not be an absolute worker/daemon path")
    if any(marker in lowered for marker in _SOCKET_MARKER):
        raise ValueError(f"{field_name} must not carry Docker socket authority")
    if any(marker in lowered for marker in _VOLUME_MARKER):
        raise ValueError(f"{field_name} must not carry a bind/volume mount source")
    return text


def _normalized_relative_destination(value: str) -> str:
    candidate = str(value).strip().replace("\\", "/")
    path = PurePosixPath(candidate)
    if (
        not candidate
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(
            "attachment destination must be a normalized relative path without traversal"
        )
    return str(path)


class WorkspaceAttachmentRef(BaseModel):
    """A single attachment carried as an artifact ref plus a relative target."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    artifact_ref: str = Field(..., alias="artifactRef", min_length=1, max_length=2000)
    destination: str = Field(..., alias="destination", min_length=1, max_length=1000)

    _validate_destination = field_validator("destination", mode="before")(
        _normalized_relative_destination
    )

    @field_validator("artifact_ref", mode="before")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _reject_pathlike(value, field_name="attachment artifactRef")


class WorkspaceMaterializationSpec(BaseModel):
    """Frozen, path-free intent for materializing one authored workspace.

    Every field is durable identity or an artifact/source ref.  The model forbids
    absolute worker/daemon paths, bind/volume sources, Docker socket authority,
    and embedded credentials so the workflow payload can never smuggle host
    authority across the browser/Temporal boundary.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal["v1"] = Field("v1", alias="schemaVersion")
    repository: str = Field(..., alias="repository", min_length=1, max_length=500)
    source_evidence_ref: str | None = Field(
        None, alias="sourceEvidenceRef", max_length=2000
    )
    starting_branch: str | None = Field(None, alias="startingBranch", max_length=500)
    target_branch: str | None = Field(None, alias="targetBranch", max_length=500)
    commit: str | None = Field(None, alias="commit", max_length=200)
    attachments: tuple[WorkspaceAttachmentRef, ...] = Field(
        default_factory=tuple, alias="attachments"
    )
    input_refs: tuple[str, ...] = Field(default_factory=tuple, alias="inputRefs")
    checkpoint_ref: str | None = Field(None, alias="checkpointRef", max_length=2000)
    external_state_ref: str | None = Field(
        None, alias="externalStateRef", max_length=2000
    )
    repository_mutation: bool = Field(False, alias="repositoryMutation")
    publish_mode: str = Field("none", alias="publishMode", max_length=64)

    @field_validator("repository", mode="before")
    @classmethod
    def _validate_repository(cls, value: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError("repository is required")
        parsed = urlsplit(text)
        if parsed.scheme in _CLONE_URL_SCHEMES:
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("repository URL must not embed credentials")
            return text
        if text.startswith("git@"):
            return text
        # ``owner/name`` shorthand — reject anything that looks like a worker path.
        if text.startswith(_ABSOLUTE_MARKER) or ".." in PurePosixPath(text).parts:
            raise ValueError("repository must be a remote identity, not a local path")
        return text

    @field_validator("commit", mode="before")
    @classmethod
    def _validate_commit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if not all(char in "0123456789abcdefABCDEF" for char in text):
            raise ValueError("commit must be a hexadecimal git object id")
        return text.lower()

    @field_validator("input_refs", mode="before")
    @classmethod
    def _validate_input_refs(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, bytes)):
            raise ValueError("inputRefs must be a sequence of artifact refs")
        refs: list[str] = []
        for item in value:
            ref = _reject_pathlike(str(item), field_name="inputRef")
            if ref:
                refs.append(ref)
        return tuple(dict.fromkeys(refs))

    @model_validator(mode="after")
    def _validate_refs(self) -> WorkspaceMaterializationSpec:
        for ref, name in (
            (self.source_evidence_ref, "sourceEvidenceRef"),
            (self.checkpoint_ref, "checkpointRef"),
            (self.external_state_ref, "externalStateRef"),
        ):
            if ref:
                _reject_pathlike(ref, field_name=name)
        return self

    def digest(self) -> str:
        """Stable content digest used for idempotent retry detection."""

        payload = json.dumps(
            self.model_dump(by_alias=True, mode="json"), sort_keys=True
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_request_payload(
        cls,
        *,
        workspace_spec: Mapping[str, Any] | None,
        parameters: Mapping[str, Any] | None,
        input_refs: Sequence[str] | None = None,
    ) -> WorkspaceMaterializationSpec | None:
        """Compile a spec from the ordinary Create/edit/rerun/schedule payload.

        Returns ``None`` for non-repository work (for example a preflight-only or
        already-materialized locator), so the caller preserves the existing
        resolve-only contract.
        """

        spec_map = dict(workspace_spec or {})
        params = dict(parameters or {})
        explicit = spec_map.get("materialization")
        if isinstance(explicit, Mapping):
            return cls.model_validate(explicit)

        repository = str(
            spec_map.get("repository") or params.get("repository") or ""
        ).strip()
        if not repository:
            return None

        starting_branch = _first_text(
            spec_map.get("startingBranch"),
            spec_map.get("branch"),
            params.get("startingBranch"),
        )
        target_branch = _first_text(
            spec_map.get("targetBranch"),
            params.get("targetBranch"),
        )
        commit = _first_text(spec_map.get("commit"), spec_map.get("ref"))

        attachments: list[dict[str, Any]] = []
        raw_attachments = spec_map.get("attachments")
        if isinstance(raw_attachments, Sequence) and not isinstance(
            raw_attachments, (str, bytes)
        ):
            for item in raw_attachments:
                if not isinstance(item, Mapping):
                    continue
                ref = str(
                    item.get("artifactRef") or item.get("ref") or ""
                ).strip()
                destination = str(
                    item.get("destination") or item.get("path") or ""
                ).strip()
                if ref and destination:
                    attachments.append(
                        {"artifactRef": ref, "destination": destination}
                    )

        publish_mode = str(params.get("publishMode") or "none").strip() or "none"

        return cls.model_validate(
            {
                "repository": repository,
                "startingBranch": starting_branch or None,
                "targetBranch": target_branch or None,
                "commit": commit or None,
                "attachments": attachments,
                "inputRefs": list(input_refs or ()),
                "checkpointRef": _first_text(spec_map.get("checkpointRef")) or None,
                "externalStateRef": _first_text(spec_map.get("externalStateRef"))
                or None,
                "repositoryMutation": bool(
                    params.get("repositoryMutationRequired")
                    or publish_mode not in {"", "none"}
                ),
                "publishMode": publish_mode,
            }
        )


def _first_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


class WorkspaceArtifactReader(Protocol):
    async def read_bytes(self, artifact_ref: str) -> bytes:  # pragma: no cover - proto
        raise NotImplementedError


class WorkspaceRestoreBoundary(Protocol):
    async def restore(
        self, *, ref: str, destination: Path, idempotency_key: str
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol
        raise NotImplementedError


def _to_clone_url(repository: str) -> str:
    if urlsplit(repository).scheme in _CLONE_URL_SCHEMES:
        return repository
    if repository.startswith("git@"):
        return repository
    return f"https://github.com/{repository}.git"


def _credential_git_args(github_token: str | None) -> tuple[list[str], dict[str, str]]:
    """Return token-safe git ``-c`` args and env.

    The token is only ever read from the environment by an inline credential
    helper, so it never appears in argv, the persisted ``.git/config`` remote,
    or captured evidence.
    """

    if not github_token:
        return [], {}
    helper = (
        "!f() { echo username=x-access-token; "
        'echo "password=${MOONMIND_MATERIALIZE_TOKEN}"; }; f'
    )
    args = ["-c", "credential.helper=", "-c", f"credential.helper={helper}"]
    return args, {"MOONMIND_MATERIALIZE_TOKEN": github_token}


async def materialize_workspace(
    *,
    spec: WorkspaceMaterializationSpec,
    owned_root: Path,
    repo_path: Path,
    run_command: RunCommand,
    artifact_reader: WorkspaceArtifactReader | None = None,
    github_token: str | None = None,
    restore_boundary: WorkspaceRestoreBoundary | None = None,
) -> dict[str, Any]:
    """Materialize the authored workspace behind an already-resolved locator.

    ``owned_root`` is the authorized workspace identity root (``.../<workspace_id>``)
    and ``repo_path`` is the resolved locator directory inside it.  Both are
    produced by the containment-checked locator resolution before this runs.
    """

    owned_root.mkdir(parents=True, exist_ok=True)
    marker = owned_root / ".mm-materialization.json"
    digest = spec.digest()

    existing = _load_marker(marker)
    if existing is not None:
        if existing.get("specDigest") != digest:
            raise WorkspaceMaterializationError(
                "WORKSPACE_MATERIALIZATION_SPEC_CONFLICT",
                "a different workspace intent already materialized this identity",
            )
        # Retry safety: the authorized workspace is already materialized for this
        # exact intent.  Do not clone, checkout, restore, or re-project again.
        evidence = dict(existing.get("evidence") or {})
        evidence["reused"] = True
        return evidence

    if not spec.repository:
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_REPOSITORY_REQUIRED",
            "repository identity is required to materialize the workspace",
        )

    cred_args, cred_env = _credential_git_args(github_token)

    cloned = False
    git_dir = repo_path / ".git"
    if not git_dir.exists():
        # Preserve existing uncommitted / restored work: only clone into a fresh
        # directory.  A retry that finds a checkout keeps it.
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        await _git(
            run_command,
            [*cred_args, "clone", _to_clone_url(spec.repository), str(repo_path)],
            cwd=owned_root,
            env=cred_env,
            failure="WORKSPACE_MATERIALIZATION_CLONE_FAILED",
        )
        cloned = True

    checked_out_ref = ""
    if spec.commit:
        await _git(
            run_command,
            [*cred_args, "fetch", "--all", "--prune"],
            cwd=repo_path,
            env=cred_env,
            check=False,
        )
        await _git(
            run_command,
            ["checkout", spec.commit],
            cwd=repo_path,
            failure="WORKSPACE_MATERIALIZATION_CHECKOUT_FAILED",
        )
        checked_out_ref = spec.commit
    elif spec.starting_branch:
        await _git(
            run_command,
            [*cred_args, "fetch", "--all", "--prune"],
            cwd=repo_path,
            env=cred_env,
            check=False,
        )
        await _git(
            run_command,
            ["checkout", spec.starting_branch],
            cwd=repo_path,
            failure="WORKSPACE_MATERIALIZATION_CHECKOUT_FAILED",
        )
        checked_out_ref = spec.starting_branch

    source_commit = await _resolve_head_commit(run_command, repo_path)

    attachment_count = await _materialize_attachments(spec, repo_path, artifact_reader)
    input_count = await _materialize_input_refs(spec, owned_root, artifact_reader)
    restore_evidence_ref = await _restore_external_state(
        spec, owned_root, restore_boundary
    )

    evidence: dict[str, Any] = {
        "materialized": True,
        "reused": False,
        "repository": spec.repository,
        "sourceCommit": source_commit,
        "startingBranch": spec.starting_branch or "",
        "targetBranch": spec.target_branch or "",
        "checkedOutRef": checked_out_ref,
        "cloned": cloned,
        "attachmentCount": attachment_count,
        "inputRefCount": input_count,
        "restoreEvidenceRef": restore_evidence_ref,
        "repositoryMutation": spec.repository_mutation,
        "publishMode": spec.publish_mode,
        "sourceEvidenceRef": spec.source_evidence_ref or "",
    }
    _write_marker(marker, {"specDigest": digest, "evidence": evidence})
    return evidence


def _load_marker(marker: Path) -> Mapping[str, Any] | None:
    if not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_EVIDENCE_INVALID",
            "durable materialization evidence is unreadable",
        ) from exc
    if not isinstance(value, Mapping):
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_EVIDENCE_INVALID",
            "durable materialization evidence is malformed",
        )
    return value


def _write_marker(marker: Path, value: Mapping[str, Any]) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(tmp, marker)


async def _git(
    run_command: RunCommand,
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    failure: str | None = None,
) -> tuple[int, str, str]:
    command_env = dict(os.environ)
    if env:
        command_env.update(env)
    return_code, stdout, stderr = await run_command(
        "git",
        "-C",
        str(cwd),
        *args,
        env=command_env,
        check=False,
    )
    if check and return_code != 0:
        raise WorkspaceMaterializationError(
            failure or "WORKSPACE_MATERIALIZATION_COMMAND_FAILED",
            "git materialization command failed",
        )
    return return_code, stdout, stderr


async def _resolve_head_commit(run_command: RunCommand, repo_path: Path) -> str:
    return_code, stdout, _ = await _git(
        run_command, ["rev-parse", "HEAD"], cwd=repo_path, check=False
    )
    if return_code != 0:
        return ""
    return stdout.strip()


async def _materialize_attachments(
    spec: WorkspaceMaterializationSpec,
    repo_path: Path,
    artifact_reader: WorkspaceArtifactReader | None,
) -> int:
    if not spec.attachments:
        return 0
    if artifact_reader is None:
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_ARTIFACTS_UNAVAILABLE",
            "attachments were declared but no artifact reader is configured",
        )
    repo_root = repo_path.resolve()
    for attachment in spec.attachments:
        destination = (repo_path / attachment.destination).resolve()
        if not destination.is_relative_to(repo_root):
            raise WorkspaceMaterializationError(
                "WORKSPACE_MATERIALIZATION_ATTACHMENT_ESCAPE",
                "attachment destination escapes the authorized workspace",
            )
        payload = await artifact_reader.read_bytes(attachment.artifact_ref)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return len(spec.attachments)


async def _materialize_input_refs(
    spec: WorkspaceMaterializationSpec,
    owned_root: Path,
    artifact_reader: WorkspaceArtifactReader | None,
) -> int:
    if not spec.input_refs:
        return 0
    if artifact_reader is None:
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_ARTIFACTS_UNAVAILABLE",
            "input refs were declared but no artifact reader is configured",
        )
    inputs_root = (owned_root / "inputs").resolve()
    inputs_root.mkdir(parents=True, exist_ok=True)
    for index, ref in enumerate(spec.input_refs):
        payload = await artifact_reader.read_bytes(ref)
        name = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16]
        (inputs_root / f"{index:04d}-{name}").write_bytes(payload)
    return len(spec.input_refs)


async def _restore_external_state(
    spec: WorkspaceMaterializationSpec,
    owned_root: Path,
    restore_boundary: WorkspaceRestoreBoundary | None,
) -> str:
    ref = spec.checkpoint_ref or spec.external_state_ref
    if not ref:
        return ""
    if restore_boundary is None:
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_RESTORE_UNAVAILABLE",
            "checkpoint/external-state restore boundary is not configured",
        )
    result = await restore_boundary.restore(
        ref=ref,
        destination=owned_root,
        idempotency_key=f"{owned_root.name}:{spec.digest()}",
    )
    evidence_ref = str(
        result.get("restoreEvidenceRef")
        or result.get("restorationEvidenceRef")
        or ""
    ).strip()
    if not evidence_ref:
        raise WorkspaceMaterializationError(
            "WORKSPACE_MATERIALIZATION_RESTORE_MISMATCH",
            "restore boundary returned no durable evidence ref",
        )
    return evidence_ref


__all__ = [
    "RunCommand",
    "WorkspaceArtifactReader",
    "WorkspaceAttachmentRef",
    "WorkspaceMaterializationError",
    "WorkspaceMaterializationSpec",
    "WorkspaceRestoreBoundary",
    "materialize_workspace",
]
