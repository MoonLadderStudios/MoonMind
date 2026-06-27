"""Retained-state cleanup for managed runtime workspaces and artifacts."""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal

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

logger = logging.getLogger(__name__)

ResourceClass = Literal["workspace_root", "artifact_dir", "run_record", "session_record"]

_DEFAULT_AGENT_RUNTIME_STORE = "/work/agent_jobs"


@dataclass(frozen=True)
class ManagedRuntimeCleanupCandidate:
    resource_class: ResourceClass
    safe_path: str
    classification: str
    reason: str
    estimated_bytes: int = 0


@dataclass(frozen=True)
class ManagedRuntimeCleanupResult:
    disabled: bool
    dry_run: bool
    scanned_run_records: int = 0
    scanned_session_records: int = 0
    scanned_workspace_roots: int = 0
    scanned_artifact_dirs: int = 0
    scanned_record_files: int = 0
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
    delete_budget_exhausted: int = 0
    errors: tuple[str, ...] = ()
    metrics: dict[str, int] = field(default_factory=dict)
    candidate_samples: tuple[ManagedRuntimeCleanupCandidate, ...] = ()
    deleted_samples: tuple[ManagedRuntimeCleanupCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["candidateSamples"] = data.pop("candidate_samples")
        data["deletedSamples"] = data.pop("deleted_samples")
        data["scannedRunRecords"] = data.pop("scanned_run_records")
        data["scannedSessionRecords"] = data.pop("scanned_session_records")
        data["scannedWorkspaceRoots"] = data.pop("scanned_workspace_roots")
        data["scannedArtifactDirs"] = data.pop("scanned_artifact_dirs")
        data["scannedRecordFiles"] = data.pop("scanned_record_files")
        data["protectedRoots"] = data.pop("protected_roots")
        data["eligibleRoots"] = data.pop("eligible_roots")
        data["deletedRoots"] = data.pop("deleted_roots")
        data["deletedArtifactDirs"] = data.pop("deleted_artifact_dirs")
        data["deletedRecordFiles"] = data.pop("deleted_record_files")
        data["estimatedDeletedBytes"] = data.pop("estimated_deleted_bytes")
        data["skippedActive"] = data.pop("skipped_active")
        data["skippedRecent"] = data.pop("skipped_recent")
        data["skippedUnsafePath"] = data.pop("skipped_unsafe_path")
        data["skippedAmbiguousOwner"] = data.pop("skipped_ambiguous_owner")
        data["deleteBudgetExhausted"] = data.pop("delete_budget_exhausted")
        return data


@dataclass(frozen=True)
class _Candidate:
    resource_class: ResourceClass
    path: Path
    owners: tuple[ManagedRunRecord | CodexManagedSessionRecord, ...] = ()
    record_id: str | None = None


@dataclass
class _Counters:
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
    delete_budget_exhausted: int = 0


def cleanup_managed_runtime_files(
    *,
    run_store: ManagedRunStore,
    session_store: ManagedSessionStore,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
    sample_limit: int = 20,
) -> ManagedRuntimeCleanupResult:
    """Classify and optionally remove old retained managed-runtime state."""

    environ = os.environ if env is None else env
    enabled = _env_bool(environ, "MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED", False)
    dry_run = _env_bool(environ, "MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN", True)
    now = now or datetime.now(tz=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if not enabled:
        result = ManagedRuntimeCleanupResult(disabled=True, dry_run=dry_run)
        _log_pass(result)
        return result

    store_root = Path(
        environ.get("MOONMIND_AGENT_RUNTIME_STORE", _DEFAULT_AGENT_RUNTIME_STORE)
        or _DEFAULT_AGENT_RUNTIME_STORE
    ).resolve()
    artifact_root = managed_runtime_artifact_root(environ).resolve()
    workspace_retention = _env_timedelta(
        environ, "MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS", 30
    )
    artifact_retention = _env_timedelta(
        environ, "MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS", 90
    )
    record_retention = _optional_env_timedelta(
        environ, "MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS"
    )
    grace = timedelta(
        seconds=max(
            0,
            _env_int(environ, "MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS", 3600),
        )
    )
    max_delete_paths = max(
        0, _env_int(environ, "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS", 25)
    )
    max_delete_bytes = _optional_env_int(
        environ, "MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES"
    )

    errors: list[str] = []
    try:
        run_records = list(run_store.iter_all())
    except Exception as exc:
        safe = _safe_path(run_store.store_root, roots=((store_root, "store"),))
        errors.append(f"{safe}: {type(exc).__name__}")
        result = ManagedRuntimeCleanupResult(
            disabled=False,
            dry_run=dry_run,
            errors=tuple(errors),
            metrics={"resource.store.error": 1},
        )
        _log_pass(result)
        return result
    try:
        session_records = list(session_store.iter_all())
    except Exception as exc:
        safe = _safe_path(session_store.store_root, roots=((store_root, "store"),))
        errors.append(f"{safe}: {type(exc).__name__}")
        result = ManagedRuntimeCleanupResult(
            disabled=False,
            dry_run=dry_run,
            scanned_run_records=len(run_records),
            errors=tuple(errors),
            metrics={"resource.store.error": 1},
        )
        _log_pass(result)
        return result

    candidates = _discover_candidates(
        store_root=store_root,
        artifact_root=artifact_root,
        run_store=run_store,
        session_store=session_store,
        run_records=run_records,
        session_records=session_records,
    )
    counters = _Counters()
    samples: list[ManagedRuntimeCleanupCandidate] = []
    deleted_samples: list[ManagedRuntimeCleanupCandidate] = []
    metrics: dict[str, int] = {}
    deleted_paths = 0
    deleted_bytes = 0
    dry_run_estimated_bytes = 0
    scanned_workspace_roots = 0
    scanned_artifact_dirs = 0
    scanned_record_files = 0

    for candidate in candidates:
        if candidate.resource_class == "workspace_root":
            scanned_workspace_roots += 1
        elif candidate.resource_class == "artifact_dir":
            scanned_artifact_dirs += 1
        else:
            scanned_record_files += 1
        classification, reason = _classify_candidate(
            candidate,
            store_root=store_root,
            artifact_root=artifact_root,
            now=now,
            workspace_retention=workspace_retention,
            artifact_retention=artifact_retention,
            record_retention=record_retention,
            grace=grace,
        )
        estimated_bytes = _estimate_size(candidate.path)
        safe_path = _safe_path(
            candidate.path,
            roots=((store_root, "store"), (artifact_root, "artifacts")),
        )
        if classification == "protected_active":
            counters.protected_roots += 1
            counters.skipped_active += 1
        elif classification in {"protected_recent", "protected_shared"}:
            counters.protected_roots += 1
            counters.skipped_recent += 1
        elif classification == "skipped_unsafe_path":
            counters.skipped_unsafe_path += 1
        elif classification == "skipped_ambiguous_owner":
            counters.skipped_ambiguous_owner += 1
        elif classification == "eligible":
            counters.eligible_roots += 1
            dry_run_estimated_bytes += estimated_bytes

        sample = ManagedRuntimeCleanupCandidate(
            resource_class=candidate.resource_class,
            safe_path=safe_path,
            classification=classification,
            reason=reason,
            estimated_bytes=estimated_bytes,
        )
        _count_metric(metrics, candidate.resource_class, classification)
        if len(samples) < sample_limit:
            samples.append(sample)

        if classification != "eligible" or dry_run:
            continue
        if deleted_paths >= max_delete_paths or (
            max_delete_bytes is not None
            and deleted_bytes + estimated_bytes > max_delete_bytes
        ):
            counters.delete_budget_exhausted += 1
            _count_metric(metrics, candidate.resource_class, "budget_exhausted")
            continue
        try:
            _delete_candidate(candidate, run_store=run_store, session_store=session_store)
        except Exception as exc:
            errors.append(f"{safe_path}: {type(exc).__name__}")
            _count_metric(metrics, candidate.resource_class, "error")
            continue
        deleted_paths += 1
        deleted_bytes += estimated_bytes
        counters.estimated_deleted_bytes += estimated_bytes
        deleted_sample = ManagedRuntimeCleanupCandidate(
            resource_class=candidate.resource_class,
            safe_path=safe_path,
            classification="deleted",
            reason="deleted after retained-state safety gates passed",
            estimated_bytes=estimated_bytes,
        )
        if candidate.resource_class == "artifact_dir":
            counters.deleted_artifact_dirs += 1
        elif candidate.resource_class in {"run_record", "session_record"}:
            counters.deleted_record_files += 1
        else:
            counters.deleted_roots += 1
        if len(deleted_samples) < sample_limit:
            deleted_samples.append(deleted_sample)
        _count_metric(metrics, candidate.resource_class, "deleted")

    if dry_run:
        counters.estimated_deleted_bytes = dry_run_estimated_bytes

    result = ManagedRuntimeCleanupResult(
        disabled=False,
        dry_run=dry_run,
        scanned_run_records=len(run_records),
        scanned_session_records=len(session_records),
        scanned_workspace_roots=scanned_workspace_roots,
        scanned_artifact_dirs=scanned_artifact_dirs,
        scanned_record_files=scanned_record_files,
        protected_roots=counters.protected_roots,
        eligible_roots=counters.eligible_roots,
        deleted_roots=counters.deleted_roots,
        deleted_artifact_dirs=counters.deleted_artifact_dirs,
        deleted_record_files=counters.deleted_record_files,
        estimated_deleted_bytes=counters.estimated_deleted_bytes,
        skipped_active=counters.skipped_active,
        skipped_recent=counters.skipped_recent,
        skipped_unsafe_path=counters.skipped_unsafe_path,
        skipped_ambiguous_owner=counters.skipped_ambiguous_owner,
        delete_budget_exhausted=counters.delete_budget_exhausted,
        errors=tuple(errors),
        metrics=metrics,
        candidate_samples=tuple(samples),
        deleted_samples=tuple(deleted_samples),
    )
    _log_pass(result)
    return result


def _discover_candidates(
    *,
    store_root: Path,
    artifact_root: Path,
    run_store: ManagedRunStore,
    session_store: ManagedSessionStore,
    run_records: Iterable[ManagedRunRecord],
    session_records: Iterable[CodexManagedSessionRecord],
) -> list[_Candidate]:
    by_root: dict[Path, list[ManagedRunRecord | CodexManagedSessionRecord]] = {}
    run_records = list(run_records)
    session_records = list(session_records)
    for record in run_records:
        root = _ownership_root(record.workspace_path, store_root=store_root)
        if root is not None:
            by_root.setdefault(root, []).append(record)
    for record in session_records:
        for raw_path in (record.workspace_path, record.session_workspace_path):
            root = _ownership_root(raw_path, store_root=store_root)
            if root is not None:
                by_root.setdefault(root, []).append(record)

    candidates: list[_Candidate] = []
    for root, owners in sorted(by_root.items(), key=lambda item: str(item[0])):
        if root.exists():
            candidates.append(
                _Candidate(
                    resource_class="workspace_root",
                    path=root,
                    owners=tuple(owners),
                )
            )
    if artifact_root.exists():
        for path in sorted(child for child in artifact_root.iterdir() if child.is_dir()):
            owners = tuple(
                record
                for record in (*run_records, *session_records)
                if _record_references_artifact(record, path)
            )
            candidates.append(
                _Candidate(resource_class="artifact_dir", path=path, owners=owners)
            )
    for record in run_records:
        path = run_store._resolve_path(record.run_id)
        if path.exists():
            candidates.append(
                _Candidate(
                    resource_class="run_record",
                    path=path,
                    owners=(record,),
                    record_id=record.run_id,
                )
            )
    for record in session_records:
        path = session_store._resolve_path(record.session_id)
        if path.exists():
            candidates.append(
                _Candidate(
                    resource_class="session_record",
                    path=path,
                    owners=(record,),
                    record_id=record.session_id,
                )
            )
    return candidates


def _classify_candidate(
    candidate: _Candidate,
    *,
    store_root: Path,
    artifact_root: Path,
    now: datetime,
    workspace_retention: timedelta,
    artifact_retention: timedelta,
    record_retention: timedelta | None,
    grace: timedelta,
) -> tuple[str, str]:
    if not _is_safe_candidate_path(
        candidate.path, candidate.resource_class, store_root, artifact_root
    ):
        return ("skipped_unsafe_path", "path is outside canonical roots or is a symlink")
    if (
        candidate.resource_class in {"run_record", "session_record"}
        and record_retention is None
    ):
        return ("protected_recent", "record retention is disabled")
    if not candidate.owners:
        return ("skipped_ambiguous_owner", "no durable owner record maps to this path")
    if any(_owner_active(owner) for owner in candidate.owners):
        return ("protected_active", "at least one owner is active or has activeTurnId")
    newest = _newest_activity(candidate.owners, candidate.path)
    if newest is None:
        return ("protected_recent", "no reliable owner or filesystem timestamp")
    retention = workspace_retention
    if candidate.resource_class == "artifact_dir":
        retention = artifact_retention
    elif candidate.resource_class in {"run_record", "session_record"}:
        retention = record_retention or timedelta.max
    if now - newest < retention + grace:
        return ("protected_recent", "retention or grace window has not elapsed")
    return ("eligible", "all retained-state safety gates passed")


def _delete_candidate(
    candidate: _Candidate,
    *,
    run_store: ManagedRunStore,
    session_store: ManagedSessionStore,
) -> None:
    if candidate.resource_class == "run_record" and candidate.record_id:
        run_store.delete(candidate.record_id)
        return
    if candidate.resource_class == "session_record" and candidate.record_id:
        session_store.delete(candidate.record_id)
        return
    quarantine = candidate.path.with_name(
        f".gc-{uuid.uuid4().hex}-{candidate.path.name}"
    )
    candidate.path.rename(quarantine)
    if quarantine.is_dir():
        shutil.rmtree(quarantine)
    else:
        quarantine.unlink(missing_ok=True)


def _ownership_root(raw_path: str | None, *, store_root: Path) -> Path | None:
    if not raw_path:
        return None
    try:
        path = Path(raw_path).resolve()
    except OSError:
        return None
    workspaces = store_root / "workspaces"
    if path.is_relative_to(workspaces):
        relative = path.relative_to(workspaces)
        if relative.parts:
            return workspaces / relative.parts[0]
    if path.is_relative_to(store_root):
        relative = path.relative_to(store_root)
        if relative.parts and relative.parts[0] not in {
            "artifacts",
            "managed_runs",
            "managed_sessions",
            "workspaces",
        }:
            return store_root / relative.parts[0]
    return None


def _record_references_artifact(
    record: ManagedRunRecord | CodexManagedSessionRecord,
    artifact_dir: Path,
) -> bool:
    prefix = f"{artifact_dir.name}/"
    refs = [
        getattr(record, "stdout_artifact_ref", None),
        getattr(record, "stderr_artifact_ref", None),
        getattr(record, "merged_log_artifact_ref", None),
        getattr(record, "diagnostics_ref", None),
        getattr(record, "observability_events_ref", None),
        getattr(record, "latest_summary_ref", None),
        getattr(record, "latest_checkpoint_ref", None),
        getattr(record, "latest_control_event_ref", None),
        getattr(record, "latest_reset_boundary_ref", None),
    ]
    return any(str(ref or "").startswith(prefix) for ref in refs)


def _owner_active(owner: ManagedRunRecord | CodexManagedSessionRecord) -> bool:
    if getattr(owner, "active_turn_id", None):
        return True
    status = str(getattr(owner, "status", "")).lower()
    if isinstance(owner, ManagedRunRecord):
        return status not in TERMINAL_AGENT_RUN_STATES
    return status not in TERMINAL_MANAGED_SESSION_STATUSES


def _newest_activity(
    owners: tuple[ManagedRunRecord | CodexManagedSessionRecord, ...],
    path: Path,
) -> datetime | None:
    values: list[datetime] = []
    for owner in owners:
        for field_name in (
            "finished_at",
            "last_heartbeat_at",
            "last_log_at",
            "updated_at",
            "started_at",
        ):
            value = getattr(owner, field_name, None)
            if isinstance(value, datetime):
                values.append(value if value.tzinfo else value.replace(tzinfo=UTC))
    try:
        values.append(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
    except OSError:
        pass
    return max(values) if values else None


def _is_safe_candidate_path(
    path: Path,
    resource_class: ResourceClass,
    store_root: Path,
    artifact_root: Path,
) -> bool:
    try:
        if path.is_symlink():
            return False
        resolved = path.resolve()
    except OSError:
        return False
    if resource_class == "workspace_root":
        workspaces_root = store_root / "workspaces"
        return (
            resolved.parent == workspaces_root
            or (
                resolved.parent == store_root
                and resolved.name
                not in {"artifacts", "managed_runs", "managed_sessions", "workspaces"}
            )
        )
    if resource_class == "artifact_dir":
        return resolved.parent == artifact_root
    if resource_class == "run_record":
        return resolved.parent == store_root / "managed_runs"
    if resource_class == "session_record":
        return resolved.parent == store_root / "managed_sessions"
    return False


def _estimate_size(path: Path) -> int:
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


def _safe_path(path: Path, *, roots: tuple[tuple[Path, str], ...]) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    for root, label in roots:
        try:
            return f"{label}:{resolved.relative_to(root.resolve()).as_posix()}"
        except ValueError:
            continue
    return f"path:{resolved.name}"


def _count_metric(
    metrics: dict[str, int], resource_class: str, classification: str
) -> None:
    key = f"resource.{resource_class}.{classification}"
    metrics[key] = metrics.get(key, 0) + 1


def _log_pass(result: ManagedRuntimeCleanupResult) -> None:
    logger.info(
        "managed_runtime_cleanup_pass",
        extra={"managed_runtime_cleanup": result.to_dict()},
    )


def _env_bool(environ: dict[str, str], key: str, default: bool) -> bool:
    raw = str(environ.get(key, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(environ: dict[str, str], key: str, default: int) -> int:
    raw = str(environ.get(key, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _optional_env_int(environ: dict[str, str], key: str) -> int | None:
    raw = str(environ.get(key, "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env_timedelta(environ: dict[str, str], key: str, default_days: int) -> timedelta:
    return timedelta(days=max(0, _env_int(environ, key, default_days)))


def _optional_env_timedelta(environ: dict[str, str], key: str) -> timedelta | None:
    value = _optional_env_int(environ, key)
    if value is None:
        return None
    return timedelta(days=max(0, value))
