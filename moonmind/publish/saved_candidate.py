"""Publish immutable saved work through the existing publisher.

GitHub issue MoonLadderStudios/MoonMind#4018.

``PublishService`` receives saved content that its restore and protection
owners (#4014, #4015/#4017) already verified and materialized. This module
never looks up the original source, runs a model, or imports the source's
credentials, hooks, or Git configuration. It admits one destination decision,
builds a deterministic candidate commit in a fresh contained workspace, and
records push and pull-request effects separately so a retry reconciles the
same target before repeating any mutation.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas.saved_work_models import saved_work_path_exclusion
from moonmind.utils.logging import redact_sensitive_text

_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_DELETED = "0 " + "0" * 40
_PASSTHROUGH_ENV = (
    "PATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "GIT_SSL_CAINFO",
)

SavedPublicationStrategy = Literal[
    "baseline_delta", "additive_import", "empty_initialization"
]


class SavedPublicationError(RuntimeError):
    """A bounded, operator-visible saved-publication rejection."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: tuple[str, ...] | list[str] = (),
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.details = tuple(details)
        self.retryable = retryable
        super().__init__(f"{code}: {message}")


def _safe_relative_path(value: Any, name: str) -> str:
    text = str(value or "")
    parts = text.split("/")
    if (
        not text
        or text.startswith("/")
        or "\\" in text
        or any(ch in text for ch in "\0\n\r")
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold() == ".git" for part in parts)
    ):
        raise ValueError(f"{name} must be a contained relative path")
    return text


def _branch_name(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if (
        not _BRANCH.fullmatch(text)
        or text.startswith(("-", "/"))
        or text.endswith(("/", ".", ".lock"))
        or ".." in text
        or "//" in text
    ):
        raise ValueError(f"{name} must be a plain branch name")
    return text


class SavedPublicationCommit(BaseModel):
    """Fixed commit identity so a rebuilt candidate has the same SHA."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    message: str
    author_name: str = Field(..., alias="authorName")
    author_email: str = Field(..., alias="authorEmail")
    timestamp: datetime

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text or "\0" in text:
            raise ValueError("commit message must not be blank")
        return text

    @field_validator("author_name", "author_email")
    @classmethod
    def _identity(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text or any(ch in text for ch in "\0\n\r<>"):
            raise ValueError(f"{info.field_name} must be a compact value")
        return text

    @field_validator("timestamp")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("commit timestamp must include a timezone")
        return value

    def git_env(self) -> dict[str, str]:
        date = f"@{int(self.timestamp.timestamp())} {self.timestamp.strftime('%z')}"
        return {
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email,
            "GIT_COMMITTER_DATE": date,
        }


class SavedPublicationAdmission(BaseModel):
    """One admitted destination decision for one saved-work digest.

    Changing the content, base, mapping, deletions, or authority yields a
    different ``decision_digest``, so an old decision cannot be reused.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    saved_work_digest: str = Field(..., alias="savedWorkDigest")
    repository: str
    objective: Literal["branch", "pr"]
    base_branch: str | None = Field(None, alias="baseBranch")
    head_branch: str = Field(..., alias="headBranch")
    strategy: SavedPublicationStrategy
    expected_base_sha: str | None = Field(None, alias="expectedBaseSha")
    expected_head_sha: str | None = Field(None, alias="expectedHeadSha")
    path_prefix: str | None = Field(None, alias="pathPrefix")
    authorized_deletions: tuple[str, ...] = Field((), alias="authorizedDeletions")
    authority_ref: str = Field(..., alias="authorityRef")
    commit: SavedPublicationCommit

    @field_validator("saved_work_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        if not _DIGEST.fullmatch(str(value or "")):
            raise ValueError("savedWorkDigest must be a sha256 digest")
        return value

    @field_validator("repository")
    @classmethod
    def _repository(cls, value: str) -> str:
        owner, sep, name = str(value or "").strip().partition("/")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", name
        ):
            raise ValueError("repository must be an owner/name identity")
        return f"{owner}/{name}"

    @field_validator("base_branch", "head_branch")
    @classmethod
    def _branches(cls, value: str | None, info: Any) -> str | None:
        return None if value is None else _branch_name(value, info.field_name)

    @field_validator("expected_base_sha", "expected_head_sha")
    @classmethod
    def _shas(cls, value: str | None, info: Any) -> str | None:
        if value is not None and not _SHA.fullmatch(value):
            raise ValueError(f"{info.field_name} must be a full commit SHA")
        return value

    @field_validator("path_prefix")
    @classmethod
    def _prefix(cls, value: str | None) -> str | None:
        return None if value is None else _safe_relative_path(value, "pathPrefix")

    @field_validator("authorized_deletions")
    @classmethod
    def _deletions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        paths = tuple(_safe_relative_path(item, "authorizedDeletions") for item in value)
        if len(set(paths)) != len(paths):
            raise ValueError("authorizedDeletions must not repeat a path")
        return paths

    @field_validator("authority_ref")
    @classmethod
    def _authority(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text or "\n" in text or "\r" in text:
            raise ValueError("authorityRef must be a compact non-blank value")
        return text

    @model_validator(mode="after")
    def _supported(self) -> SavedPublicationAdmission:
        if self.strategy == "empty_initialization":
            if self.objective != "branch" or self.base_branch is not None:
                raise ValueError(
                    "empty initialization publishes one branch with no base"
                )
            if self.expected_base_sha or self.expected_head_sha or self.authorized_deletions:
                raise ValueError("empty initialization has no remote expectation")
            return self
        if not self.base_branch or not self.expected_base_sha:
            raise ValueError("an existing destination requires baseBranch and expectedBaseSha")
        if self.head_branch == self.base_branch:
            raise ValueError("saved work publishes to a head branch distinct from its base")
        if self.strategy == "baseline_delta" and self.path_prefix is not None:
            raise ValueError("a baseline delta applies at its recorded paths")
        return self

    def decision_digest(self) -> str:
        payload = self.model_dump(by_alias=True, mode="json")
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class SavedEntry:
    path: str
    kind: Literal["file", "symlink"] = "file"
    executable: bool = False


@dataclass(frozen=True, slots=True)
class SavedContent:
    """Materialized saved files already verified by their restore owner."""

    root: Path
    entries: tuple[SavedEntry, ...]
    digest: str
    baseline_commit: str | None = None
    excluded_paths: tuple[str, ...] = ()
    # Deletions recorded by capture's exact-baseline delta. ``None`` infers
    # them from the recorded baseline tree; a recorded delta is authoritative,
    # so a path absent from the snapshot but never recorded as deleted stays.
    recorded_deletions: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SavedCandidate:
    head_sha: str | None
    base_sha: str | None
    tree_sha: str
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    no_change: bool
    decision_digest: str


@dataclass(frozen=True, slots=True)
class CandidatePushOutcome:
    status: Literal["pushed", "reconciled", "no_change", "conflict", "unavailable"]
    reason_code: str
    head_sha: str | None
    remote_head_sha: str | None = None
    remote_verified: bool = False
    retryable: bool = False
    summary: str = ""


@dataclass(frozen=True, slots=True)
class PullRequestOutcome:
    status: Literal[
        "created", "adopted", "conflict", "closed", "merged", "unavailable", "rejected"
    ]
    url: str | None = None
    head_sha: str | None = None
    retryable: bool = False
    summary: str = ""


def admitted_token(*, github_token: str | None, bound_credential: Any | None) -> str:
    """Return only admitted destination authority; never ambient process state."""

    token = str(github_token or "").strip()
    if not token and bound_credential is not None:
        captured: list[str] = []
        bound_credential.credential.use_now(
            lambda raw: captured.append(bytes(raw).decode("utf-8").strip())
        )
        token = captured[0] if captured else ""
    if not token or "\n" in token or "\r" in token:
        raise SavedPublicationError(
            "PUBLICATION_AUTHORITY_UNAVAILABLE",
            "admitted destination authority is required",
        )
    return token


def github_remote_url(repository: str) -> str:
    return f"https://github.com/{repository}.git"


@dataclass(frozen=True, slots=True)
class _GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace").strip()


class _ContainedGit:
    """Git with no host/global config, hooks, templates, prompts, or ambient tokens."""

    def __init__(
        self, *, git_binary: str, workspace: Path, token: str, remote_url: str
    ) -> None:
        from moonmind.workflows.temporal.runtime.git_auth import (
            build_github_token_git_environment,
        )

        self.workspace = workspace
        self.remote_url = remote_url
        self._git_binary = git_binary
        self._token = token
        self.local_env = {
            **{key: os.environ[key] for key in _PASSTHROUGH_ENV if key in os.environ},
            "HOME": os.devnull,
            "XDG_CONFIG_HOME": os.devnull,
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        host = urlsplit(remote_url).hostname if "://" in remote_url else None
        self._network_env = build_github_token_git_environment(
            token, base_env=self.local_env, host=host or "github.com"
        )

    async def run(
        self,
        *args: str,
        network: bool = False,
        input: bytes | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float = 300,
    ) -> _GitResult:
        process = await asyncio.create_subprocess_exec(
            self._git_binary,
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(cwd or self.workspace),
            *args,
            stdin=asyncio.subprocess.PIPE if input is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**(self._network_env if network else self.local_env), **(env or {})},
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(input), timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
                await process.wait()
            return _GitResult(124, b"", b"git timed out")
        code = process.returncode
        return _GitResult(-1 if code is None else code, stdout, stderr)

    async def checked(self, *args: str, **kwargs: Any) -> _GitResult:
        result = await self.run(*args, **kwargs)
        if result.returncode != 0:
            raise SavedPublicationError(
                "PUBLICATION_WORKSPACE_FAILED",
                f"git {args[0]} failed: {self.summary(result)}",
            )
        return result

    def summary(self, result: _GitResult) -> str:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        detail = detail.replace(self._token, "[REDACTED]") if self._token else detail
        return redact_sensitive_text(detail)[:500] or f"exit {result.returncode}"

    async def remote_refs(self, *patterns: str) -> dict[str, str] | None:
        """Read remote refs; ``None`` means unavailable, never confirmed absence."""

        result = await self.run("ls-remote", self.remote_url, *patterns, network=True)
        if result.returncode != 0:
            return None
        refs: dict[str, str] = {}
        for line in result.text.splitlines():
            sha, _, name = line.partition("\t")
            if name:
                refs[name.strip()] = sha.strip()
        return refs


def _parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def _is_excluded(path: str, excluded: tuple[str, ...]) -> bool:
    return (
        any(path == item or path.startswith(item.rstrip("/") + "/") for item in excluded)
        or saved_work_path_exclusion(path) is not None
    )


def _verified_entries(
    admission: SavedPublicationAdmission, content: SavedContent
) -> dict[str, SavedEntry]:
    """Bind saved content to the admitted digest and map it to destination paths."""

    if content.digest != admission.saved_work_digest:
        raise SavedPublicationError(
            "PUBLICATION_CONTENT_MISMATCH",
            "saved content digest differs from the admitted decision",
        )
    root = Path(content.root)
    contained_root = root.resolve()
    mapped: dict[str, SavedEntry] = {}
    problems: list[str] = []
    for entry in content.entries:
        try:
            path = _safe_relative_path(entry.path, "saved path")
        except ValueError:
            problems.append(f"unsafe_path:{entry.path!r}")
            continue
        if saved_work_path_exclusion(path) is not None:
            problems.append(f"excluded_by_current_policy:{path}")
            continue
        location = root.joinpath(*path.split("/"))
        parent = location.parent.resolve()
        if parent != contained_root and contained_root not in parent.parents:
            problems.append(f"uncontained_path:{path}")
            continue
        if entry.kind == "symlink":
            present = location.is_symlink()
        else:
            present = location.is_file() and not location.is_symlink()
        if not present:
            problems.append(f"materialized_entry_missing:{path}")
            continue
        destination = f"{admission.path_prefix}/{path}" if admission.path_prefix else path
        if destination in mapped:
            problems.append(f"duplicate_path:{destination}")
            continue
        mapped[destination] = entry
    if problems:
        raise SavedPublicationError(
            "PUBLICATION_CONTENT_INVALID",
            "saved content does not match its verified entries",
            details=sorted(problems),
        )
    return mapped


async def _tree_entries(git: _ContainedGit, rev: str) -> dict[str, tuple[str, str]]:
    result = await git.checked("ls-tree", "-r", "-z", "--full-tree", rev)
    entries: dict[str, tuple[str, str]] = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, _kind, sha = meta.decode().split()
        entries[name.decode("utf-8", errors="surrogateescape")] = (mode, sha)
    return entries


async def _hash_saved(
    git: _ContainedGit, root: Path, mapped: dict[str, SavedEntry]
) -> dict[str, tuple[str, str]]:
    hashed: dict[str, tuple[str, str]] = {}
    files = [(dest, entry) for dest, entry in mapped.items() if entry.kind == "file"]
    if files:
        paths = "".join(
            str(root.joinpath(*entry.path.split("/"))) + "\n" for _, entry in files
        )
        result = await git.checked(
            "hash-object", "-w", "--no-filters", "--stdin-paths", input=paths.encode()
        )
        shas = result.text.splitlines()
        for (dest, entry), sha in zip(files, shas, strict=True):
            hashed[dest] = ("100755" if entry.executable else "100644", sha)
    for dest, entry in mapped.items():
        if entry.kind == "symlink":
            target = os.fsencode(os.readlink(root.joinpath(*entry.path.split("/"))))
            result = await git.checked("hash-object", "-w", "--stdin", input=target)
            hashed[dest] = ("120000", result.text)
    return hashed


class _DestinationLayout:
    """Destination paths, directories, and case-folded names for placement checks."""

    def __init__(self, destination: dict[str, tuple[str, str]]) -> None:
        self.files = destination
        self.directories = {parent for path in destination for parent in _parents(path)}
        self.folded: dict[str, str] = {}
        for name in (*self.directories, *destination):
            self.folded.setdefault(name.casefold(), name)

    def conflict(self, path: str) -> str | None:
        """Reject an addition that would replace a file/directory or collide by case."""

        if path in self.directories or any(
            parent in self.files for parent in _parents(path)
        ):
            return f"path_type_conflict:{path}"
        for candidate in (*_parents(path), path):
            existing = self.folded.get(candidate.casefold())
            if existing is not None and existing != candidate:
                return f"case_collision:{path}"
        return None


async def _baseline_operations(
    git: _ContainedGit,
    content: SavedContent,
    *,
    base_sha: str,
    destination: dict[str, tuple[str, str]],
    saved: dict[str, tuple[str, str]],
    conflicts: list[str],
) -> tuple[dict[str, tuple[str, str]], set[str]]:
    baseline = str(content.baseline_commit or "")
    if (
        not _SHA.fullmatch(baseline)
        or (await git.run("cat-file", "-e", f"{baseline}^{{commit}}")).returncode
        or (await git.run("merge-base", "--is-ancestor", baseline, base_sha)).returncode
    ):
        raise SavedPublicationError(
            "PUBLICATION_BASELINE_UNAVAILABLE",
            "the destination does not contain the saved work's recorded baseline",
        )
    recorded = await _tree_entries(git, baseline)
    changes: dict[str, tuple[str, str]] = {}
    deletions: set[str] = set()
    for path in sorted(set(recorded) | set(saved)):
        old, new, current = recorded.get(path), saved.get(path), destination.get(path)
        # Capture exclusion is never evidence of deletion.
        if new is None and (
            _is_excluded(path, content.excluded_paths)
            or (
                content.recorded_deletions is not None
                and path not in content.recorded_deletions
            )
        ):
            continue
        if old == new or current == new:
            continue
        if current != old:
            conflicts.append(f"destination_changed_since_baseline:{path}")
        elif new is None:
            deletions.add(path)
        else:
            changes[path] = new
    return changes, deletions


async def prepare_candidate(
    *,
    git_binary: str,
    admission: SavedPublicationAdmission,
    content: SavedContent,
    workspace: Path,
    token: str,
    remote_url: str,
    persisted_head_sha: str | None = None,
) -> SavedCandidate:
    """Build the admitted candidate commit in a fresh contained workspace.

    ``persisted_head_sha`` rebuilds an already persisted candidate: the base is
    fetched by its admitted commit rather than the branch's current tip, and
    the rebuilt commit must be byte-identical to the persisted one.
    """

    mapped = _verified_entries(admission, content)
    workspace = Path(workspace)
    if workspace.exists() and any(workspace.iterdir()):
        raise SavedPublicationError(
            "PUBLICATION_WORKSPACE_NOT_FRESH",
            "saved publication requires a fresh owned workspace",
        )
    workspace.mkdir(parents=True, exist_ok=True)
    git = _ContainedGit(
        git_binary=git_binary, workspace=workspace, token=token, remote_url=remote_url
    )
    await git.checked("init", "-q", "--template=", str(workspace), cwd=workspace.parent)

    base_sha: str | None = None
    destination: dict[str, tuple[str, str]] = {}
    if admission.strategy == "empty_initialization":
        # A rebuild relies on the emptiness confirmed at admission; its push
        # lease still requires the head branch to be absent or hold this commit.
        refs = {} if persisted_head_sha else await git.remote_refs()
        if refs is None:
            raise SavedPublicationError(
                "PUBLICATION_DESTINATION_UNAVAILABLE",
                "destination emptiness could not be confirmed",
                retryable=True,
            )
        if refs:
            raise SavedPublicationError(
                "PUBLICATION_DESTINATION_NOT_EMPTY",
                "empty initialization requires a confirmed empty destination",
                details=sorted(refs)[:20],
            )
    else:
        base_ref = f"refs/heads/{admission.base_branch}"
        source = f"+{admission.expected_base_sha}:refs/moonmind/destination-base"
        if not persisted_head_sha:
            refs = await git.remote_refs(base_ref)
            if refs is None:
                raise SavedPublicationError(
                    "PUBLICATION_DESTINATION_UNAVAILABLE",
                    "destination base could not be observed",
                    retryable=True,
                )
            if refs.get(base_ref) != admission.expected_base_sha:
                raise SavedPublicationError(
                    "PUBLICATION_STALE_EXPECTATION",
                    "destination base differs from the admitted expectation",
                    details=(f"{base_ref}:{refs.get(base_ref) or 'absent'}",),
                )
            source = f"+{base_ref}:refs/moonmind/destination-base"
        depth = () if admission.strategy == "baseline_delta" else ("--depth=1",)
        fetched = await git.run(
            "fetch",
            "--no-tags",
            "--quiet",
            *depth,
            remote_url,
            source,
            network=True,
        )
        if fetched.returncode != 0:
            raise SavedPublicationError(
                "PUBLICATION_DESTINATION_UNAVAILABLE",
                f"destination base could not be fetched: {git.summary(fetched)}",
                retryable=True,
            )
        observed = (await git.checked("rev-parse", "refs/moonmind/destination-base")).text
        if observed != admission.expected_base_sha:
            raise SavedPublicationError(
                "PUBLICATION_STALE_EXPECTATION",
                "destination base moved while it was fetched",
                details=(f"{base_ref}:{observed}",),
            )
        base_sha = observed
        destination = await _tree_entries(git, base_sha)

    saved = await _hash_saved(git, Path(content.root), mapped)
    conflicts: list[str] = []
    if admission.strategy == "baseline_delta":
        assert base_sha is not None
        changes, deletions = await _baseline_operations(
            git,
            content,
            base_sha=base_sha,
            destination=destination,
            saved=saved,
            conflicts=conflicts,
        )
    else:
        # Additive import never overwrites or deletes a destination-only file.
        changes, deletions = {}, set()
        for path, new in saved.items():
            current = destination.get(path)
            if current is None:
                changes[path] = new
            elif current != new:
                conflicts.append(f"destination_path_conflict:{path}")
    layout = _DestinationLayout(destination)
    saved_folded: dict[str, str] = {}
    for path in sorted(changes):
        if path not in destination:
            conflict = layout.conflict(path)
            if conflict:
                conflicts.append(conflict)
        if saved_folded.setdefault(path.casefold(), path) != path:
            conflicts.append(f"case_collision:{path}")
    for path in admission.authorized_deletions:
        if path not in destination:
            conflicts.append(f"deletion_target_missing:{path}")
        elif path in saved:
            conflicts.append(f"deletion_conflicts_with_saved_path:{path}")
        else:
            deletions.add(path)
    if conflicts:
        raise SavedPublicationError(
            "PUBLICATION_APPLICATION_CONFLICT",
            "saved work cannot be applied without changing unrelated destination content",
            details=sorted(set(conflicts)),
        )

    if base_sha:
        await git.checked("read-tree", base_sha)
    else:
        await git.checked("read-tree", "--empty")
    records = b"".join(
        f"{mode} {sha}\t".encode() + path.encode("utf-8", "surrogateescape") + b"\0"
        for path, (mode, sha) in sorted(changes.items())
    ) + b"".join(
        f"{_DELETED}\t".encode() + path.encode("utf-8", "surrogateescape") + b"\0"
        for path in sorted(deletions)
    )
    if records:
        await git.checked("update-index", "-z", "--index-info", input=records)
    tree = (await git.checked("write-tree")).text
    base_tree = (
        (await git.checked("rev-parse", f"{base_sha}^{{tree}}")).text
        if base_sha
        else _EMPTY_TREE
    )
    decision = admission.decision_digest()
    if tree == base_tree:
        if persisted_head_sha and persisted_head_sha != base_sha:
            raise SavedPublicationError(
                "PUBLICATION_CANDIDATE_MISMATCH",
                "rebuilt saved content no longer differs from its admitted base",
            )
        return SavedCandidate(
            head_sha=base_sha,
            base_sha=base_sha,
            tree_sha=tree,
            changed_paths=(),
            deleted_paths=(),
            no_change=True,
            decision_digest=decision,
        )
    parent = ("-p", base_sha) if base_sha else ()
    head = (
        await git.checked(
            "commit-tree",
            tree,
            *parent,
            "-F",
            "-",
            input=admission.commit.message.encode() + b"\n",
            env=admission.commit.git_env(),
        )
    ).text
    if persisted_head_sha and head != persisted_head_sha:
        raise SavedPublicationError(
            "PUBLICATION_CANDIDATE_MISMATCH",
            "rebuilt candidate differs from the persisted candidate",
            details=(f"persisted:{persisted_head_sha}", f"rebuilt:{head}"),
        )
    await git.checked("update-ref", "refs/moonmind/candidate", head)
    return SavedCandidate(
        head_sha=head,
        base_sha=base_sha,
        tree_sha=tree,
        changed_paths=tuple(sorted(changes)),
        deleted_paths=tuple(sorted(deletions)),
        no_change=False,
        decision_digest=decision,
    )


async def observe_branch(
    *,
    git_binary: str,
    workspace: Path,
    branch: str,
    token: str,
    remote_url: str,
) -> str | None:
    """Observe one destination branch tip once; ``None`` means confirmed absent."""

    ref = f"refs/heads/{_branch_name(branch, 'branch')}"
    git = _ContainedGit(
        git_binary=git_binary, workspace=Path(workspace), token=token, remote_url=remote_url
    )
    refs = await git.remote_refs(ref)
    if refs is None:
        raise SavedPublicationError(
            "PUBLICATION_DESTINATION_UNAVAILABLE",
            "destination branch could not be observed",
            retryable=True,
        )
    return refs.get(ref)


async def push_candidate(
    *,
    git_binary: str,
    repo_dir: Path,
    head_branch: str,
    candidate_sha: str,
    base_sha: str | None,
    expected_remote_sha: str | None,
    token: str,
    remote_url: str,
    scan: Callable[[Path, str, str | None, dict[str, str]], Awaitable[None]],
) -> CandidatePushOutcome:
    """Push exactly the persisted candidate under the admitted expectation."""

    if candidate_sha == base_sha:
        return CandidatePushOutcome(
            status="no_change", reason_code="no_change", head_sha=candidate_sha
        )
    head_ref = f"refs/heads/{_branch_name(head_branch, 'head_branch')}"
    git = _ContainedGit(
        git_binary=git_binary, workspace=Path(repo_dir), token=token, remote_url=remote_url
    )
    lineage = await git.run("rev-list", "--parents", "-n", "1", f"{candidate_sha}^{{commit}}")
    if lineage.returncode != 0 or lineage.text.split() != [
        candidate_sha,
        *([base_sha] if base_sha else []),
    ]:
        raise SavedPublicationError(
            "PUBLICATION_CANDIDATE_MISMATCH",
            "the workspace does not hold the persisted candidate on its admitted base",
        )

    def outcome(status: Any, reason: str, remote: str | None, **extra: Any):
        return CandidatePushOutcome(
            status=status,
            reason_code=reason,
            head_sha=candidate_sha,
            remote_head_sha=remote,
            remote_verified=remote == candidate_sha,
            **extra,
        )

    before = await git.remote_refs(head_ref)
    if before is None:
        return outcome(
            "unavailable", "remote_observation_unavailable", None, retryable=True
        )
    observed = before.get(head_ref)
    if observed == candidate_sha:
        return outcome("reconciled", "remote_head_matches_candidate", observed)
    if observed != expected_remote_sha:
        # A newly observed tip is never promoted into overwrite permission.
        return outcome("conflict", "remote_head_changed", observed)
    try:
        await scan(Path(repo_dir), candidate_sha, base_sha, git.local_env)
    except RuntimeError as exc:
        raise SavedPublicationError("PUBLICATION_SCAN_BLOCKED", str(exc)) from exc
    pushed = await git.run(
        "push",
        "--porcelain",
        f"--force-with-lease={head_ref}:{expected_remote_sha or ''}",
        remote_url,
        f"{candidate_sha}:{head_ref}",
        network=True,
    )
    after = await git.remote_refs(head_ref)
    remote = None if after is None else after.get(head_ref)
    if after is None:
        return outcome(
            "unavailable",
            "push_outcome_unverified",
            None,
            retryable=True,
            summary=git.summary(pushed) if pushed.returncode else "",
        )
    if remote == candidate_sha:
        reason = "pushed" if pushed.returncode == 0 else "push_acknowledgment_reconciled"
        return outcome("pushed", reason, remote)
    if remote == expected_remote_sha and pushed.returncode != 0:
        return outcome(
            "unavailable",
            "push_failed",
            remote,
            retryable=True,
            summary=git.summary(pushed),
        )
    return outcome("conflict", "remote_head_changed", remote)


async def publish_pull_request(
    *,
    github: Any,
    repository: str,
    head_branch: str,
    base_branch: str,
    candidate_sha: str,
    draft: bool,
    title: str,
    body: str,
    token: str,
) -> PullRequestOutcome:
    """Reconcile this head/base before creating, then verify the provider result."""

    observed = await github.reconcile_pull_request(
        repo=repository,
        head=head_branch,
        base=base_branch,
        expected_head_sha=candidate_sha,
        draft=draft,
        github_token=token,
    )
    if observed.state == "unavailable":
        return PullRequestOutcome(
            status="unavailable", retryable=observed.retryable, summary=observed.summary
        )
    if observed.state != "absent":
        # Adoption performs no metadata write; a closed/merged result or another
        # actor's PR is never an invitation to create another one.
        status = {"matched": "adopted", "mismatched": "conflict"}.get(
            observed.state, observed.state
        )
        return PullRequestOutcome(
            status=status,
            url=observed.url,
            head_sha=observed.head_sha,
            summary=observed.summary,
        )
    result = await github.create_pull_request(
        repo=repository,
        head=head_branch,
        base=base_branch,
        title=title,
        body=body,
        draft=draft,
        github_token=token,
    )
    if result.created or result.adopted:
        head = str(result.head_sha or "").strip()
        # Adoption must prove the candidate head; creation may omit the head.
        if head != candidate_sha and (result.adopted or head):
            return PullRequestOutcome(
                status="conflict",
                url=result.url,
                head_sha=head or None,
                summary="pull request head differs from the published candidate",
            )
        return PullRequestOutcome(
            status="created" if result.created else "adopted",
            url=result.url,
            head_sha=candidate_sha,
            summary=result.summary,
        )
    # A lost acknowledgment is reconciled by the next attempt, never re-created blind.
    return PullRequestOutcome(
        status="unavailable" if result.retryable else "rejected",
        retryable=bool(result.retryable),
        summary=result.summary,
    )


__all__ = [
    "CandidatePushOutcome",
    "PullRequestOutcome",
    "SavedCandidate",
    "SavedContent",
    "SavedEntry",
    "SavedPublicationAdmission",
    "SavedPublicationCommit",
    "SavedPublicationError",
    "admitted_token",
    "github_remote_url",
    "observe_branch",
    "prepare_candidate",
    "publish_pull_request",
    "push_candidate",
]
