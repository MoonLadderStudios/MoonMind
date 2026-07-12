"""Retained-state cleanup for managed-runtime local files."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from moonmind.schemas.agent_runtime_models import (
    TERMINAL_AGENT_RUN_STATES,
    ManagedRunRecord,
)
from moonmind.workflows.temporal.runtime.managed_session_store import (
    TERMINAL_MANAGED_SESSION_STATUSES,
    ManagedSessionStore,
)
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.runtime.store import ManagedRunStore


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return max(0, int(raw))


def _optional_int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return max(0, int(raw))


@dataclass(frozen=True)
class ManagedRuntimeCleanupConfig:
    enabled: bool
    dry_run: bool
    workspace_retention: timedelta
    artifact_retention: timedelta
    record_retention: timedelta | None
    grace: timedelta
    max_delete_paths: int
    store_root: Path
    artifact_root: Path

    @classmethod
    def from_env(cls) -> "ManagedRuntimeCleanupConfig":
        store_root = Path(
            os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs")
        ).resolve()
        return cls(
            enabled=_bool_env("MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", True),
            dry_run=_bool_env("MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", False),
            workspace_retention=timedelta(
                days=_int_env("MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS", 30)
            ),
            artifact_retention=timedelta(
                days=_int_env("MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS", 90)
            ),
            record_retention=(
                timedelta(days=record_days)
                if (
                    record_days := _optional_int_env(
                        "MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS"
                    )
                )
                is not None
                else None
            ),
            grace=timedelta(
                seconds=_int_env("MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS", 3600)
            ),
            max_delete_paths=_int_env(
                "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS", 100
            ),
            store_root=store_root,
            artifact_root=managed_runtime_artifact_root().resolve(),
        )


@dataclass
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
    skipped_active: int = 0
    skipped_recent: int = 0
    skipped_unsafe_path: int = 0
    skipped_ambiguous_owner: int = 0
    errors: tuple[str, ...] = ()

    def add_error(self, message: str) -> None:
        self.errors = (*self.errors, message)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ManagedRuntimeWorkspaceJanitor:
    """Scan and optionally delete retained managed-runtime state."""

    def __init__(
        self,
        config: ManagedRuntimeCleanupConfig | None = None,
        *,
        run_store: ManagedRunStore | None = None,
        session_store: ManagedSessionStore | None = None,
    ) -> None:
        self._config = config or ManagedRuntimeCleanupConfig.from_env()
        self._run_store = run_store or ManagedRunStore(
            self._config.store_root / "managed_runs"
        )
        self._session_store = session_store or ManagedSessionStore(
            self._config.store_root / "managed_sessions"
        )

    def run(self) -> ManagedRuntimeCleanupResult:
        cfg = self._config
        result = ManagedRuntimeCleanupResult(
            disabled=not cfg.enabled,
            dry_run=cfg.dry_run,
        )
        if not cfg.enabled:
            return result

        now = datetime.now(UTC)
        try:
            run_records = list(self._run_store.iter_all())
            session_records = list(self._session_store.iter_all())
        except Exception as exc:
            result.add_error(f"store_read_failed:{type(exc).__name__}")
            return result

        result.scanned_run_records = len(run_records)
        result.scanned_session_records = len(session_records)

        owner_records = self._records_by_workspace_root(run_records, session_records)
        artifact_refs = self._referenced_artifact_dir_names(run_records, session_records)

        for path in self._workspace_candidates():
            result.scanned_workspace_roots += 1
            self._classify_workspace_candidate(
                path,
                owner_records.get(path.resolve(), ()),
                now,
                result,
            )

        for path in self._artifact_candidates():
            result.scanned_artifact_dirs += 1
            if path.name in artifact_refs:
                result.protected_roots += 1
                continue
            self._classify_filesystem_candidate(
                path,
                now,
                cfg.artifact_retention,
                result,
                artifact=True,
            )

        if cfg.record_retention is not None:
            self._delete_old_records(run_records, session_records, now, result)

        return result

    def _workspace_candidates(self) -> list[Path]:
        cfg = self._config
        roots: list[Path] = []
        workspaces_root = cfg.store_root / "workspaces"
        if workspaces_root.exists():
            roots.extend(path for path in workspaces_root.iterdir() if path.is_dir())
        if cfg.store_root.exists():
            reserved = {"artifacts", "managed_runs", "managed_sessions", "workspaces"}
            roots.extend(
                path
                for path in cfg.store_root.iterdir()
                if path.is_dir()
                and path.name not in reserved
                and not path.name.startswith(".")
            )
        return sorted({path.resolve() for path in roots})

    def _artifact_candidates(self) -> list[Path]:
        artifact_root = self._config.artifact_root
        if not artifact_root.exists():
            return []
        return sorted(path.resolve() for path in artifact_root.iterdir() if path.is_dir())

    def _records_by_workspace_root(
        self,
        run_records: list[ManagedRunRecord],
        session_records: list[object],
    ) -> dict[Path, tuple[object, ...]]:
        grouped: dict[Path, list[object]] = {}
        for record in run_records:
            if record.workspace_path:
                root = self._ownership_root(Path(record.workspace_path))
                if root is not None:
                    grouped.setdefault(root, []).append(record)
        for record in session_records:
            for raw_path in (
                getattr(record, "workspace_path", None),
                getattr(record, "session_workspace_path", None),
            ):
                if raw_path:
                    root = self._ownership_root(Path(str(raw_path)))
                    if root is not None:
                        grouped.setdefault(root, []).append(record)
        return {root: tuple(records) for root, records in grouped.items()}

    def _ownership_root(self, path: Path) -> Path | None:
        cfg = self._config
        try:
            resolved = path.resolve()
            workspaces_root = (cfg.store_root / "workspaces").resolve()
            if resolved.is_relative_to(workspaces_root):
                relative = resolved.relative_to(workspaces_root)
                if relative.parts:
                    return (workspaces_root / relative.parts[0]).resolve()
            if resolved.is_relative_to(cfg.store_root):
                relative = resolved.relative_to(cfg.store_root)
                if relative.parts and relative.parts[0] not in {
                    "artifacts",
                    "managed_runs",
                    "managed_sessions",
                    "workspaces",
                }:
                    return (cfg.store_root / relative.parts[0]).resolve()
        except (OSError, ValueError):
            return None
        return None

    def _referenced_artifact_dir_names(
        self,
        run_records: list[ManagedRunRecord],
        session_records: list[object],
    ) -> set[str]:
        names: set[str] = set()
        for record in (*run_records, *session_records):
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
                if isinstance(value, str) and value.strip():
                    names.add(Path(value).parts[0])
        return names

    def _classify_workspace_candidate(
        self,
        path: Path,
        owners: tuple[object, ...],
        now: datetime,
        result: ManagedRuntimeCleanupResult,
    ) -> None:
        if not owners:
            result.skipped_ambiguous_owner += 1
            return
        if any(self._record_is_active(owner) for owner in owners):
            result.protected_roots += 1
            result.skipped_active += 1
            return
        newest = max(self._record_activity_time(owner) for owner in owners)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            newest = max(newest, mtime)
        except OSError:
            result.add_error(f"stat_failed:{path.name}")
            return
        if now - newest < max(self._config.workspace_retention, self._config.grace):
            result.protected_roots += 1
            result.skipped_recent += 1
            return
        self._delete_candidate(path, result, artifact=False)

    def _classify_filesystem_candidate(
        self,
        path: Path,
        now: datetime,
        retention: timedelta,
        result: ManagedRuntimeCleanupResult,
        *,
        artifact: bool,
    ) -> None:
        if not self._path_allowed(path, artifact=artifact):
            result.skipped_unsafe_path += 1
            return
        if path.is_symlink():
            result.skipped_unsafe_path += 1
            return
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        except OSError:
            result.add_error(f"stat_failed:{path.name}")
            return
        if now - mtime < max(retention, self._config.grace):
            result.protected_roots += 1
            result.skipped_recent += 1
            return
        self._delete_candidate(path, result, artifact=artifact)

    def _delete_candidate(
        self,
        path: Path,
        result: ManagedRuntimeCleanupResult,
        *,
        artifact: bool,
    ) -> None:
        if not self._path_allowed(path, artifact=artifact) or path.is_symlink():
            result.skipped_unsafe_path += 1
            return
        result.eligible_roots += 1
        result.estimated_deleted_bytes += self._estimate_size(path)
        if (
            result.deleted_roots + result.deleted_artifact_dirs
            >= self._config.max_delete_paths
        ):
            result.skipped_recent += 1
            return
        if self._config.dry_run:
            return
        quarantine = path.with_name(f".gc-{uuid.uuid4().hex}-{path.name}")
        try:
            path.rename(quarantine)
            shutil.rmtree(quarantine)
        except Exception as exc:
            result.add_error(f"delete_failed:{path.name}:{type(exc).__name__}")
            return
        if artifact:
            result.deleted_artifact_dirs += 1
        else:
            result.deleted_roots += 1

    def _delete_old_records(
        self,
        run_records: list[ManagedRunRecord],
        session_records: list[object],
        now: datetime,
        result: ManagedRuntimeCleanupResult,
    ) -> None:
        assert self._config.record_retention is not None
        cutoff = max(self._config.record_retention, self._config.grace)
        if self._config.dry_run:
            return
        for record in run_records:
            if (
                self._record_is_active(record)
                or now - self._record_activity_time(record) < cutoff
            ):
                continue
            self._run_store.delete(record.run_id)
            result.deleted_record_files += 1
        for record in session_records:
            if (
                self._record_is_active(record)
                or now - self._record_activity_time(record) < cutoff
            ):
                continue
            self._session_store.delete(str(getattr(record, "session_id")))
            result.deleted_record_files += 1

    def _path_allowed(self, path: Path, *, artifact: bool) -> bool:
        try:
            resolved = path.resolve()
            if artifact:
                return resolved.parent == self._config.artifact_root
            workspaces_root = (self._config.store_root / "workspaces").resolve()
            if resolved.parent == workspaces_root:
                return True
            return resolved.parent == self._config.store_root and resolved.name not in {
                "artifacts",
                "managed_runs",
                "managed_sessions",
                "workspaces",
            }
        except OSError:
            return False

    def _record_is_active(self, record: object) -> bool:
        if getattr(record, "active_turn_id", None):
            return True
        status = str(getattr(record, "status", "")).lower()
        if isinstance(record, ManagedRunRecord):
            return status not in TERMINAL_AGENT_RUN_STATES
        return status not in TERMINAL_MANAGED_SESSION_STATUSES

    def _record_activity_time(self, record: object) -> datetime:
        for attr in (
            "finished_at",
            "last_heartbeat_at",
            "last_log_at",
            "updated_at",
            "started_at",
        ):
            value = getattr(record, attr, None)
            if isinstance(value, datetime):
                return value if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.now(UTC)

    def _estimate_size(self, path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    total += child.stat().st_size
            except OSError:
                continue
        return total
