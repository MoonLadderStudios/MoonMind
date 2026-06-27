"""Retained-state managed runtime cleanup classification.

Implements MM-949 from source issue MM-940.
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

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

ManagedRuntimeCandidateKind = Literal[
    "workspace",
    "artifact",
    "run_record",
    "session_record",
]
ManagedRuntimeCleanupClassification = Literal[
    "protected_active",
    "protected_recent",
    "protected_shared",
    "eligible",
    "deleted",
    "skipped_unsafe_path",
    "skipped_ambiguous_owner",
    "error",
]

_FALSEY = frozenset({"", "0", "false", "no", "off"})


@dataclass(frozen=True)
class ManagedRuntimeCleanupConfig:
    enabled: bool = False
    dry_run: bool = True
    workspace_retention: timedelta = timedelta(days=30)
    artifact_retention: timedelta = timedelta(days=90)
    record_retention: timedelta | None = None
    grace: timedelta = timedelta(hours=1)
    max_delete_paths: int = 25
    max_delete_bytes: int | None = None
    lock_path: Path = Path("/work/agent_jobs/.janitor.lock")
    runtime_store_root: Path = Path("/work/agent_jobs")
    artifact_root: Path = Path("/work/agent_jobs/artifacts")

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "ManagedRuntimeCleanupConfig":
        source = env or os.environ
        runtime_store_root = Path(
            source.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
            or "/work/agent_jobs"
        )
        artifact_root = managed_runtime_artifact_root()
        if env is not None:
            raw_artifact_root = source.get("MOONMIND_AGENT_RUNTIME_ARTIFACTS")
            if raw_artifact_root is not None:
                artifact_root = Path(raw_artifact_root or str(runtime_store_root))
                if artifact_root.name != "artifacts":
                    artifact_root = artifact_root / "artifacts"
        return cls(
            enabled=_env_bool(
                source, "MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", False
            ),
            dry_run=_env_bool(
                source, "MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", True
            ),
            workspace_retention=timedelta(
                days=_env_int(
                    source, "MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS", 30
                )
            ),
            artifact_retention=timedelta(
                days=_env_int(
                    source, "MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS", 90
                )
            ),
            record_retention=_env_optional_days(
                source, "MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS"
            ),
            grace=timedelta(
                seconds=_env_int(
                    source, "MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS", 3600
                )
            ),
            max_delete_paths=max(
                0,
                _env_int(
                    source, "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS", 25
                ),
            ),
            max_delete_bytes=_env_optional_int(
                source, "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES"
            ),
            lock_path=Path(
                source.get(
                    "MOONMIND_MANAGED_RUNTIME_JANITOR_LOCK_PATH",
                    str(runtime_store_root / ".janitor.lock"),
                )
            ),
            runtime_store_root=runtime_store_root,
            artifact_root=artifact_root,
        )


@dataclass(frozen=True)
class ManagedRuntimeCleanupCandidate:
    kind: ManagedRuntimeCandidateKind
    path: Path
    ownership_root: Path | None = None
    run_records: tuple[ManagedRunRecord, ...] = ()
    session_records: tuple[CodexManagedSessionRecord, ...] = ()


@dataclass(frozen=True)
class ManagedRuntimeCleanupDecision:
    kind: ManagedRuntimeCandidateKind
    path: str
    ownership_root: str | None
    classification: ManagedRuntimeCleanupClassification
    reason: str
    newest_activity_at: datetime | None = None
    estimated_bytes: int = 0


@dataclass(frozen=True)
class ManagedRuntimeCleanupResult:
    disabled: bool
    dry_run: bool
    scanned_run_records: int
    scanned_session_records: int
    scanned_workspace_roots: int
    scanned_artifact_dirs: int
    protected_roots: int
    eligible_roots: int
    deleted_roots: int
    deleted_artifact_dirs: int
    deleted_record_files: int
    estimated_deleted_bytes: int
    skipped_active: int
    skipped_recent: int
    skipped_unsafe_path: int
    skipped_ambiguous_owner: int
    errors: tuple[str, ...]
    decisions: tuple[ManagedRuntimeCleanupDecision, ...] = ()

    def to_dict(self) -> dict[str, object]:
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
            "skippedActive": self.skipped_active,
            "skippedRecent": self.skipped_recent,
            "skippedUnsafePath": self.skipped_unsafe_path,
            "skippedAmbiguousOwner": self.skipped_ambiguous_owner,
            "errors": list(self.errors),
            "decisions": [
                {
                    "kind": decision.kind,
                    "path": decision.path,
                    "ownershipRoot": decision.ownership_root,
                    "classification": decision.classification,
                    "reason": decision.reason,
                    "newestActivityAt": (
                        decision.newest_activity_at.isoformat()
                        if decision.newest_activity_at
                        else None
                    ),
                    "estimatedBytes": decision.estimated_bytes,
                }
                for decision in self.decisions
            ],
        }


@dataclass
class _CleanupBudget:
    deleted_paths: int = 0
    deleted_bytes: int = 0


@dataclass(frozen=True)
class DockerReferenceState:
    active_container_refs: frozenset[str] = field(default_factory=frozenset)
    active_mount_paths: frozenset[str] = field(default_factory=frozenset)
    failed: bool = False
    reason: str | None = None


DockerReferenceProvider = Callable[[], DockerReferenceState | Mapping[str, object]]
CleanupProgressCallback = Callable[[Mapping[str, object]], None]


class ManagedRuntimeWorkspaceJanitor:
    """Classify and optionally delete retained managed-runtime filesystem state."""

    def __init__(
        self,
        *,
        run_store: ManagedRunStore,
        session_store: ManagedSessionStore,
        config: ManagedRuntimeCleanupConfig | None = None,
        docker_reference_provider: DockerReferenceProvider | None = None,
        progress_callback: CleanupProgressCallback | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_store = run_store
        self._session_store = session_store
        self._config = config or ManagedRuntimeCleanupConfig.from_env()
        self._docker_reference_provider = docker_reference_provider
        self._progress_callback = progress_callback
        self._now = now or (lambda: datetime.now(tz=UTC))

    def run(self) -> ManagedRuntimeCleanupResult:
        config = self._config
        if not config.enabled:
            try:
                scanned_run_records = len(tuple(self._run_store.iter_all()))
                scanned_session_records = len(tuple(self._session_store.iter_all()))
                errors: tuple[str, ...] = ()
            except (OSError, ValueError) as exc:
                scanned_run_records = 0
                scanned_session_records = 0
                errors = (f"unreadable_store: {exc}",)
            return ManagedRuntimeCleanupResult(
                disabled=True,
                dry_run=config.dry_run,
                scanned_run_records=scanned_run_records,
                scanned_session_records=scanned_session_records,
                scanned_workspace_roots=0,
                scanned_artifact_dirs=0,
                protected_roots=0,
                eligible_roots=0,
                deleted_roots=0,
                deleted_artifact_dirs=0,
                deleted_record_files=0,
                estimated_deleted_bytes=0,
                skipped_active=0,
                skipped_recent=0,
                skipped_unsafe_path=0,
                skipped_ambiguous_owner=0,
                errors=errors,
            )
        with _JanitorLock(config.lock_path):
            return self._run_enabled_pass()

    def _run_enabled_pass(self) -> ManagedRuntimeCleanupResult:
        errors: list[str] = []
        try:
            run_records = tuple(self._run_store.iter_all())
            session_records = tuple(self._session_store.iter_all())
        except (OSError, ValueError) as exc:
            return self._empty_enabled_error(f"unreadable_store: {exc}")
        docker_state = self._docker_reference_state()
        if docker_state.failed:
            errors.append(docker_state.reason or "docker_reference_scan_failed")
        candidates = self._build_candidates(run_records, session_records)
        budget = _CleanupBudget()
        decisions: list[ManagedRuntimeCleanupDecision] = []
        for index, candidate in enumerate(candidates):
            self._emit_progress("classify", candidate, index=index, total=len(candidates))
            decisions.append(
                self._classify_candidate(
                    candidate,
                    docker_state=docker_state,
                    budget=budget,
                )
            )
        return self._summarize(
            tuple(decisions),
            scanned_run_records=len(run_records),
            scanned_session_records=len(session_records),
            errors=tuple(errors),
        )

    def _empty_enabled_error(self, error: str) -> ManagedRuntimeCleanupResult:
        return ManagedRuntimeCleanupResult(
            disabled=False,
            dry_run=self._config.dry_run,
            scanned_run_records=0,
            scanned_session_records=0,
            scanned_workspace_roots=0,
            scanned_artifact_dirs=0,
            protected_roots=0,
            eligible_roots=0,
            deleted_roots=0,
            deleted_artifact_dirs=0,
            deleted_record_files=0,
            estimated_deleted_bytes=0,
            skipped_active=0,
            skipped_recent=0,
            skipped_unsafe_path=0,
            skipped_ambiguous_owner=0,
            errors=(error,),
        )

    def _build_candidates(
        self,
        run_records: Sequence[ManagedRunRecord],
        session_records: Sequence[CodexManagedSessionRecord],
    ) -> tuple[ManagedRuntimeCleanupCandidate, ...]:
        groups: dict[Path, dict[str, list[object]]] = defaultdict(
            lambda: {"runs": [], "sessions": []}
        )
        ambiguous: list[ManagedRuntimeCleanupCandidate] = []
        for record in run_records:
            root = self._ownership_root_for_path(record.workspace_path)
            if root is None:
                if record.workspace_path:
                    ambiguous.append(
                        ManagedRuntimeCleanupCandidate(
                            kind="workspace",
                            path=Path(record.workspace_path),
                            run_records=(record,),
                        )
                    )
                continue
            groups[root]["runs"].append(record)
        for record in session_records:
            roots = {
                self._ownership_root_for_path(record.workspace_path),
                self._ownership_root_for_path(record.session_workspace_path),
            }
            roots.discard(None)
            if not roots:
                ambiguous.append(
                    ManagedRuntimeCleanupCandidate(
                        kind="workspace",
                        path=Path(record.workspace_path),
                        session_records=(record,),
                    )
                )
                continue
            for root in roots:
                assert root is not None
                groups[root]["sessions"].append(record)
        candidates = [
            ManagedRuntimeCleanupCandidate(
                kind="workspace",
                path=root,
                ownership_root=root,
                run_records=tuple(values["runs"]),  # type: ignore[arg-type]
                session_records=tuple(values["sessions"]),  # type: ignore[arg-type]
            )
            for root, values in sorted(groups.items(), key=lambda item: str(item[0]))
        ]
        known_roots = {candidate.path for candidate in candidates}
        candidates.extend(self._workspace_path_candidates(known_roots))
        candidates.extend(ambiguous)
        candidates.extend(self._artifact_candidates(run_records, session_records))
        candidates.extend(self._record_candidates(run_records, session_records))
        return tuple(candidates)

    def _workspace_path_candidates(
        self, known_roots: set[Path]
    ) -> list[ManagedRuntimeCleanupCandidate]:
        runtime_root = self._config.runtime_store_root
        candidates: list[ManagedRuntimeCleanupCandidate] = []
        workspace_parent = runtime_root / "workspaces"
        if workspace_parent.exists():
            for path in sorted(child for child in workspace_parent.iterdir() if child.is_dir()):
                if path not in known_roots:
                    candidates.append(
                        ManagedRuntimeCleanupCandidate(
                            kind="workspace",
                            path=path,
                            ownership_root=path,
                        )
                    )
        if runtime_root.exists():
            reserved = {"artifacts", "managed_runs", "managed_sessions", "workspaces"}
            for path in sorted(child for child in runtime_root.iterdir() if child.is_dir()):
                if path.name in reserved or path in known_roots:
                    continue
                candidates.append(
                    ManagedRuntimeCleanupCandidate(
                        kind="workspace",
                        path=path,
                        ownership_root=path,
                    )
                )
        return candidates

    def _artifact_candidates(
        self,
        run_records: Sequence[ManagedRunRecord],
        session_records: Sequence[CodexManagedSessionRecord],
    ) -> list[ManagedRuntimeCleanupCandidate]:
        refs_by_job: dict[str, dict[str, list[object]]] = defaultdict(
            lambda: {"runs": [], "sessions": []}
        )
        for record in run_records:
            for ref in _run_artifact_refs(record):
                job = _artifact_job_id(ref)
                if job:
                    refs_by_job[job]["runs"].append(record)
        for record in session_records:
            for ref in record.published_artifact_refs():
                job = _artifact_job_id(ref)
                if job:
                    refs_by_job[job]["sessions"].append(record)
        candidates: list[ManagedRuntimeCleanupCandidate] = []
        root = self._config.artifact_root
        if root.exists():
            for path in sorted(child for child in root.iterdir() if child.is_dir()):
                values = refs_by_job.get(path.name, {"runs": [], "sessions": []})
                candidates.append(
                    ManagedRuntimeCleanupCandidate(
                        kind="artifact",
                        path=path,
                        ownership_root=path,
                        run_records=tuple(values["runs"]),  # type: ignore[arg-type]
                        session_records=tuple(values["sessions"]),  # type: ignore[arg-type]
                    )
                )
        return candidates

    def _record_candidates(
        self,
        run_records: Sequence[ManagedRunRecord],
        session_records: Sequence[CodexManagedSessionRecord],
    ) -> list[ManagedRuntimeCleanupCandidate]:
        if self._config.record_retention is None:
            return []
        runtime_root = self._config.runtime_store_root
        return [
            *(
                ManagedRuntimeCleanupCandidate(
                    kind="run_record",
                    path=runtime_root / "managed_runs" / f"{record.run_id}.json",
                    run_records=(record,),
                )
                for record in run_records
            ),
            *(
                ManagedRuntimeCleanupCandidate(
                    kind="session_record",
                    path=runtime_root
                    / "managed_sessions"
                    / f"{record.session_id}.json",
                    session_records=(record,),
                )
                for record in session_records
            ),
        ]

    def _ownership_root_for_path(self, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        path = Path(raw_path)
        runtime_root = self._config.runtime_store_root
        workspaces_root = runtime_root / "workspaces"
        try:
            relative = path.absolute().relative_to(workspaces_root.absolute())
            if relative.parts:
                return workspaces_root / relative.parts[0]
        except (OSError, ValueError):
            # Paths outside /workspaces fall through to the per-run root check.
            pass
        try:
            relative = path.absolute().relative_to(runtime_root.absolute())
            if relative.parts and relative.parts[0] not in {
                "artifacts",
                "managed_runs",
                "managed_sessions",
                "workspaces",
            }:
                return runtime_root / relative.parts[0]
        except (OSError, ValueError):
            # Paths outside the runtime root have no cleanup ownership root.
            pass
        return None

    def _classify_candidate(
        self,
        candidate: ManagedRuntimeCleanupCandidate,
        *,
        docker_state: DockerReferenceState,
        budget: _CleanupBudget,
    ) -> ManagedRuntimeCleanupDecision:
        path = candidate.path
        kind = candidate.kind
        try:
            if not self._path_allowed(kind, path):
                return self._decision(candidate, "skipped_unsafe_path", "unsafe path")
            if path.is_symlink():
                return self._decision(
                    candidate, "skipped_unsafe_path", "candidate path is a symlink"
                )
            if kind == "workspace" and candidate.ownership_root is None:
                return self._decision(
                    candidate,
                    "skipped_ambiguous_owner",
                    "ownership root could not be derived",
                )
            if (
                kind == "workspace"
                and not candidate.run_records
                and not candidate.session_records
            ):
                return self._decision(
                    candidate,
                    "skipped_ambiguous_owner",
                    "no durable owner records reference candidate",
                )
            if docker_state.failed:
                return self._decision(
                    candidate,
                    "protected_active",
                    docker_state.reason or "docker reference scan failed",
                )
            if self._has_active_owner(candidate):
                classification: ManagedRuntimeCleanupClassification = (
                    "protected_shared"
                    if kind == "workspace"
                    and len(candidate.run_records) + len(candidate.session_records) > 1
                    else "protected_active"
                )
                return self._decision(
                    candidate, classification, "active owner or active turn"
                )
            if self._has_live_docker_reference(candidate, docker_state):
                return self._decision(
                    candidate, "protected_active", "live Docker reference"
                )
            newest = self._newest_activity(candidate)
            if newest is None:
                return self._decision(
                    candidate,
                    "protected_recent",
                    "missing owner and filesystem timestamps",
                )
            retention = self._retention_for(kind)
            if retention is None:
                return self._decision(
                    candidate, "protected_recent", "record retention disabled", newest
                )
            age = self._now() - newest
            if age < retention:
                return self._decision(
                    candidate,
                    "protected_recent",
                    "retention window has not elapsed",
                    newest,
                )
            if age < self._config.grace:
                return self._decision(
                    candidate, "protected_recent", "grace window has not elapsed", newest
                )
            self._emit_progress("size", candidate)
            estimated_bytes = _path_size(path, progress_callback=self._progress_callback)
            if budget.deleted_paths >= self._config.max_delete_paths:
                return self._decision(
                    candidate,
                    "protected_recent",
                    "delete path cap reached",
                    newest,
                    estimated_bytes,
                )
            max_bytes = self._config.max_delete_bytes
            if max_bytes is not None and budget.deleted_bytes + estimated_bytes > max_bytes:
                return self._decision(
                    candidate,
                    "protected_recent",
                    "delete byte cap reached",
                    newest,
                    estimated_bytes,
                )
            if self._config.dry_run:
                return self._decision(
                    candidate, "eligible", "dry-run would delete", newest, estimated_bytes
                )
            if self._rescan_blocks_delete(candidate):
                return self._decision(
                    candidate,
                    "protected_active",
                    "failed rescan before delete",
                    newest,
                    estimated_bytes,
                )
            self._emit_progress("delete", candidate)
            self._delete_candidate(candidate)
            budget.deleted_paths += 1
            budget.deleted_bytes += estimated_bytes
            return self._decision(candidate, "deleted", "deleted", newest, estimated_bytes)
        except OSError as exc:
            return self._decision(candidate, "error", f"filesystem error: {exc}")

    def _decision(
        self,
        candidate: ManagedRuntimeCleanupCandidate,
        classification: ManagedRuntimeCleanupClassification,
        reason: str,
        newest: datetime | None = None,
        estimated_bytes: int = 0,
    ) -> ManagedRuntimeCleanupDecision:
        return ManagedRuntimeCleanupDecision(
            kind=candidate.kind,
            path=str(candidate.path),
            ownership_root=(
                str(candidate.ownership_root) if candidate.ownership_root else None
            ),
            classification=classification,
            reason=reason,
            newest_activity_at=newest,
            estimated_bytes=estimated_bytes,
        )

    def _path_allowed(self, kind: ManagedRuntimeCandidateKind, path: Path) -> bool:
        roots = {
            "workspace": (
                self._config.runtime_store_root,
                self._config.runtime_store_root / "workspaces",
            ),
            "artifact": (self._config.artifact_root,),
            "run_record": (self._config.runtime_store_root / "managed_runs",),
            "session_record": (self._config.runtime_store_root / "managed_sessions",),
        }[kind]
        try:
            candidate = path.absolute()
            return any(candidate.is_relative_to(root.absolute()) for root in roots)
        except OSError:
            return False

    def _has_active_owner(self, candidate: ManagedRuntimeCleanupCandidate) -> bool:
        return any(
            record.status not in TERMINAL_AGENT_RUN_STATES or record.active_turn_id
            for record in candidate.run_records
        ) or any(
            record.status not in TERMINAL_MANAGED_SESSION_STATUSES
            or record.active_turn_id
            for record in candidate.session_records
        )

    def _has_live_docker_reference(
        self,
        candidate: ManagedRuntimeCleanupCandidate,
        docker_state: DockerReferenceState,
    ) -> bool:
        ids = set()
        paths = {str(candidate.path)}
        for record in candidate.run_records:
            ids.update(
                value
                for value in (record.run_id, record.session_id, record.container_id)
                if value
            )
            if record.workspace_path:
                paths.add(str(record.workspace_path))
        for record in candidate.session_records:
            ids.update(
                value
                for value in (record.session_id, record.agent_run_id, record.container_id)
                if value
            )
            paths.update(
                (
                    record.workspace_path,
                    record.session_workspace_path,
                    record.artifact_spool_path,
                )
            )
        if ids.intersection(docker_state.active_container_refs):
            return True
        return any(
            mount == path or mount.startswith(f"{path.rstrip('/')}/")
            for mount in docker_state.active_mount_paths
            for path in paths
        )

    def _newest_activity(
        self, candidate: ManagedRuntimeCleanupCandidate
    ) -> datetime | None:
        timestamps: list[datetime] = []
        for record in candidate.run_records:
            timestamps.extend(
                ts
                for ts in (
                    record.finished_at,
                    record.last_heartbeat_at,
                    record.last_log_at,
                    record.started_at,
                )
                if ts is not None
            )
        for record in candidate.session_records:
            timestamps.extend(
                ts
                for ts in (record.updated_at, record.last_log_at, record.started_at)
                if ts is not None
            )
        try:
            if candidate.path.exists():
                timestamps.append(
                    datetime.fromtimestamp(candidate.path.stat().st_mtime, tz=UTC)
                )
        except OSError:
            return None
        if not timestamps:
            return None
        return max(_ensure_aware(ts) for ts in timestamps)

    def _retention_for(self, kind: ManagedRuntimeCandidateKind) -> timedelta | None:
        if kind == "artifact":
            return self._config.artifact_retention
        if kind in {"run_record", "session_record"}:
            return self._config.record_retention
        return self._config.workspace_retention

    def _docker_reference_state(self) -> DockerReferenceState:
        if self._docker_reference_provider is None:
            return DockerReferenceState()
        try:
            raw = self._docker_reference_provider()
        except Exception as exc:
            return DockerReferenceState(
                failed=True, reason=f"docker reference scan failed: {exc}"
            )
        if isinstance(raw, DockerReferenceState):
            return raw
        return DockerReferenceState(
            active_container_refs=frozenset(
                str(v) for v in raw.get("activeContainerRefs", ()) or ()
            ),
            active_mount_paths=frozenset(
                str(v) for v in raw.get("activeMountPaths", ()) or ()
            ),
            failed=bool(raw.get("failed", False)),
            reason=str(raw.get("reason") or "") or None,
        )

    def _rescan_blocks_delete(self, candidate: ManagedRuntimeCleanupCandidate) -> bool:
        try:
            current = self._build_candidates(
                tuple(self._run_store.iter_all()),
                tuple(self._session_store.iter_all()),
            )
        except (OSError, ValueError):
            return True
        docker_state = self._docker_reference_state()
        if docker_state.failed:
            return True
        for fresh in current:
            if fresh.kind == candidate.kind and fresh.path == candidate.path:
                return self._has_active_owner(fresh) or self._has_live_docker_reference(
                    fresh, docker_state
                )
        return self._has_live_docker_reference(candidate, docker_state)

    def _delete_candidate(self, candidate: ManagedRuntimeCleanupCandidate) -> None:
        if candidate.kind == "run_record":
            for record in candidate.run_records:
                self._run_store.delete(record.run_id)
            return
        if candidate.kind == "session_record":
            for record in candidate.session_records:
                self._session_store.delete(record.session_id)
            return
        if not candidate.path.exists():
            return
        quarantine = candidate.path.with_name(
            f".gc-{uuid.uuid4().hex}-{candidate.path.name}"
        )
        candidate.path.rename(quarantine)
        _delete_path(quarantine, progress_callback=self._progress_callback)

    def _emit_progress(
        self,
        phase: str,
        candidate: ManagedRuntimeCleanupCandidate,
        *,
        index: int | None = None,
        total: int | None = None,
    ) -> None:
        if self._progress_callback is None:
            return
        payload: dict[str, object] = {
            "phase": phase,
            "kind": candidate.kind,
            "path": str(candidate.path),
        }
        if index is not None:
            payload["index"] = index
        if total is not None:
            payload["total"] = total
        self._progress_callback(payload)

    def _summarize(
        self,
        decisions: Sequence[ManagedRuntimeCleanupDecision],
        *,
        scanned_run_records: int,
        scanned_session_records: int,
        errors: tuple[str, ...],
    ) -> ManagedRuntimeCleanupResult:
        all_errors = list(errors)
        all_errors.extend(
            f"{decision.path}: {decision.reason}"
            for decision in decisions
            if decision.classification == "error"
        )
        return ManagedRuntimeCleanupResult(
            disabled=False,
            dry_run=self._config.dry_run,
            scanned_run_records=scanned_run_records,
            scanned_session_records=scanned_session_records,
            scanned_workspace_roots=sum(1 for d in decisions if d.kind == "workspace"),
            scanned_artifact_dirs=sum(1 for d in decisions if d.kind == "artifact"),
            protected_roots=sum(
                1
                for d in decisions
                if d.kind == "workspace"
                and d.classification
                in {"protected_active", "protected_recent", "protected_shared"}
            ),
            eligible_roots=sum(1 for d in decisions if d.classification == "eligible"),
            deleted_roots=sum(
                1
                for d in decisions
                if d.kind == "workspace" and d.classification == "deleted"
            ),
            deleted_artifact_dirs=sum(
                1
                for d in decisions
                if d.kind == "artifact" and d.classification == "deleted"
            ),
            deleted_record_files=sum(
                1
                for d in decisions
                if d.kind in {"run_record", "session_record"}
                and d.classification == "deleted"
            ),
            estimated_deleted_bytes=sum(
                d.estimated_bytes for d in decisions if d.classification == "deleted"
            ),
            skipped_active=sum(
                1
                for d in decisions
                if d.classification in {"protected_active", "protected_shared"}
            ),
            skipped_recent=sum(
                1 for d in decisions if d.classification == "protected_recent"
            ),
            skipped_unsafe_path=sum(
                1 for d in decisions if d.classification == "skipped_unsafe_path"
            ),
            skipped_ambiguous_owner=sum(
                1 for d in decisions if d.classification == "skipped_ambiguous_owner"
            ),
            errors=tuple(all_errors),
            decisions=tuple(decisions),
        )


class _JanitorLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def __enter__(self) -> "_JanitorLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(self._fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._path.unlink()
        except FileNotFoundError:
            # Another cleanup path already removed the lock; exit remains successful.
            pass


def cleanup_managed_runtime_files(
    *,
    run_store: ManagedRunStore,
    session_store: ManagedSessionStore,
    config: ManagedRuntimeCleanupConfig | None = None,
    docker_reference_provider: DockerReferenceProvider | None = None,
    progress_callback: CleanupProgressCallback | None = None,
) -> ManagedRuntimeCleanupResult:
    """Run one managed-runtime retained cleanup pass."""
    return ManagedRuntimeWorkspaceJanitor(
        run_store=run_store,
        session_store=session_store,
        config=config,
        docker_reference_provider=docker_reference_provider,
        progress_callback=progress_callback,
    ).run()


def _env_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def _env_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_optional_int(source: Mapping[str, str], key: str) -> int | None:
    raw = source.get(key)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_optional_days(source: Mapping[str, str], key: str) -> timedelta | None:
    value = _env_optional_int(source, key)
    if value is None:
        return None
    return timedelta(days=value)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _run_artifact_refs(record: ManagedRunRecord) -> tuple[str, ...]:
    refs: list[str] = []
    for ref in (
        record.log_artifact_ref,
        record.stdout_artifact_ref,
        record.stderr_artifact_ref,
        record.merged_log_artifact_ref,
        record.diagnostics_ref,
        record.observability_events_ref,
    ):
        if ref and ref not in refs:
            refs.append(ref)
    return tuple(refs)


def _artifact_job_id(ref: str) -> str | None:
    cleaned = ref.strip().lstrip("/")
    if not cleaned:
        return None
    return cleaned.split("/", 1)[0]


def _path_size(
    path: Path,
    *,
    progress_callback: CleanupProgressCallback | None = None,
) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            if progress_callback is not None:
                progress_callback({"phase": "size_walk", "path": str(child)})
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return 0


def _delete_path(
    path: Path,
    *,
    progress_callback: CleanupProgressCallback | None = None,
) -> None:
    if not path.exists():
        return
    if path.is_file():
        if progress_callback is not None:
            progress_callback({"phase": "delete_path", "path": str(path)})
        path.unlink(missing_ok=True)
        return
    if not path.is_dir():
        path.unlink(missing_ok=True)
        return
    children = sorted(path.rglob("*"), key=lambda child: len(child.parts), reverse=True)
    for child in children:
        if progress_callback is not None:
            progress_callback({"phase": "delete_path", "path": str(child)})
        try:
            if child.is_dir() and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
    if progress_callback is not None:
        progress_callback({"phase": "delete_path", "path": str(path)})
    path.rmdir()
