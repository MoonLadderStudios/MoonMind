"""Retained-state janitor for managed runtime workspaces and artifacts.

MM-950 implements the destructive cleanup path described by
``docs/ManagedAgents/ManagedRuntimeCleanup.md`` while preserving source issue
MM-940 traceability. The janitor is intentionally separate from live
managed-session orphan reaping.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from moonmind.schemas.agent_runtime_models import (
    ManagedRunRecord,
    TERMINAL_AGENT_RUN_STATES,
)
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.runtime.managed_session_store import (
    TERMINAL_MANAGED_SESSION_STATUSES,
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


_DEFAULT_RUNTIME_ROOT = "/work/agent_jobs"
_PROTECTED_RUNTIME_DIRS = frozenset(
    {"artifacts", "managed_runs", "managed_sessions", "workspaces"}
)


@dataclass(frozen=True)
class ManagedRuntimeCleanupResult:
    disabled: bool
    dry_run: bool
    scanned_run_records: int = 0
    scanned_session_records: int = 0
    scanned_workspace_roots: int = 0
    scanned_artifact_dirs: int = 0
    protected_roots: int = 0
    eligible_roots: int = 0
    deleted_roots: int = 0
    deleted_artifact_dirs: int = 0
    deleted_record_files: int = 0
    estimated_deleted_bytes: int = 0
    skipped_total: int = 0
    skipped_active: int = 0
    skipped_recent: int = 0
    skipped_unsafe_path: int = 0
    skipped_ambiguous_owner: int = 0
    errors: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "disabled": self.disabled,
            "dryRun": self.dry_run,
            "scannedRunRecords": self.scanned_run_records,
            "scannedSessionRecords": self.scanned_session_records,
            "scannedWorkspaceRoots": self.scanned_workspace_roots,
            "scannedArtifactDirs": self.scanned_artifact_dirs,
            "protectedRoots": self.protected_roots,
            "eligibleRoots": self.eligible_roots,
            "deletedRoots": self.deleted_roots,
            "deletedArtifactDirs": self.deleted_artifact_dirs,
            "deletedRecordFiles": self.deleted_record_files,
            "estimatedDeletedBytes": self.estimated_deleted_bytes,
            "skippedTotal": self.skipped_total,
            "skippedActive": self.skipped_active,
            "skippedRecent": self.skipped_recent,
            "skippedUnsafePath": self.skipped_unsafe_path,
            "skippedAmbiguousOwner": self.skipped_ambiguous_owner,
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class ManagedRuntimeJanitorConfig:
    enabled: bool = False
    dry_run: bool = True
    runtime_root: Path = Path(_DEFAULT_RUNTIME_ROOT)
    artifact_root: Path = Path(_DEFAULT_RUNTIME_ROOT) / "artifacts"
    workspace_retention: timedelta = timedelta(days=30)
    artifact_retention: timedelta = timedelta(days=90)
    record_retention: timedelta | None = None
    grace: timedelta = timedelta(seconds=3600)
    max_delete_paths: int = 25
    max_delete_bytes: int | None = None
    lock_path: Path = Path(_DEFAULT_RUNTIME_ROOT) / ".janitor.lock"

    @classmethod
    def from_env(cls) -> "ManagedRuntimeJanitorConfig":
        runtime_root = Path(
            os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", _DEFAULT_RUNTIME_ROOT)
        )
        record_retention_days = _optional_positive_int_env(
            "MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS"
        )
        return cls(
            enabled=_env_bool("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", False),
            dry_run=_env_bool("MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", True),
            runtime_root=runtime_root,
            artifact_root=managed_runtime_artifact_root(),
            workspace_retention=timedelta(
                days=_positive_int_env(
                    "MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS", 30
                )
            ),
            artifact_retention=timedelta(
                days=_positive_int_env(
                    "MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS", 90
                )
            ),
            record_retention=(
                None
                if record_retention_days is None
                else timedelta(days=record_retention_days)
            ),
            grace=timedelta(
                seconds=_positive_int_env(
                    "MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS", 3600
                )
            ),
            max_delete_paths=_positive_int_env(
                "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS", 25
            ),
            max_delete_bytes=_optional_positive_int_env(
                "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES"
            ),
            lock_path=Path(
                os.environ.get(
                    "MOONMIND_MANAGED_RUNTIME_JANITOR_LOCK_PATH",
                    str(runtime_root / ".janitor.lock"),
                )
            ),
        )


@dataclass(frozen=True)
class DockerRuntimeState:
    available: bool
    active_references: tuple[str, ...] = ()
    error: str | None = None


class DockerCliRuntimeStateProvider:
    """Reload compact Docker path/label state for janitor safety checks."""

    def __init__(self, docker_binary: str = "docker") -> None:
        self._docker_binary = docker_binary

    def load(self) -> DockerRuntimeState:
        env = os.environ.copy()
        try:
            ps = subprocess.run(
                [self._docker_binary, "ps", "-q"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            container_ids = [
                line.strip() for line in ps.stdout.splitlines() if line.strip()
            ]
            if not container_ids:
                return DockerRuntimeState(available=True)
            inspect = subprocess.run(
                [self._docker_binary, "inspect", *container_ids],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            payload = json.loads(inspect.stdout)
        except Exception as exc:
            return DockerRuntimeState(available=False, error=str(exc))

        references: set[str] = set()
        for container in payload if isinstance(payload, list) else []:
            labels = (
                container.get("Config", {}).get("Labels", {})
                if isinstance(container, Mapping)
                else {}
            )
            if isinstance(labels, Mapping):
                for value in labels.values():
                    if value:
                        references.add(str(value))
            mounts = (
                container.get("Mounts", {}) if isinstance(container, Mapping) else {}
            )
            if isinstance(mounts, list):
                for mount in mounts:
                    if not isinstance(mount, Mapping):
                        continue
                    for key in ("Source", "Destination", "Name"):
                        value = mount.get(key)
                        if value:
                            references.add(str(value))
        return DockerRuntimeState(
            available=True,
            active_references=tuple(sorted(references)),
        )


@dataclass(frozen=True)
class _Snapshot:
    run_records: tuple[ManagedRunRecord, ...]
    session_records: tuple[CodexManagedSessionRecord, ...]
    docker_state: DockerRuntimeState


@dataclass(frozen=True)
class _Candidate:
    kind: Literal["workspace", "artifact", "run_record", "session_record"]
    path: Path
    owners: tuple[ManagedRunRecord | CodexManagedSessionRecord, ...] = ()


@dataclass(frozen=True)
class _Decision:
    classification: str
    reason: str
    estimated_bytes: int = 0


class ManagedRuntimeWorkspaceJanitor:
    def __init__(
        self,
        *,
        config: ManagedRuntimeJanitorConfig,
        run_store: ManagedRunStore,
        session_store: ManagedSessionStore,
        docker_state_provider: DockerCliRuntimeStateProvider
        | Callable[[], DockerRuntimeState]
        | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._run_store = run_store
        self._session_store = session_store
        self._docker_state_provider = (
            docker_state_provider
            or DockerCliRuntimeStateProvider(
                os.environ.get("MOONMIND_DOCKER_BINARY", "docker")
            )
        )
        self._now = now or (lambda: datetime.now(tz=UTC))

    def run(self) -> ManagedRuntimeCleanupResult:
        if not self._config.enabled:
            return ManagedRuntimeCleanupResult(
                disabled=True,
                dry_run=self._config.dry_run,
            )

        self._config.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._config.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            return self._run_locked()

    def _run_locked(self) -> ManagedRuntimeCleanupResult:
        errors: list[str] = []
        try:
            snapshot = self._load_snapshot()
        except Exception as exc:
            return ManagedRuntimeCleanupResult(
                disabled=False,
                dry_run=self._config.dry_run,
                errors=(f"store-read-failed:{exc}",),
            )
        if not snapshot.docker_state.available:
            return ManagedRuntimeCleanupResult(
                disabled=False,
                dry_run=self._config.dry_run,
                scanned_run_records=len(snapshot.run_records),
                scanned_session_records=len(snapshot.session_records),
                errors=(f"docker-state-unavailable:{snapshot.docker_state.error}",),
            )

        workspace_candidates = self._workspace_candidates(snapshot)
        artifact_candidates = self._artifact_candidates(snapshot)
        result = ManagedRuntimeCleanupResult(
            disabled=False,
            dry_run=self._config.dry_run,
            scanned_run_records=len(snapshot.run_records),
            scanned_session_records=len(snapshot.session_records),
            scanned_workspace_roots=len(workspace_candidates),
            scanned_artifact_dirs=len(artifact_candidates),
        )
        delete_paths = 0
        delete_bytes = 0

        for candidate in (*workspace_candidates, *artifact_candidates):
            decision = self._classify_candidate(candidate, snapshot)
            result = self._record_decision(result, candidate, decision)
            if decision.classification != "eligible" or self._config.dry_run:
                continue
            if delete_paths >= self._config.max_delete_paths:
                result = replace(result, skipped_total=result.skipped_total + 1)
                continue
            if (
                self._config.max_delete_bytes is not None
                and delete_bytes + decision.estimated_bytes
                > self._config.max_delete_bytes
            ):
                result = replace(result, skipped_total=result.skipped_total + 1)
                continue
            delete_result = self._delete_after_rescan(candidate)
            if delete_result.classification == "deleted":
                delete_paths += 1
                delete_bytes += delete_result.estimated_bytes
                if candidate.kind == "artifact":
                    result = replace(
                        result,
                        deleted_artifact_dirs=result.deleted_artifact_dirs + 1,
                        estimated_deleted_bytes=(
                            result.estimated_deleted_bytes
                            + delete_result.estimated_bytes
                        ),
                    )
                else:
                    result = replace(
                        result,
                        deleted_roots=result.deleted_roots + 1,
                        estimated_deleted_bytes=(
                            result.estimated_deleted_bytes
                            + delete_result.estimated_bytes
                        ),
                    )
            else:
                result = self._record_decision(result, candidate, delete_result)

        if self._config.record_retention is not None:
            result = self._delete_record_candidates(result)
        return replace(result, errors=tuple((*result.errors, *errors)))

    def _load_snapshot(self) -> _Snapshot:
        docker_state = self._load_docker_state()
        run_records = tuple(self._run_store.iter_all())
        session_records = tuple(self._session_store.iter_all())
        return _Snapshot(
            run_records=run_records,
            session_records=session_records,
            docker_state=docker_state,
        )

    def _load_docker_state(self) -> DockerRuntimeState:
        provider = self._docker_state_provider
        if callable(provider) and not hasattr(provider, "load"):
            return provider()
        return provider.load()  # type: ignore[union-attr]

    def _workspace_candidates(self, snapshot: _Snapshot) -> tuple[_Candidate, ...]:
        roots: dict[Path, list[ManagedRunRecord | CodexManagedSessionRecord]] = {}
        for record in snapshot.run_records:
            root = self._ownership_root(getattr(record, "workspace_path", None))
            if root is not None:
                roots.setdefault(root, []).append(record)
        for record in snapshot.session_records:
            for raw_path in (
                record.workspace_path,
                record.session_workspace_path,
                record.artifact_spool_path,
            ):
                root = self._ownership_root(raw_path)
                if root is not None:
                    roots.setdefault(root, []).append(record)

        candidates: dict[Path, _Candidate] = {}
        for path, owners in roots.items():
            if path.exists():
                candidates[path] = _Candidate("workspace", path, tuple(owners))

        workspaces_root = self._config.runtime_root / "workspaces"
        for path in self._iter_children(workspaces_root):
            candidates.setdefault(path, _Candidate("workspace", path, ()))
        for path in self._iter_children(self._config.runtime_root):
            if path.name in _PROTECTED_RUNTIME_DIRS or path.name.startswith("."):
                continue
            candidates.setdefault(path, _Candidate("workspace", path, ()))
        return tuple(
            sorted(candidates.values(), key=lambda candidate: str(candidate.path))
        )

    def _artifact_candidates(self, snapshot: _Snapshot) -> tuple[_Candidate, ...]:
        owners_by_path: dict[
            Path, list[ManagedRunRecord | CodexManagedSessionRecord]
        ] = {}
        for record in (*snapshot.run_records, *snapshot.session_records):
            for ref in _artifact_refs(record):
                path = self._artifact_path_from_ref(ref)
                if path is not None:
                    owners_by_path.setdefault(path, []).append(record)
            spool_path = getattr(record, "artifact_spool_path", None)
            if spool_path:
                try:
                    path = Path(spool_path).resolve()
                except OSError:
                    continue
                if self._is_under(path, self._config.artifact_root.resolve()):
                    owners_by_path.setdefault(path, []).append(record)

        candidates: dict[Path, _Candidate] = {}
        for path in self._iter_children(self._config.artifact_root):
            candidates[path] = _Candidate(
                "artifact", path, tuple(owners_by_path.get(path.resolve(), ()))
            )
        return tuple(
            sorted(candidates.values(), key=lambda candidate: str(candidate.path))
        )

    def _classify_candidate(
        self,
        candidate: _Candidate,
        snapshot: _Snapshot,
    ) -> _Decision:
        if not self._safe_candidate_path(candidate):
            return _Decision("skipped_unsafe_path", "unsafe path")
        if candidate.path.is_symlink():
            return _Decision("skipped_unsafe_path", "symlink")
        if candidate.kind == "workspace" and not candidate.owners:
            return _Decision("skipped_ambiguous_owner", "no owner records")
        if candidate.kind == "artifact" and self._artifact_has_retained_owner(
            candidate
        ):
            return _Decision("protected_recent", "referenced by retained record")
        if any(_owner_active(owner) for owner in candidate.owners):
            return _Decision("protected_active", "active owner")
        if self._docker_references_path_or_owner(candidate, snapshot.docker_state):
            return _Decision("protected_active", "active docker reference")
        newest = self._newest_activity(candidate)
        if newest is None:
            return _Decision("skipped_ambiguous_owner", "missing timestamp")
        age = self._now() - newest
        retention = (
            self._config.artifact_retention
            if candidate.kind == "artifact"
            else self._config.workspace_retention
        )
        if age < retention or age < self._config.grace:
            return _Decision("protected_recent", "retention or grace not elapsed")
        return _Decision("eligible", "eligible", self._estimate_bytes(candidate.path))

    def _delete_after_rescan(self, candidate: _Candidate) -> _Decision:
        try:
            snapshot = self._load_snapshot()
        except Exception as exc:
            return _Decision("error", f"rescan failed: {exc}")
        if not snapshot.docker_state.available:
            return _Decision(
                "error",
                f"docker state unavailable: {snapshot.docker_state.error}",
            )

        refreshed = self._refresh_candidate(candidate, snapshot)
        decision = self._classify_candidate(refreshed, snapshot)
        if decision.classification != "eligible":
            return decision
        try:
            return _Decision(
                "deleted", "deleted", self._quarantine_delete(refreshed.path)
            )
        except Exception as exc:
            return _Decision("error", f"delete failed: {exc}")

    def _refresh_candidate(
        self, candidate: _Candidate, snapshot: _Snapshot
    ) -> _Candidate:
        for refreshed in (
            self._workspace_candidates(snapshot)
            if candidate.kind == "workspace"
            else self._artifact_candidates(snapshot)
        ):
            if refreshed.path.resolve() == candidate.path.resolve():
                return refreshed
        return candidate

    def _quarantine_delete(self, path: Path) -> int:
        estimated = self._estimate_bytes(path)
        quarantine = path.parent / f".gc-{uuid.uuid4()}-{path.name}"
        path.rename(quarantine)
        try:
            if quarantine.is_dir():
                shutil.rmtree(quarantine)
            else:
                quarantine.unlink(missing_ok=True)
        finally:
            if quarantine.exists():
                if quarantine.is_dir():
                    shutil.rmtree(quarantine, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        quarantine.unlink()
        return estimated

    def _delete_record_candidates(
        self,
        result: ManagedRuntimeCleanupResult,
    ) -> ManagedRuntimeCleanupResult:
        assert self._config.record_retention is not None
        try:
            snapshot = self._load_snapshot()
        except Exception as exc:
            return replace(
                result, errors=(*result.errors, f"record-rescan-failed:{exc}")
            )
        if not snapshot.docker_state.available:
            return replace(
                result,
                errors=(
                    *result.errors,
                    f"record-docker-state-unavailable:{snapshot.docker_state.error}",
                ),
            )
        deleted = 0
        errors = list(result.errors)
        for record in snapshot.run_records:
            if not self._record_delete_allowed(record):
                continue
            if self._config.dry_run:
                continue
            try:
                self._run_store.delete(record.run_id)
                deleted += 1
            except Exception as exc:
                errors.append(f"run-record-delete-failed:{record.run_id}:{exc}")
        for record in snapshot.session_records:
            if not self._record_delete_allowed(record):
                continue
            if self._config.dry_run:
                continue
            try:
                self._session_store.delete(record.session_id)
                deleted += 1
            except Exception as exc:
                errors.append(f"session-record-delete-failed:{record.session_id}:{exc}")
        return replace(
            result,
            deleted_record_files=result.deleted_record_files + deleted,
            errors=tuple(errors),
        )

    def _record_delete_allowed(
        self,
        record: ManagedRunRecord | CodexManagedSessionRecord,
    ) -> bool:
        if _owner_active(record):
            return False
        newest = _newest_owner_timestamp(record)
        if newest is None or self._now() - newest < self._config.record_retention:
            return False
        for raw_path in _record_paths(record):
            root = self._ownership_root(raw_path)
            if root is not None and root.exists():
                return False
        for ref in _artifact_refs(record):
            path = self._artifact_path_from_ref(ref)
            if path is not None and path.exists():
                return False
        return True

    def _record_decision(
        self,
        result: ManagedRuntimeCleanupResult,
        candidate: _Candidate,
        decision: _Decision,
    ) -> ManagedRuntimeCleanupResult:
        if decision.classification == "eligible":
            return replace(result, eligible_roots=result.eligible_roots + 1)
        if decision.classification == "protected_active":
            return replace(
                result,
                protected_roots=result.protected_roots + 1,
                skipped_active=result.skipped_active + 1,
                skipped_total=result.skipped_total + 1,
            )
        if decision.classification == "protected_recent":
            return replace(
                result,
                protected_roots=result.protected_roots + 1,
                skipped_recent=result.skipped_recent + 1,
                skipped_total=result.skipped_total + 1,
            )
        if decision.classification == "skipped_unsafe_path":
            return replace(
                result,
                skipped_unsafe_path=result.skipped_unsafe_path + 1,
                skipped_total=result.skipped_total + 1,
            )
        if decision.classification == "skipped_ambiguous_owner":
            return replace(
                result,
                skipped_ambiguous_owner=result.skipped_ambiguous_owner + 1,
                skipped_total=result.skipped_total + 1,
            )
        if decision.classification == "error":
            return replace(
                result,
                skipped_total=result.skipped_total + 1,
                errors=(
                    *result.errors,
                    f"{candidate.kind}:{candidate.path.name}:{decision.reason}",
                ),
            )
        return result

    def _artifact_has_retained_owner(self, candidate: _Candidate) -> bool:
        if self._config.record_retention is None:
            return bool(candidate.owners)
        for owner in candidate.owners:
            newest = _newest_owner_timestamp(owner)
            if newest is None or self._now() - newest < self._config.record_retention:
                return True
        return False

    def _newest_activity(self, candidate: _Candidate) -> datetime | None:
        timestamps = [
            timestamp
            for owner in candidate.owners
            if (timestamp := _newest_owner_timestamp(owner)) is not None
        ]
        with contextlib.suppress(OSError):
            timestamps.append(
                datetime.fromtimestamp(candidate.path.stat().st_mtime, tz=UTC)
            )
        if not timestamps:
            return None
        return max(timestamps)

    def _docker_references_path_or_owner(
        self,
        candidate: _Candidate,
        docker_state: DockerRuntimeState,
    ) -> bool:
        path_text = str(candidate.path)
        owner_ids = {
            value
            for owner in candidate.owners
            for value in (
                getattr(owner, "run_id", None),
                getattr(owner, "agent_run_id", None),
                getattr(owner, "session_id", None),
                getattr(owner, "container_id", None),
            )
            if value
        }
        for ref in docker_state.active_references:
            if ref == path_text or ref.startswith(path_text + os.sep):
                return True
            if ref in owner_ids:
                return True
        return False

    def _safe_candidate_path(self, candidate: _Candidate) -> bool:
        try:
            path = candidate.path.resolve()
            runtime_root = self._config.runtime_root.resolve()
            artifact_root = self._config.artifact_root.resolve()
        except OSError:
            return False
        if candidate.kind == "artifact":
            return path.parent == artifact_root
        workspaces_root = runtime_root / "workspaces"
        if path.parent == workspaces_root:
            return True
        return path.parent == runtime_root and path.name not in _PROTECTED_RUNTIME_DIRS

    def _ownership_root(self, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        try:
            path = Path(raw_path).resolve()
            runtime_root = self._config.runtime_root.resolve()
        except OSError:
            return None
        workspaces_root = runtime_root / "workspaces"
        if self._is_under(path, workspaces_root):
            relative = path.relative_to(workspaces_root)
            return workspaces_root / relative.parts[0] if relative.parts else None
        if self._is_under(path, runtime_root):
            relative = path.relative_to(runtime_root)
            if not relative.parts or relative.parts[0] in _PROTECTED_RUNTIME_DIRS:
                return None
            return runtime_root / relative.parts[0]
        return None

    def _artifact_path_from_ref(self, ref: str) -> Path | None:
        normalized = str(ref or "").strip()
        if not normalized:
            return None
        if "://" in normalized:
            normalized = normalized.split("://", 1)[1]
        first = Path(normalized).parts[0] if Path(normalized).parts else ""
        if not first or first in {".", ".."}:
            return None
        return (self._config.artifact_root / first).resolve()

    def _iter_children(self, root: Path) -> Iterable[Path]:
        try:
            yield from (path for path in root.iterdir() if path.exists())
        except FileNotFoundError:
            return

    def _estimate_bytes(self, path: Path) -> int:
        try:
            if path.is_file():
                return path.stat().st_size
            total = 0
            for child in path.rglob("*"):
                if child.is_symlink():
                    continue
                if child.is_file():
                    total += child.stat().st_size
            return total
        except OSError:
            return 0

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        return path == root or path.is_relative_to(root)


def _record_paths(
    record: ManagedRunRecord | CodexManagedSessionRecord,
) -> tuple[str | None, ...]:
    return (
        getattr(record, "workspace_path", None),
        getattr(record, "session_workspace_path", None),
        getattr(record, "artifact_spool_path", None),
    )


def _artifact_refs(
    record: ManagedRunRecord | CodexManagedSessionRecord,
) -> tuple[str, ...]:
    refs: list[str] = []
    for attr in (
        "log_artifact_ref",
        "stdout_artifact_ref",
        "stderr_artifact_ref",
        "merged_log_artifact_ref",
        "diagnostics_ref",
        "observability_events_ref",
        "latest_summary_ref",
        "latest_checkpoint_ref",
        "latest_control_event_ref",
        "latest_reset_boundary_ref",
    ):
        value = getattr(record, attr, None)
        if value:
            refs.append(str(value))
    return tuple(refs)


def _owner_active(record: ManagedRunRecord | CodexManagedSessionRecord) -> bool:
    if getattr(record, "active_turn_id", None):
        return True
    if isinstance(record, ManagedRunRecord):
        return record.status not in TERMINAL_AGENT_RUN_STATES
    return record.status not in TERMINAL_MANAGED_SESSION_STATUSES


def _newest_owner_timestamp(
    record: ManagedRunRecord | CodexManagedSessionRecord,
) -> datetime | None:
    timestamps = [
        value
        for value in (
            getattr(record, "finished_at", None),
            getattr(record, "last_heartbeat_at", None),
            getattr(record, "last_log_at", None),
            getattr(record, "updated_at", None),
            getattr(record, "started_at", None),
        )
        if isinstance(value, datetime)
    ]
    if not timestamps:
        return None
    return max(
        value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        for value in timestamps
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _optional_positive_int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def cleanup_managed_runtime_files(
    *,
    run_store: ManagedRunStore,
    session_store: ManagedSessionStore,
    config: ManagedRuntimeJanitorConfig | None = None,
    docker_state_provider: DockerCliRuntimeStateProvider
    | Callable[[], DockerRuntimeState]
    | None = None,
) -> ManagedRuntimeCleanupResult:
    janitor = ManagedRuntimeWorkspaceJanitor(
        config=config or ManagedRuntimeJanitorConfig.from_env(),
        run_store=run_store,
        session_store=session_store,
        docker_state_provider=docker_state_provider,
    )
    return janitor.run()


__all__ = (
    "DockerCliRuntimeStateProvider",
    "DockerRuntimeState",
    "ManagedRuntimeCleanupResult",
    "ManagedRuntimeJanitorConfig",
    "ManagedRuntimeWorkspaceJanitor",
    "cleanup_managed_runtime_files",
)
