"""Owner-side resolution of managed-runtime workspace locators."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from moonmind.schemas.workspace_locator_models import (
    ManagedWorkspaceLocator,
    SandboxWorkspaceLocator,
    WORKSPACE_AUTHORITY_MISMATCH,
    WORKSPACE_IDENTITY_MISMATCH,
    WorkspaceLocatorResolutionError,
)


@dataclass(frozen=True)
class SandboxWorkspaceRecord:
    """Durable owner evidence for a sandbox workspace identity."""

    workspace_id: str
    workflow_id: str
    step_execution_id: str
    relative_path: str


class SandboxWorkspaceRecordStore:
    """Filesystem-backed owner records kept outside materialized workspaces."""

    def __init__(self, workspace_root: Path) -> None:
        self._authority = (workspace_root / "temporal_sandbox").resolve()
        self.store_root = self._authority / ".workspace_records"

    def _record_path(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.json").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace record escapes its authority",
            )
        return candidate

    def _completion_marker_path(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.materialized").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace completion marker escapes its authority",
            )
        return candidate

    def is_materialized(self, workspace_id: str) -> bool:
        """Return whether workspace materialization durably completed.

        A completion marker is written only after the full clone, checkout, and
        restore-input materialization succeeded, so a retry can distinguish a
        finished workspace from a partially built directory left by a prior
        attempt that failed mid-materialization.
        """
        path = self._completion_marker_path(workspace_id)
        try:
            return path.read_text(encoding="utf-8") == "materialized-v2"
        except OSError:
            return False

    def mark_materialized(self, workspace_id: str) -> None:
        """Record durable evidence that materialization completed."""
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._completion_marker_path(workspace_id)
        if self.is_materialized(workspace_id):
            return
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("materialized-v2")

    def _readiness_marker_path(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.ready.json").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace readiness marker escapes its authority",
            )
        return candidate

    def read_readiness(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the digest-bound readiness marker, if one was recorded."""

        path = self._readiness_marker_path(workspace_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace readiness marker is invalid",
            ) from exc
        if not isinstance(payload, dict):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace readiness marker is invalid",
            )
        return payload

    def is_ready(self, workspace_id: str, fingerprint: Mapping[str, Any]) -> bool:
        """Return whether the recorded ready generation matches this attempt.

        Completion binds to the admitted source/digest, restore
        contract/version, input-manifest digest, target owner, and attempt —
        not just a directory or a previous marker. A retry reconciles the
        same generation; changed inputs require an explicit new import.
        """

        recorded = self.read_readiness(workspace_id)
        if recorded is None:
            return False
        return recorded.get("fingerprint") == dict(fingerprint)

    def mark_ready(
        self, workspace_id: str, fingerprint: Mapping[str, Any]
    ) -> None:
        """Record the verified ready generation for this source and attempt."""

        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._readiness_marker_path(workspace_id)
        payload = json.dumps(
            {"version": "ready-v1", "fingerprint": dict(fingerprint)},
            sort_keys=True,
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
        # The legacy directory-level marker is retained for historical
        # readers; authority decisions use the digest-bound marker above.
        if not self.is_materialized(workspace_id):
            self.mark_materialized(workspace_id)

    def _unsaved_marker_path(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.unsaved.json").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace unsaved marker escapes its authority",
            )
        return candidate

    def read_unsaved(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the durable unsaved-work decision for this workspace, if any."""

        path = self._unsaved_marker_path(workspace_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            datetime.fromisoformat(payload["retainUntil"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace unsaved marker is invalid",
            ) from exc
        return payload

    def mark_unsaved(
        self, workspace_id: str, *, reason_code: str, recorded_at: datetime
    ) -> dict[str, Any]:
        """Keep the only copy of work whose required save has not completed.

        The first recording fixes the bounded retention window; a repeated
        failure reports the same decision instead of extending it.
        """

        from moonmind.schemas.saved_work_retention import (
            SAVED_WORK_UNSAVED_LOCAL_RETENTION,
        )

        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.claims_locked(workspace_id):
            existing = self.read_unsaved(workspace_id)
            if existing is not None:
                return existing
            payload = {
                "version": "unsaved-v1",
                "availability": "locally_retained_but_unsaved",
                "reasonCode": str(reason_code),
                "recordedAt": recorded_at.astimezone(UTC).isoformat(),
                "retainUntil": (
                    recorded_at + SAVED_WORK_UNSAVED_LOCAL_RETENTION
                ).astimezone(UTC).isoformat(),
            }
            # Publish atomically: a torn marker would leave the decision unreadable.
            path = self._unsaved_marker_path(workspace_id)
            staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, sort_keys=True))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
            return payload

    def clear_unsaved(self, workspace_id: str) -> None:
        """Release the unsaved decision once the required save is durable."""

        self._unsaved_marker_path(workspace_id).unlink(missing_ok=True)

    def _claims_dir(self, workspace_id: str) -> Path:
        candidate = (self.store_root / f"{workspace_id}.grants").resolve()
        if candidate.parent != self.store_root.resolve():
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace grant registry escapes its authority",
            )
        return candidate

    @staticmethod
    def _grant_claim_identity(grant: Any) -> tuple[str, str]:
        """Derive a stable (claim_id, mode) pair from either grant model.

        Supports the ``workspace_sources`` grant (workspace_id/owner/generation
        based) and the HMAC grant shape (grant_id based) so both compilers
        fence exclusive use through the same registry.
        """

        mode = str(getattr(grant, "mode", "") or "").strip()
        grant_id = str(getattr(grant, "grant_id", "") or "").strip()
        if not grant_id:
            digest = str(getattr(grant, "grant_digest", "") or "").strip()
            if digest:
                grant_id = "digest_" + digest.replace(":", "_")
            else:
                workspace_id = str(
                    getattr(grant, "workspace_id", "")
                    or getattr(grant, "source_workspace_id", "")
                ).strip()
                owner = str(
                    getattr(grant, "owner_workflow_id", "")
                    or getattr(grant, "grantee_workflow_id", "")
                ).strip()
                generation = str(
                    getattr(grant, "generation", "")
                    or getattr(grant, "expected_generation", "")
                ).strip()
                grantee = str(getattr(grant, "grantee_workflow_id", "") or "").strip()
                grant_id = f"{workspace_id}:{owner}:{generation}:{grantee}"
        # The sharing mode is part of the claim identity so an exclusive
        # grant and a read-only grant never collapse onto the same claim
        # file: distinct grants must conflict, identical grants must be
        # idempotent on reclaim.
        return f"{grant_id}:{mode}", mode

    @contextmanager
    def claims_locked(self, workspace_id: str) -> Iterator[None]:
        """Serialize grant admission and workspace deletion across workers.

        Keep the lock inode outside the removable claims directory. Kernel
        locks release on process exit and also serialize threads holding
        separately opened descriptors; PID files cannot distinguish them.
        Never unlink the lock file while another worker may hold its inode.
        """
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self._claims_dir(workspace_id).with_suffix(".claims.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            for _ in range(250):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.02)
            else:
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH,
                    "existing workspace claim is contended by another execution",
                )
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _claim_is_expired(
        payload: dict[str, Any], now: datetime | None = None
    ) -> bool:
        """Return whether a recorded claim outlived its grant lifetime."""

        raw = str(payload.get("expiresAt") or "")
        if not raw:
            return False
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment <= (now or datetime.now(tz=UTC))

    def claim_existing_workspace(
        self, workspace_id: str, grant: Any, *, grantee_workflow_id: str = ""
    ) -> None:
        """Record exclusive/read-only use of another workflow's workspace.

        Existing-workspace grants declare exclusive writable use or explicitly
        supported read-only sharing. An exclusive claim conflicts with any
        other active claim; read-only claims coexist only with read-only
        claims. Reclaiming the same grant is idempotent for retries. Claims
        whose grant lifetime has expired are reaped during the scan so a
        grant that was never released cannot conflict indefinitely: every
        grant carries a bounded lifetime, and expiry is enforced here even
        when the execution lifecycle never called
        :meth:`release_existing_workspace`.

        ``grantee_workflow_id`` names the workflow using the workspace. Grants
        that do not carry their grantee need it so retention never mistakes a
        running reader's claim for the (possibly closed) owner's.
        """

        claims = self._claims_dir(workspace_id)
        grant_id, mode = self._grant_claim_identity(grant)
        if not grant_id or mode not in {"exclusive", "read_only"}:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "existing-workspace grant claim is invalid",
            )
        safe_name = "".join(
            ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
            for ch in grant_id
        )[:128] or "grant"
        with self.claims_locked(workspace_id):
            if (
                self.load(workspace_id) is None
                or not (self._authority / workspace_id).is_dir()
            ):
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH, "existing workspace is unavailable"
                )
            claims.mkdir(mode=0o700, parents=True, exist_ok=True)
            for existing_path in sorted(claims.glob("*.json")):
                if existing_path.name == f"{safe_name}.json":
                    continue
                try:
                    existing = json.loads(existing_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(existing, dict):
                    continue
                if self._claim_is_expired(existing):
                    try:
                        existing_path.unlink()
                    except OSError:
                        # Reap best-effort only: an unlinked-expired claim
                        # stays visible to this scan and conflicts below.
                        pass
                    else:
                        continue
                if mode == "exclusive" or existing.get("mode") == "exclusive":
                    raise WorkspaceLocatorResolutionError(
                        WORKSPACE_IDENTITY_MISMATCH,
                        "existing workspace is already granted to another execution",
                    )
            claim_path = claims / f"{safe_name}.json"
            expires_at = getattr(grant, "expires_at", None)
            try:
                expires_text = (
                    expires_at.isoformat()
                    if expires_at is not None and hasattr(expires_at, "isoformat")
                    else ""
                )
            except (OSError, ValueError, TypeError):
                expires_text = ""
            payload = json.dumps(
                {
                    "grantId": grant_id,
                    "mode": mode,
                    "granteeWorkflowId": str(
                        grantee_workflow_id
                        or getattr(grant, "grantee_workflow_id", "")
                        or ""
                    ),
                    "granteeIdentityVerified": True,
                    "expectedGeneration": int(
                        getattr(grant, "expected_generation", None)
                        if getattr(grant, "expected_generation", None) is not None
                        else getattr(grant, "generation", 0) or 0
                    ),
                    "expiresAt": expires_text,
                },
                sort_keys=True,
            )
            try:
                descriptor = os.open(
                    claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                return
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)

    def release_existing_workspace(self, workspace_id: str, grant_id: str) -> None:
        """Release ownership of a previously claimed existing workspace.

        ``grant_id`` is the claim identity returned as the first element of
        :meth:`_grant_claim_identity` for the granted object. The execution
        lifecycle owner calls this when the granted execution finalizes so
        the claim does not outlive its use; claims whose grant lifetime has
        expired are additionally reaped by :meth:`claim_existing_workspace`.
        """

        safe_name = "".join(
            ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
            for ch in str(grant_id or "")
        )[:128] or "grant"
        claim_path = self._claims_dir(workspace_id) / f"{safe_name}.json"
        try:
            claim_path.unlink()
        except FileNotFoundError:
            # Already released or never claimed: idempotent for retries.
            pass
        except OSError as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "existing-workspace grant release failed",
            ) from exc

    def active_claim_grantees(
        self, workspace_id: str, *, now: datetime | None = None
    ) -> tuple[str, ...]:
        """Return grantee workflow ids of unexpired existing-workspace claims.

        A claim without a recorded grantee yields ``""`` so callers cannot
        attribute it to a finished workflow.
        """

        claims = self._claims_dir(workspace_id)
        grantees: list[str] = []
        for claim_path in sorted(claims.glob("*.json")):
            try:
                payload = json.loads(claim_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                grantees.append("")
                continue
            if not isinstance(payload, dict):
                grantees.append("")
                continue
            if self._claim_is_expired(payload, now):
                continue
            # Before reader identity was passed explicitly, this field could
            # contain the owner instead. Keep historical claims protected
            # until expiry rather than mistaking a live reader for its owner.
            grantees.append(
                str(payload.get("granteeWorkflowId") or "").strip()
                if payload.get("granteeIdentityVerified") is True
                else ""
            )
        return tuple(grantees)

    def record_paths(self, workspace_id: str) -> tuple[Path, ...]:
        """Return the existing owner-side files kept for ``workspace_id``."""

        return tuple(
            path
            for path in (
                self._record_path(workspace_id),
                self._completion_marker_path(workspace_id),
                self._readiness_marker_path(workspace_id),
                self._unsaved_marker_path(workspace_id),
                self._claims_dir(workspace_id),
            )
            if path.exists()
        )

    def discard(self, workspace_id: str) -> None:
        """Remove the owner record, markers and claims of a deleted workspace."""

        for path in self.record_paths(workspace_id):
            if path.is_dir():
                for child in path.iterdir():
                    child.unlink(missing_ok=True)
                path.rmdir()
            else:
                path.unlink(missing_ok=True)

    def load(self, workspace_id: str) -> SandboxWorkspaceRecord | None:
        path = self._record_path(workspace_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return SandboxWorkspaceRecord(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "sandbox workspace owner record is invalid",
            ) from exc

    def ensure(self, record: SandboxWorkspaceRecord) -> None:
        existing = self.load(record.workspace_id)
        if existing is not None:
            if existing != record:
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH,
                    "sandbox workspace owner record does not match the current execution",
                )
            return
        self.store_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._record_path(record.workspace_id)
        payload = json.dumps(asdict(record), sort_keys=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            concurrent = self.load(record.workspace_id)
            if concurrent != record:
                raise WorkspaceLocatorResolutionError(
                    WORKSPACE_IDENTITY_MISMATCH,
                    "sandbox workspace owner record changed during persistence",
                )
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)


def resolve_sandbox_workspace_locator(
    locator: SandboxWorkspaceLocator,
    *,
    workspace_root: Path,
    expected_workspace_id: str,
    owner_record: SandboxWorkspaceRecord | None = None,
    expected_workflow_id: str | None = None,
    expected_step_execution_id: str | None = None,
    must_exist: bool = True,
) -> Path:
    """Resolve a sandbox locator at its owning worker boundary."""
    if locator.workspace_id != expected_workspace_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH,
            "sandbox locator does not match the current execution identity",
        )
    if owner_record is not None:
        if (
            owner_record.workspace_id != locator.workspace_id
            or owner_record.relative_path != locator.relative_path
            or owner_record.workflow_id != expected_workflow_id
            or owner_record.step_execution_id != expected_step_execution_id
        ):
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_IDENTITY_MISMATCH,
                "sandbox workspace owner record does not match the locator",
            )
    authority = (workspace_root / "temporal_sandbox").resolve()
    owned_root = (authority / locator.workspace_id).resolve()
    if owned_root.parent != authority:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "sandbox workspace identity escapes its authority"
        )
    workspace = (owned_root / locator.relative_path).resolve()
    if not workspace.is_relative_to(owned_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "sandbox relative path escapes its workspace"
        )
    if must_exist and not workspace.is_dir():
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "authorized sandbox workspace is unavailable"
        )
    return workspace


def workspace_volume_subpath(path: Path) -> str:
    """Return *path* as a subpath of the deployment's workspace volume.

    A remote daemon does not share the worker filesystem, and the volume the
    worker writes through is the only authority both sides name identically.
    Docker resolves the volume itself, so the launch boundary mounts
    ``type=volume,volume-subpath=<this>`` instead of translating the worker
    path into a daemon host path: the volume mountpoint a daemon reports is
    not always reachable as a bind source (Docker Desktop serves volumes from
    a store that host bind paths cannot reach), and an unreachable bind source
    is created as an empty directory rather than refused.

    :param path: Worker path under ``WORKFLOW_WORKSPACE_ROOT``.
    :returns: The POSIX-relative subpath, e.g. ``"temporal_sandbox/ws-1/repo"``.
    """

    worker_root_text = os.getenv("WORKFLOW_WORKSPACE_ROOT", "").strip()
    if not worker_root_text:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "daemon workspace mapping requires WORKFLOW_WORKSPACE_ROOT",
        )
    worker_root = Path(worker_root_text).resolve()
    resolved = path.resolve()
    if resolved == worker_root or not resolved.is_relative_to(worker_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "workspace is outside the daemon mapping authority",
        )
    subpath = resolved.relative_to(worker_root).as_posix()
    # ``,`` terminates a Docker ``--mount`` field, so a path containing one
    # would silently become a different option rather than a subpath.
    if not subpath or subpath == "." or "," in subpath:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "workspace has an invalid volume subpath",
        )
    return subpath


def daemon_visible_workspace_path(
    path: Path,
    *,
    daemon_root: Path | str | None = None,
) -> Path:
    """Translate a worker path to a daemon-visible bind path at the trusted boundary.

    The translation contract is deployment-selected and deterministic:

    - ``local`` (the default when no daemon root is configured): the Docker daemon
      shares the worker filesystem, so the worker path is already daemon-visible and
      is returned unchanged. Configuring a daemon root remap in this mode is a
      contradiction and fails closed.
    - ``remote``: the daemon runs against a distinct filesystem view, so the worker
      path is rebased from ``WORKFLOW_WORKSPACE_ROOT`` onto
      ``WORKFLOW_WORKSPACE_DAEMON_ROOT`` after a containment check. A remote
      selection without a configured daemon root cannot produce a valid bind path
      and fails closed rather than leaking a worker-only path to the daemon.

    ``WORKFLOW_DOCKER_DAEMON_MODE`` selects the contract explicitly; when unset it is
    inferred from whether a daemon root is configured, preserving the prior
    behavior. Translation only ever runs at this trusted worker/runtime boundary,
    after authorization and materialization.
    """
    worker_root_text = os.getenv("WORKFLOW_WORKSPACE_ROOT", "").strip()
    daemon_root_text = str(
        daemon_root
        if daemon_root is not None
        else os.getenv("WORKFLOW_WORKSPACE_DAEMON_ROOT", "")
    ).strip()
    resolved = path.resolve()

    mode = os.getenv("WORKFLOW_DOCKER_DAEMON_MODE", "").strip().lower()
    if not mode:
        mode = "remote" if daemon_root_text else "local"
    if mode not in {"local", "remote"}:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "WORKFLOW_DOCKER_DAEMON_MODE must be 'local' or 'remote'",
        )

    if mode == "local":
        if daemon_root_text:
            raise WorkspaceLocatorResolutionError(
                WORKSPACE_AUTHORITY_MISMATCH,
                "local daemon mode must not configure a daemon root remap",
            )
        return resolved

    # Remote daemon translation contract.
    if not daemon_root_text:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "remote daemon mode requires WORKFLOW_WORKSPACE_DAEMON_ROOT",
        )
    if not worker_root_text:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH,
            "daemon workspace mapping requires WORKFLOW_WORKSPACE_ROOT",
        )
    worker_root = Path(worker_root_text).resolve()
    if not resolved.is_relative_to(worker_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "workspace is outside the daemon mapping authority"
        )
    return Path(daemon_root_text).resolve() / resolved.relative_to(worker_root)


class ManagedRunRecord(Protocol):
    run_id: str
    runtime_id: str
    workspace_path: str


class ManagedRunRecordStore(Protocol):
    store_root: Path

    def load(self, run_id: str) -> ManagedRunRecord | None:
        """Load the managed run record identified by ``run_id``."""


def resolve_managed_workspace_locator(
    locator: ManagedWorkspaceLocator,
    *,
    store: ManagedRunRecordStore,
    current_agent_run_id: str,
    current_runtime_id: str,
) -> Path:
    """Resolve a locator only after caller, record, and filesystem authority agree."""
    if locator.agent_run_id != current_agent_run_id or locator.runtime_id != current_runtime_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH, "managed locator does not match the current run identity"
        )
    record = store.load(locator.agent_run_id)
    if record is None:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH, "managed run record was not found"
        )
    if record.run_id != locator.agent_run_id or record.runtime_id != locator.runtime_id:
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_IDENTITY_MISMATCH, "managed run record does not match the locator"
        )
    workspace_root = Path(record.workspace_path).resolve()
    store_authority = store.store_root.resolve().parent
    if not workspace_root.is_relative_to(store_authority):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "managed workspace is outside the configured store"
        )
    workspace = (
        workspace_root
        if locator.relative_path == "."
        or (locator.relative_path == "repo" and workspace_root.name == "repo")
        else (workspace_root / locator.relative_path).resolve()
    )
    if not workspace.is_relative_to(workspace_root):
        raise WorkspaceLocatorResolutionError(
            WORKSPACE_AUTHORITY_MISMATCH, "managed relative path escapes its workspace"
        )
    return workspace
