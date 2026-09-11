"""Worker code identity: detect bind-mounted workers running stale modules.

Bind-mounted source plus long-lived workers means every host ``git pull``
silently creates a mixed-version deployment until someone restarts containers
(MoonLadderStudios/MoonMind#4224). This module gives every worker a
startup-recorded code identity (git revision or a content digest of the
imported ``moonmind`` package) and the comparison/probe/recovery helpers used
by the worker health projection, the API ``/healthz`` detail, the ``moonmind``
CLI, the deployment-control worker, and the UserWorkflow admission gate.

Uses only the Python standard library so host update scripts and thin worker
entrypoints can import it without the application dependency set.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

UNKNOWN = "unknown"
STALE_CODE_REASON = "stale_code"

_CODE_REVISION_ENV_KEYS = ("MOONMIND_BUILD_SHA", "MOONMIND_IMAGE_DIGEST")
_PACKAGE_ROOT_ENV_KEY = "MOONMIND_CODE_PACKAGE_ROOT"
_READINESS_URLS_ENV_KEY = "MOONMIND_WORKER_READINESS_URLS"
_WORKFLOW_READINESS_URL_ENV_KEY = "TEMPORAL_WORKFLOW_READINESS_URL"

_MAX_DIGEST_FILES = 10000
_DIGEST_CACHE_TTL_SECONDS = 60.0

_digest_cache: dict[str, tuple[float, str | None]] = {}
_STARTUP_IDENTITY: "WorkerCodeIdentity | None" = None


@dataclass(frozen=True, slots=True)
class WorkerCodeIdentity:
    """Code identity recorded for one worker process or checkout."""

    revision: str | None = None
    digest: str | None = None
    source: str = UNKNOWN

    def to_payload(self) -> dict[str, Any]:
        return {
            "codeRevision": self.revision or UNKNOWN,
            "codeDigest": self.digest or UNKNOWN,
            "codeIdentitySource": self.source,
        }

    @property
    def known(self) -> bool:
        return bool((self.revision or "").strip() or (self.digest or "").strip())


def _package_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    override = os.environ.get(_PACKAGE_ROOT_ENV_KEY, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent


def _repo_root_for(package_root: Path) -> Path:
    return package_root.parent


def _run_git(args: Sequence[str], *, cwd: Path, timeout: float = 5.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def resolve_git_revision(
    repo_root: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[str | None, str]:
    """Return ``(revision, source)`` for the checkout on disk.

    Prefers the explicit ``MOONMIND_BUILD_SHA`` / ``MOONMIND_IMAGE_DIGEST``
    environment identity, then ``git rev-parse HEAD``. A dirty working tree
    (bind-mounted file modified without a commit) is part of the identity:
    the revision carries a ``-dirty`` suffix so a startup-recorded clean
    revision compares stale against it.
    """

    env = os.environ if environ is None else environ
    for key in _CODE_REVISION_ENV_KEYS:
        raw = str(env.get(key) or "").strip()
        if raw:
            base = raw.removeprefix("sha256:")
            root = _repo_root_for(_package_root())
            if repo_root is not None:
                root = Path(repo_root)
            dirty = _run_git(
                ["status", "--porcelain", "--untracked-files=no"],
                cwd=root,
                timeout=timeout,
            )
            if dirty:
                return f"{base}-dirty", key
            return base, key
    root = _repo_root_for(_package_root())
    if repo_root is not None:
        root = Path(repo_root)
    head = _run_git(["rev-parse", "HEAD"], cwd=root, timeout=timeout)
    if head is None:
        return None, UNKNOWN
    dirty = _run_git(
        ["status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        timeout=timeout,
    )
    if dirty:
        return f"{head}-dirty", "git-dirty"
    return head, "git"


def compute_package_digest(package_root: str | Path | None = None) -> str | None:
    """Return a sha256 digest over the ``moonmind`` package ``*.py`` sources."""

    root = _package_root(package_root)
    if not root.is_dir():
        return None
    hasher = hashlib.sha256()
    try:
        candidates = sorted(
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    except OSError:
        return None
    if not candidates:
        return None
    count = 0
    for path in candidates:
        if count >= _MAX_DIGEST_FILES:
            break
        try:
            relative = path.relative_to(root).as_posix()
            content = path.read_bytes()
        except OSError:
            continue
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(content)
        hasher.update(b"\x00")
        count += 1
    if count == 0:
        return None
    return "sha256:" + hasher.hexdigest()


def _cached_package_digest(package_root: str | Path | None = None) -> str | None:
    key = str(_package_root(package_root))
    now = time.monotonic()
    cached = _digest_cache.get(key)
    if cached is not None and now - cached[0] < _DIGEST_CACHE_TTL_SECONDS:
        return cached[1]
    digest = compute_package_digest(package_root)
    _digest_cache[key] = (now, digest)
    return digest


def resolve_worker_code_identity(
    package_root: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> WorkerCodeIdentity:
    """Resolve the code identity a worker records once at startup."""

    revision, source = resolve_git_revision(environ=environ)
    digest = compute_package_digest(package_root)
    if revision is None and digest is None:
        return WorkerCodeIdentity(revision=None, digest=None, source=UNKNOWN)
    if revision is None:
        return WorkerCodeIdentity(revision=None, digest=digest, source="package-digest")
    return WorkerCodeIdentity(revision=revision, digest=digest, source=source)


def resolve_checkout_code_identity(
    package_root: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    live_digest: bool = True,
) -> WorkerCodeIdentity:
    """Resolve the code identity currently on disk (live, per-request)."""

    revision, source = resolve_git_revision(environ=environ)
    digest = _cached_package_digest(package_root) if live_digest else None
    if revision is None and digest is None:
        return WorkerCodeIdentity(revision=None, digest=None, source=UNKNOWN)
    if revision is None:
        return WorkerCodeIdentity(revision=None, digest=digest, source="package-digest")
    return WorkerCodeIdentity(revision=revision, digest=digest, source=source)


def record_worker_startup_identity(
    identity: WorkerCodeIdentity | None = None,
    **kwargs: Any,
) -> WorkerCodeIdentity:
    """Record (or re-record) this process's startup code identity."""

    global _STARTUP_IDENTITY
    _STARTUP_IDENTITY = identity or resolve_worker_code_identity(**kwargs)
    return _STARTUP_IDENTITY


def worker_startup_identity() -> WorkerCodeIdentity:
    """Return this process's startup code identity, resolving it once."""

    global _STARTUP_IDENTITY
    if _STARTUP_IDENTITY is None:
        _STARTUP_IDENTITY = resolve_worker_code_identity()
    return _STARTUP_IDENTITY


def current_worker_code_revision() -> str:
    """Return this process's startup code revision, or ``"unknown"``."""

    revision = (worker_startup_identity().revision or "").strip()
    return revision or UNKNOWN


def compare_code_identities(
    startup: WorkerCodeIdentity,
    current: WorkerCodeIdentity,
) -> str:
    """Compare a startup-recorded identity with the checkout on disk.

    Returns ``"healthy"``, ``"stale"``, or ``"unknown"``. A missing startup
    identity is reported as ``unknown``, never healthy: without a recorded
    revision there is no evidence the running modules match the checkout.
    """

    if not startup.known:
        return UNKNOWN
    if not current.known:
        return UNKNOWN
    startup_revision = (startup.revision or "").strip()
    current_revision = (current.revision or "").strip()
    if startup_revision and current_revision:
        if startup_revision != current_revision:
            return "stale"
        # Same revision but the package digest moved (bind-mounted file edited
        # without a commit and outside git's view, e.g. git unavailable at one
        # of the two resolutions): still a mixed-version deployment.
        startup_digest = (startup.digest or "").strip()
        current_digest = (current.digest or "").strip()
        if startup_digest and current_digest and startup_digest != current_digest:
            return "stale"
        return "healthy"
    startup_digest = (startup.digest or "").strip()
    current_digest = (current.digest or "").strip()
    if startup_digest and current_digest:
        return "healthy" if startup_digest == current_digest else "stale"
    return UNKNOWN


@dataclass(frozen=True, slots=True)
class WorkerCodeFreshness:
    """Freshness of one worker's running modules against the checkout."""

    name: str
    status: str
    startup_revision: str
    current_revision: str
    startup_digest: str = UNKNOWN
    current_digest: str = UNKNOWN
    source: str = UNKNOWN

    def to_payload(self) -> dict[str, Any]:
        return {
            "worker": self.name,
            "status": self.status,
            "startupRevision": self.startup_revision,
            "currentRevision": self.current_revision,
            "startupDigest": self.startup_digest,
            "currentDigest": self.current_digest,
            "source": self.source,
        }


def evaluate_worker_freshness(
    *,
    name: str,
    startup: WorkerCodeIdentity,
    current: WorkerCodeIdentity,
) -> WorkerCodeFreshness:
    """Evaluate one worker's freshness with both identities for evidence."""

    return WorkerCodeFreshness(
        name=name,
        status=compare_code_identities(startup, current),
        startup_revision=(startup.revision or "").strip() or UNKNOWN,
        current_revision=(current.revision or "").strip() or UNKNOWN,
        startup_digest=(startup.digest or "").strip() or UNKNOWN,
        current_digest=(current.digest or "").strip() or UNKNOWN,
        source=(startup.source or "").strip() or UNKNOWN,
    )


def stale_code_detail(freshness: WorkerCodeFreshness) -> dict[str, Any]:
    """Return the ``stale_code`` evidence block for one stale worker."""

    return {
        "reasonCode": STALE_CODE_REASON,
        "worker": freshness.name,
        "startupRevision": freshness.startup_revision,
        "currentRevision": freshness.current_revision,
    }


def format_stale_code_message(stale: Sequence[WorkerCodeFreshness]) -> str:
    parts = []
    for item in stale:
        parts.append(
            f"{item.name} (running {item.startup_revision}, "
            f"checkout {item.current_revision})"
        )
    return (
        "Stale worker code detected (reasonCode=stale_code): "
        + "; ".join(parts)
        + ". A host git pull alone is not a deployment: restart the workers "
        "so the running modules match the checkout before new runs are admitted."
    )


def readiness_urls_from_env(
    environ: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(name, url)`` worker readiness endpoints from the environment."""

    env = os.environ if environ is None else environ
    urls: list[tuple[str, str]] = []
    raw = str(env.get(_READINESS_URLS_ENV_KEY) or "").strip()
    if raw:
        for entry in raw.split(","):
            item = entry.strip()
            if not item:
                continue
            if "=" in item:
                name, url = item.split("=", 1)
                name, url = name.strip() or url.strip(), url.strip()
            else:
                name, url = item, item
            if url:
                urls.append((name, url))
    workflow_url = str(env.get(_WORKFLOW_READINESS_URL_ENV_KEY) or "").strip()
    if workflow_url and all(url != workflow_url for _, url in urls):
        urls.append(("workflow", workflow_url))
    return urls


def probe_worker_readiness(
    url: str,
    *,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    """Fetch one worker ``/readyz`` payload best-effort; ``None`` when unknown."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body = response.read()
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def collect_worker_code_freshness(
    urls: Sequence[tuple[str, str]] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    current: WorkerCodeIdentity | None = None,
    probe: Any = None,
) -> list[WorkerCodeFreshness]:
    """Compare each reachable worker's recorded identity with the checkout."""

    targets = list(urls) if urls is not None else readiness_urls_from_env(environ)
    checkout = current or resolve_checkout_code_identity(environ=environ)
    fetch = probe or probe_worker_readiness
    results: list[WorkerCodeFreshness] = []
    for name, url in targets:
        payload = fetch(url)
        if not isinstance(payload, dict):
            results.append(
                WorkerCodeFreshness(
                    name=name,
                    status=UNKNOWN,
                    startup_revision=UNKNOWN,
                    current_revision=(checkout.revision or "").strip() or UNKNOWN,
                )
            )
            continue
        startup = WorkerCodeIdentity(
            revision=str(payload.get("codeRevision") or "").strip() or None,
            digest=str(payload.get("codeDigest") or "").strip() or None,
            source=str(payload.get("codeIdentitySource") or "").strip() or UNKNOWN,
        )
        results.append(evaluate_worker_freshness(name=name, startup=startup, current=checkout))
    return results


def stale_workers_only(
    freshness: Sequence[WorkerCodeFreshness],
) -> tuple[bool, list[WorkerCodeFreshness]]:
    """Return whether new work must be refused: known workers, all stale.

    Fail-open when no worker reported a known identity (nothing to compare
    against) so a monitoring outage cannot wedge admissions; the unknown state
    stays visible in the readiness detail instead.
    """

    known = [item for item in freshness if item.status in {"healthy", "stale"}]
    if not known:
        return False, []
    stale = [item for item in known if item.status == "stale"]
    if stale and len(stale) == len(known):
        return True, stale
    return False, stale


@dataclass(frozen=True, slots=True)
class StaleWorkerRecoveryPlan:
    """Which stale workers restart now and which must drain first."""

    restart_now: tuple[str, ...] = ()
    drain_then_restart: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "restartNow": list(self.restart_now),
            "drainThenRestart": list(self.drain_then_restart),
        }


def plan_stale_worker_recovery(
    stale: Sequence[str | WorkerCodeFreshness],
    busy: Sequence[str] | None = None,
) -> StaleWorkerRecoveryPlan:
    """Split stale workers into idle (restart now) and busy (drain, never kill).

    A busy worker is drained — it stops accepting new activities and restarts
    after in-flight work finishes — rather than killed, so bounded recovery
    cannot discard running work to fix the deployment skew.
    """

    names = [
        item.name if isinstance(item, WorkerCodeFreshness) else str(item)
        for item in stale
    ]
    busy_set = {str(item).strip() for item in (busy or []) if str(item).strip()}
    restart_now = tuple(name for name in names if name not in busy_set)
    drain_then_restart = tuple(name for name in names if name in busy_set)
    return StaleWorkerRecoveryPlan(
        restart_now=restart_now, drain_then_restart=drain_then_restart
    )


@dataclass(slots=True)
class WorkerCodeAdmissionError(RuntimeError):
    """New UserWorkflows are refused while only stale workers serve the queue."""

    stale: list[WorkerCodeFreshness] = field(default_factory=list)

    def __post_init__(self) -> None:
        # NOTE: explicit base init (not zero-arg super()): @dataclass
        # slots=True recreates the class, which breaks the zero-arg
        # super() __class__ cell for Exception subclasses.
        RuntimeError.__init__(self, format_stale_code_message(self.stale))


def enforce_worker_code_admission(
    freshness: Sequence[WorkerCodeFreshness],
) -> list[WorkerCodeFreshness]:
    """Raise :class:`WorkerCodeAdmissionError` when only stale workers remain."""

    blocked, stale = stale_workers_only(freshness)
    if blocked:
        raise WorkerCodeAdmissionError(stale=list(stale))
    return list(stale)
