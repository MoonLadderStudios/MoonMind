"""Trusted Trivy feed snapshots for offline repository scanners.

A source-free refresh job acquires selected Trivy components into private
staging on the deployment's feed cache volume, validates them, and publishes
one immutable directory per content identity with an atomic rename. Scanner
jobs mount the same volume read-only and select one snapshot by that identity,
so concurrent refreshes and repository content cannot change what an admitted
scan reads.

Volume layout: ``snapshots/<id>/`` (a Trivy cache directory plus
``manifest.json``), ``staging/`` (in-progress work, never read by scanners),
and ``current.json`` naming the latest published snapshot.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, ValidationError

from moonmind.schemas.repository_scanner_models import (
    Contract,
    FeedComponent,
    FeedRefresh,
    Text,
)
from moonmind.security.assessments import content_key

SNAPSHOT_SCHEMA = "scanner-feed-snapshot/v1"
MANIFEST = "manifest.json"
FEED_CACHE_TARGET = "/opt/moonmind-scanner-feeds"
ARCHIVE_INPUT = "feed-archive"
ARCHIVE_FILE = "feed.tar"

#: Deployment-selected upstreams. Requests select components, never locations.
UPSTREAMS: dict[str, str] = {
    "vulnerability": "ghcr.io/aquasecurity/trivy-db:2",
    "java": "ghcr.io/aquasecurity/trivy-java-db:1",
    "checks": "ghcr.io/aquasecurity/trivy-checks:1",
}
#: GHCR answers registry requests and redirects blob transfers to its CDN.
FEED_HOSTS = frozenset({"ghcr.io", "pkg-containers.githubusercontent.com"})

#: Each component's Trivy cache-dir location and supported metadata schema.
_LAYOUT: dict[str, str] = {"vulnerability": "db", "java": "java-db", "checks": "policy"}
_SCHEMA = {"vulnerability": 2, "java": 1}
_DATABASE = {"vulnerability": "trivy.db", "java": "trivy-java.db"}
_JAVA_ARCHIVES = (".jar", ".war", ".ear", ".par")
MAX_FILES = 4096
MAX_SNAPSHOT_BYTES = 16 * 1024**3
_KEEP_SNAPSHOTS = 3
_STALE_STAGING_SECONDS = 24 * 3600

Code = Literal[
    "feed_snapshot_unavailable",
    "feed_snapshot_incomplete",
    "feed_snapshot_corrupt",
    "feed_snapshot_incompatible",
    "feed_archive_rejected",
    "feed_download_failed",
    "feed_refresh_failed",
]


class FeedSnapshotError(ValueError):
    """A bounded diagnostic code; never carries paths or upstream text."""

    def __init__(self, code: Code):
        super().__init__(code)
        self.code = code


class FeedComponentRecord(Contract):
    upstream: Text
    schema_version: int | None = None
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    upstream_digest: str | None = Field(None, pattern=r"^sha256:[0-9a-f]{64}$")
    downloaded_at: datetime
    # Freshness clock: upstream ``UpdatedAt`` for databases; the checks bundle
    # records only when Trivy downloaded it.
    updated_at: datetime | None = None
    next_update: datetime | None = None


class FeedSnapshot(Contract):
    """``manifest.json``: written only after every component validated."""

    schema_version: Literal["scanner-feed-snapshot/v1"] = SNAPSHOT_SCHEMA
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    acquisition: Literal["download", "import"]
    published_at: datetime
    components: dict[FeedComponent, FeedComponentRecord]
    validation: Literal["passed"] = "passed"


class FeedEvidence(Contract):
    """What a scan actually used, and whether it met the freshness policy."""

    snapshot: FeedSnapshot | None
    # ``embedded`` is Trivy's built-in check fallback, not refreshed checks.
    checks: Literal["snapshot", "embedded", "not_selected"]
    max_age_hours: int
    evaluated_at: datetime
    stale_components: list[FeedComponent] = Field(default_factory=list)


class FeedRefreshResult(Contract):
    schema_version: Literal["scanner-feed-refresh/v1"] = "scanner-feed-refresh/v1"
    outcome: Literal["published", "reused", "failed"]
    failure: Code | None = None
    # A failed refresh names the prior snapshot it fell back to, if any.
    fallback: bool = False
    usable: bool
    # Requested components the fallback snapshot does not contain.
    missing_components: list[FeedComponent] = Field(default_factory=list)
    feed: FeedEvidence
    limitations: list[str] = Field(default_factory=list)


def _timestamp(value) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise FeedSnapshotError("feed_snapshot_incompatible")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FeedSnapshotError("feed_snapshot_incompatible") from exc
    if parsed.tzinfo is None:
        raise FeedSnapshotError("feed_snapshot_incompatible")
    return parsed


def tree_digest(
    root: Path, *, exclude: frozenset[str] = frozenset(), max_bytes=MAX_SNAPSHOT_BYTES
) -> str:
    """SHA-256 of sorted ``[relative_path, file_sha256]`` pairs.

    Links, special files and multiply-linked files are rejected: published
    content must not alias anything a later writer could change.
    """
    records, total = [], 0

    def vanished(error):
        raise error

    for directory, dirs, files in os.walk(root, onerror=vanished, followlinks=False):
        here = Path(directory)
        if any((here / name).is_symlink() for name in [*dirs, *files]):
            raise FeedSnapshotError("feed_snapshot_corrupt")
        dirs.sort()
        for name in sorted(files):
            path = here / name
            relative = path.relative_to(root).as_posix()
            if relative in exclude:
                continue
            info = path.lstat()
            total += info.st_size
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink > 1
                or total > max_bytes
                or len(records) >= MAX_FILES
            ):
                raise FeedSnapshotError("feed_snapshot_corrupt")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            records.append([relative, digest])
    if not records:
        raise FeedSnapshotError("feed_snapshot_incomplete")
    return content_key(sorted(records))


def _metadata(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise FeedSnapshotError("feed_snapshot_incomplete")
    try:
        if path.stat().st_size > 64 * 1024:
            raise FeedSnapshotError("feed_snapshot_corrupt")
        value = json.loads(path.read_bytes())
    except OSError as exc:
        raise FeedSnapshotError("feed_snapshot_incomplete") from exc
    except ValueError as exc:
        raise FeedSnapshotError("feed_snapshot_corrupt") from exc
    if not isinstance(value, dict):
        raise FeedSnapshotError("feed_snapshot_corrupt")
    return value


def _component_records(
    stage: Path,
    components: list[str],
    upstreams: dict[str, str],
    acquired_at: datetime,
    max_bytes: int,
) -> dict[str, FeedComponentRecord]:
    expected = {_LAYOUT[c] for c in components}
    for entry in stage.iterdir():
        if entry.name not in _LAYOUT.values():
            raise FeedSnapshotError("feed_snapshot_corrupt")
        if entry.name not in expected:
            # A snapshot holds exactly its selected components.
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    records = {}
    for component in components:
        directory = stage / _LAYOUT[component]
        if directory.is_symlink() or not directory.is_dir():
            raise FeedSnapshotError("feed_snapshot_incomplete")
        meta = _metadata(directory / "metadata.json")
        if component == "checks":
            content = directory / "content"
            if (
                content.is_symlink()
                or not content.is_dir()
                or not any(content.iterdir())
            ):
                raise FeedSnapshotError("feed_snapshot_incomplete")
            upstream_digest = meta.get("Digest")
            updated = _timestamp(meta.get("DownloadedAt"))
            if not isinstance(upstream_digest, str) or updated is None:
                raise FeedSnapshotError("feed_snapshot_incompatible")
            schema, following = None, None
        else:
            if meta.get("Version") != _SCHEMA[component]:
                raise FeedSnapshotError("feed_snapshot_incompatible")
            database = directory / _DATABASE[component]
            if database.is_symlink() or not database.is_file():
                raise FeedSnapshotError("feed_snapshot_incomplete")
            schema, upstream_digest = _SCHEMA[component], None
            updated = _timestamp(meta.get("UpdatedAt"))
            following = _timestamp(meta.get("NextUpdate"))
            if updated is None:
                raise FeedSnapshotError("feed_snapshot_incompatible")
        try:
            records[component] = FeedComponentRecord(
                upstream=upstreams[component],
                schemaVersion=schema,
                contentDigest=tree_digest(directory, max_bytes=max_bytes),
                upstreamDigest=upstream_digest,
                downloadedAt=_timestamp(meta.get("DownloadedAt")) or acquired_at,
                updatedAt=updated,
                nextUpdate=following,
            )
        except ValidationError as exc:
            raise FeedSnapshotError("feed_snapshot_incompatible") from exc
    return records


def _snapshots(root: Path) -> Path:
    return root / "snapshots"


def load_snapshot(
    root: Path, snapshot_id: str, *, max_bytes=MAX_SNAPSHOT_BYTES, verify=True
) -> FeedSnapshot:
    """Validate one published snapshot.

    ``verify=False`` reads only the manifest, for a caller that verifies the
    private copy it actually uses instead (see ``copy_snapshot``).
    """
    if (
        not isinstance(snapshot_id, str)
        or len(snapshot_id) != 64
        or not all(c in "0123456789abcdef" for c in snapshot_id)
    ):
        raise FeedSnapshotError("feed_snapshot_unavailable")
    path = _snapshots(root) / snapshot_id
    if path.is_symlink() or not path.is_dir():
        raise FeedSnapshotError("feed_snapshot_unavailable")
    identity = _identity(path)
    try:
        raw = _metadata(path / MANIFEST)
    except FeedSnapshotError:
        if _identity(path) != identity:
            raise FeedSnapshotError("feed_snapshot_unavailable") from None
        raise
    if raw.get("schemaVersion") != SNAPSHOT_SCHEMA:
        raise FeedSnapshotError("feed_snapshot_incompatible")
    try:
        snapshot = FeedSnapshot.model_validate(raw)
    except ValidationError as exc:
        raise FeedSnapshotError("feed_snapshot_corrupt") from exc
    if snapshot.snapshot_id != snapshot_id:
        raise FeedSnapshotError("feed_snapshot_corrupt")
    if verify:
        _verify(
            path,
            snapshot_id,
            path,
            identity,
            exclude=frozenset({MANIFEST}),
            max_bytes=max_bytes,
        )
    return snapshot


def _identity(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_dev, info.st_ino, info.st_ctime_ns


def _verify(tree: Path, expected: str, published: Path, identity, **kwargs) -> None:
    try:
        digest = tree_digest(tree, **kwargs)
    except (OSError, FeedSnapshotError):
        digest = None
    if digest == expected:
        return
    # Retention renames a snapshot away whole, and a later writer may publish
    # the same content again as a new directory: a reader racing either sees
    # absence, not corruption.
    if _identity(published) != identity:
        raise FeedSnapshotError("feed_snapshot_unavailable")
    raise FeedSnapshotError("feed_snapshot_corrupt")


def copy_snapshot(root: Path, snapshot: FeedSnapshot, destination: Path) -> None:
    """Give a scanner its own writable Trivy cache seeded from the snapshot.

    The copy, not the shared source, is verified: it is what the scan reads.
    """
    source = _snapshots(root) / snapshot.snapshot_id
    identity = _identity(source)
    try:
        for component in snapshot.components:
            shutil.copytree(
                source / _LAYOUT[component],
                destination / _LAYOUT[component],
                symlinks=True,
                dirs_exist_ok=True,
            )
    except OSError as exc:
        # Retention retired it mid-copy: absence, never partial content.
        raise FeedSnapshotError("feed_snapshot_unavailable") from exc
    _verify(destination, snapshot.snapshot_id, source, identity)


def current_snapshot(root: Path) -> FeedSnapshot:
    pointer = root / "current.json"
    if not pointer.exists():
        raise FeedSnapshotError("feed_snapshot_unavailable")
    return load_snapshot(root, _metadata(pointer).get("snapshotId"))


def _write_atomic(path: Path, body: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    with temporary.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def _writer_lock(root: Path):
    """Serialize refresh writers on the shared volume; readers never lock.

    Publication, pointer update and retention are short. Holding them under
    one lock keeps retention from retiring a concurrently published snapshot
    before its pointer moves. An exited holder releases the lock.
    """
    with (root / ".writer.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _retire(root: Path, path: Path) -> None:
    """Move out of ``snapshots/`` first, so readers see absence, not a partial tree."""
    trash = root / "staging" / f"retired-{uuid.uuid4().hex}"
    os.rename(path, trash)
    shutil.rmtree(trash, ignore_errors=True)


def publish_snapshot(
    root: Path,
    stage: Path,
    *,
    components: list[str],
    acquisition: Literal["download", "import"],
    upstreams: dict[str, str],
    now: datetime,
    max_bytes: int = MAX_SNAPSHOT_BYTES,
) -> tuple[FeedSnapshot, bool]:
    """Validate ``stage`` and rename it into place; returns ``(snapshot, reused)``.

    ``stage`` must be a directory under ``root/staging`` on the same
    filesystem. Identical content published concurrently is reused.
    """
    records = _component_records(stage, components, upstreams, now, max_bytes)
    snapshot = FeedSnapshot(
        snapshotId=tree_digest(stage, max_bytes=max_bytes),
        acquisition=acquisition,
        publishedAt=now,
        components=records,
    )
    (stage / MANIFEST).write_text(snapshot.model_dump_json(by_alias=True))
    target = _snapshots(root) / snapshot.snapshot_id
    reused = False
    with _writer_lock(root):
        if target.exists():
            try:
                snapshot = load_snapshot(
                    root, snapshot.snapshot_id, max_bytes=max_bytes
                )
                reused = True
            except FeedSnapshotError:
                _retire(root, target)
        if not reused:
            os.rename(stage, target)
        _write_atomic(
            root / "current.json",
            json.dumps({"snapshotId": snapshot.snapshot_id}).encode(),
        )
    return snapshot, reused


def prune(root: Path, *, keep: int = _KEEP_SNAPSHOTS) -> None:
    """Bound volume growth; the current snapshot is never removed."""
    with _writer_lock(root):
        _prune(root, keep)


def _prune(root: Path, keep: int) -> None:
    try:
        current = _metadata(root / "current.json").get("snapshotId")
    except FeedSnapshotError:
        return
    dated = []
    for path in _snapshots(root).iterdir():
        try:
            published = FeedSnapshot.model_validate_json(
                (path / MANIFEST).read_bytes()
            ).published_at
        except (OSError, ValidationError):
            published = datetime.min.replace(tzinfo=UTC)
        dated.append((published, path))
    survivors = 1
    for _published, path in sorted(dated, key=lambda item: item[0], reverse=True):
        if path.name == current:
            continue
        if survivors < keep:
            survivors += 1
            continue
        _retire(root, path)
    cutoff = time.time() - _STALE_STAGING_SECONDS
    for path in (root / "staging").iterdir():
        # Interrupted refreshes leave staging behind; live ones are recent.
        if path.lstat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)


def extract_archive(
    archive: Path, stage: Path, *, max_bytes: int, max_files: int = MAX_FILES
) -> None:
    """Extract a Trivy cache archive: regular files and directories only.

    ``max_files`` bounds every created entry, including implicit parents.
    """
    count = total = 0
    directories: set[tuple[str, ...]] = set()
    try:
        with tarfile.open(archive, "r:*") as bundle:
            for member in bundle:
                parts = [p for p in PurePosixPath(member.name).parts if p != "."]
                if member.name.startswith("/") or any(
                    part in {"..", ""} or "\\" in part for part in parts
                ):
                    raise FeedSnapshotError("feed_archive_rejected")
                if not parts:
                    continue
                if parts[0] not in _LAYOUT.values():
                    raise FeedSnapshotError("feed_archive_rejected")
                destination = stage.joinpath(*parts)
                if not member.isdir() and not member.isreg():
                    raise FeedSnapshotError("feed_archive_rejected")
                parents = parts if member.isdir() else parts[:-1]
                created = {
                    tuple(parents[:depth]) for depth in range(1, len(parents) + 1)
                } - directories
                directories |= created
                count += len(created)
                if member.isreg():
                    count += 1
                    total += member.size
                if count > max_files or total > max_bytes:
                    raise FeedSnapshotError("feed_archive_rejected")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                # Exclusive create: a repeated member cannot replace content.
                with destination.open("xb") as stream:
                    shutil.copyfileobj(source, stream)
    except (tarfile.TarError, FileExistsError, NotADirectoryError) as exc:
        raise FeedSnapshotError("feed_archive_rejected") from exc


Fetch = Callable[[str, Path, Path, float], None]


def trivy_fetch(component: str, stage: Path, scratch: Path, deadline: float) -> None:
    """Fetch one component into private scratch, then move only its layout.

    Trivy has database-only flags for the vulnerability and Java databases.
    The checks bundle has none: a misconfiguration scan of an empty directory
    refreshes it, and Trivy silently falls back to embedded checks when that
    download fails, so the bundle's presence is verified rather than assumed.
    """
    cache = scratch / component
    cache.mkdir()
    common = [
        "--cache-dir",
        str(cache),
        "--skip-version-check",
        "--disable-telemetry",
        "--no-progress",
        "--quiet",
    ]
    if component == "vulnerability":
        argv = ["trivy", "image", "--download-db-only", "--db-repository"]
    elif component == "java":
        argv = ["trivy", "image", "--download-java-db-only", "--java-db-repository"]
    else:
        empty = scratch / "empty"
        empty.mkdir(exist_ok=True)
        argv = ["trivy", "config", "--config=", "--format=json", "--exit-code=0"]
        argv += ["--output", str(scratch / "checks.json"), "--checks-bundle-repository"]
    argv.append(UPSTREAMS[component])
    argv += common
    if component == "checks":
        argv.append(str(empty))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FeedSnapshotError("feed_download_failed")
    try:
        completed = subprocess.run(
            argv,
            env={**os.environ, "HOME": str(scratch)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FeedSnapshotError("feed_download_failed") from exc
    produced = cache / _LAYOUT[component]
    if (
        completed.returncode != 0
        or produced.is_symlink()
        or not produced.is_dir()
        or (component == "checks" and not (produced / "content").is_dir())
    ):
        raise FeedSnapshotError("feed_download_failed")
    os.rename(produced, stage / _LAYOUT[component])


def feed_evidence(
    snapshot: FeedSnapshot | None,
    *,
    misconfig: bool,
    max_age_hours: int,
    now: datetime,
) -> FeedEvidence:
    limit = timedelta(hours=max_age_hours)
    stale = sorted(
        name
        for name, record in (snapshot.components.items() if snapshot else ())
        # A component whose age cannot be established is not within policy.
        if record.updated_at is None or now - record.updated_at > limit
    )
    if not misconfig:
        checks = "not_selected"
    elif snapshot is not None and "checks" in snapshot.components:
        checks = "snapshot"
    else:
        checks = "embedded"
    return FeedEvidence(
        snapshot=snapshot,
        checks=checks,
        maxAgeHours=max_age_hours,
        evaluatedAt=now,
        staleComponents=stale,
    )


def scanner_feed(
    scan, root: Path, destination: Path, assets: list[str], *, now: datetime
) -> tuple[FeedEvidence | None, list[str]]:
    """Seed a Trivy scan's private cache from its admitted snapshot."""
    if scan.scanner != "trivy":
        return None, []
    snapshot = None
    if scan.database_cache_ref:
        snapshot = load_snapshot(root, scan.database_digest, verify=False)
        copy_snapshot(root, snapshot, destination)
    evidence = feed_evidence(
        snapshot,
        misconfig="misconfig" in scan.trivy_scanners,
        max_age_hours=scan.feed_max_age_hours,
        now=now,
    )
    limitations = ["feed_snapshot_stale"] if evidence.stale_components else []
    # The Java index is required only when Java archives are examined.
    if (
        "vuln" in scan.trivy_scanners
        and (snapshot is None or "java" not in snapshot.components)
        and any(asset.lower().endswith(_JAVA_ARCHIVES) for asset in assets)
    ):
        limitations.append("java_db_unavailable")
    return evidence, limitations


def run_refresh(
    refresh: FeedRefresh,
    root: Path,
    output: Path,
    *,
    archive: Path | None = None,
    fetch: Fetch = trivy_fetch,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """Publish a validated snapshot, or report the usable prior one."""
    deadline = time.monotonic() + refresh.timeout_seconds
    output.mkdir(parents=True, exist_ok=True)
    (_snapshots(root)).mkdir(parents=True, exist_ok=True)
    (root / "staging").mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="refresh-", dir=root / "staging"))
    snapshot, outcome, failure = None, "failed", None
    if refresh.acquisition == "import":
        upstreams = dict.fromkeys(
            refresh.components, f"artifact:{refresh.archive_artifact_ref}"
        )
    else:
        upstreams = UPSTREAMS
    try:
        if refresh.acquisition == "import":
            extract_archive(archive, stage, max_bytes=refresh.max_bytes)
        else:
            # Scratch shares the volume with staging so components move by
            # rename; only their expected layout ever enters the stage.
            with tempfile.TemporaryDirectory(
                prefix="fetch-", dir=root / "staging"
            ) as scratch:
                for component in refresh.components:
                    fetch(component, stage, Path(scratch), deadline)
        snapshot, reused = publish_snapshot(
            root,
            stage,
            components=list(refresh.components),
            acquisition=refresh.acquisition,
            upstreams=upstreams,
            now=clock(),
            max_bytes=refresh.max_bytes,
        )
        outcome = "reused" if reused else "published"
    except FeedSnapshotError as exc:
        failure = exc.code
    except (OSError, ValueError):
        failure = "feed_refresh_failed"
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if failure is None:
        try:
            prune(root)
        except OSError:
            pass  # Retention is best effort; the publication stands.
    limitations, missing = [], []
    if failure is not None:
        limitations.append("feed_refresh_failed")
        try:
            snapshot = current_snapshot(root)
        except FeedSnapshotError as exc:
            limitations.append(exc.code)
        else:
            missing = [c for c in refresh.components if c not in snapshot.components]
            if missing:
                limitations.append("feed_components_missing")
    evidence = feed_evidence(
        snapshot,
        misconfig=True,
        max_age_hours=refresh.max_age_hours,
        now=clock(),
    )
    if evidence.stale_components:
        limitations.append("feed_snapshot_stale")
    usable = snapshot is not None and not evidence.stale_components and not missing
    result = FeedRefreshResult(
        outcome=outcome,
        failure=failure,
        fallback=failure is not None and snapshot is not None,
        usable=usable,
        missingComponents=missing,
        feed=evidence,
        limitations=sorted(set(limitations)),
    )
    (output / "refresh.json").write_text(result.model_dump_json(by_alias=True))
    return 0 if failure is None and usable else 2


def build_feed_refresh_job(
    refresh: FeedRefresh, *, image_source_ref: str = "repository-security"
) -> dict:
    """Source-free workload: no workspace, credentials, or environment."""
    download = refresh.acquisition == "download"
    return {
        "imageSourceRef": image_source_ref,
        "feedRefresh": refresh.model_dump(mode="json", by_alias=True),
        # An empty attachment list mounts no workspace at all.
        "inputAttachments": (
            []
            if download
            else [
                {
                    "name": ARCHIVE_INPUT,
                    "artifactRef": refresh.archive_artifact_ref,
                    "fileName": ARCHIVE_FILE,
                    "maxBytes": refresh.max_bytes,
                }
            ]
        ),
        "entrypoint": ["python"],
        "command": [
            "-I",
            "-m",
            "moonmind.security.scanner_feeds",
            refresh.model_dump_json(by_alias=True),
            FEED_CACHE_TARGET,
            f"/outputs/{refresh.output_directory}",
        ],
        "networkMode": "bridge" if download else "none",
        **({"egressPolicyRef": refresh.egress_policy_ref} if download else {}),
        "workdir": "/workspace",
        "resources": {"cpuMillis": 2000, "memoryMiB": 2048, "pids": 256},
        "timeoutSeconds": refresh.timeout_seconds + 15,
        "outputs": [
            {
                "name": "refresh",
                "relativePath": f"{refresh.output_directory}/refresh.json",
            }
        ],
        "caches": [
            {
                "cacheRef": refresh.feed_cache_ref,
                "target": FEED_CACHE_TARGET,
                "readOnly": False,
            }
        ],
    }


def expand_feed_refresh_job(value):
    """Fill a typed refresh request with its canonical job, for every entry.

    A schedule or other caller names only ``feedRefresh`` (and resources);
    supplied fields win, so any divergence still fails validation below.
    """
    if not isinstance(value, dict):
        return value
    raw = value.get("feedRefresh", value.get("feed_refresh"))
    if raw is None:
        return value
    try:
        refresh = FeedRefresh.model_validate(raw)
    except ValueError:
        return value
    source = value.get("imageSourceRef", value.get("image_source_ref"))
    canonical = build_feed_refresh_job(
        refresh,
        **({"image_source_ref": source} if isinstance(source, str) else {}),
    )
    if "image" in value:
        canonical.pop("imageSourceRef")
    # Field names and aliases both count as supplied (populate_by_name).
    supplied = {
        head + "".join(part.title() for part in rest)
        for head, *rest in (str(key).split("_") for key in value)
    }
    return {
        **{key: item for key, item in canonical.items() if key not in supplied},
        **value,
    }


def validate_feed_refresh_job(spec) -> None:
    """Rechecked wherever a spec is decoded: admission, persistence, worker."""
    refresh = spec.feed_refresh
    if refresh is None:
        return
    if spec.repository_scan is not None or spec.workload_profile is not None:
        raise ValueError("feed refresh is its own workload")
    if spec.image_source_ref is None:
        raise ValueError("feed refresh uses a deployment-owned image source")
    if spec.input_attachments is None:
        raise ValueError("feed refresh never mounts the workspace")
    canonical = build_feed_refresh_job(refresh, image_source_ref=spec.image_source_ref)
    for field, expected in (
        ("entrypoint", canonical["entrypoint"]),
        ("command", canonical["command"]),
        ("network_mode", canonical["networkMode"]),
        ("egress_policy_ref", canonical.get("egressPolicyRef")),
        ("workdir", "/workspace"),
        ("environment", []),
        ("service_ref", None),
        ("registry_credential_ref", None),
        ("timeout_seconds", canonical["timeoutSeconds"]),
    ):
        if getattr(spec, field) != expected:
            raise ValueError("feed refresh workload differs from its admission")
    for field, key in (
        ("input_attachments", "inputAttachments"),
        ("outputs", "outputs"),
        ("caches", "caches"),
    ):
        actual = [v.model_dump(by_alias=True) for v in getattr(spec, field)]
        model = type(getattr(spec, field)[0]) if getattr(spec, field) else None
        expected = [
            model.model_validate(item).model_dump(by_alias=True) if model else item
            for item in canonical[key]
        ]
        if actual != expected:
            raise ValueError("feed refresh mounts differ from its admission")


def feed_policy_admits(policy) -> bool:
    """A refresh's egress reaches feed hosts only, never broader provider egress."""
    return all(
        d.host in FEED_HOSTS
        and d.protocol == "https"
        and d.port == 443
        and d.target_network_ref is None
        for d in policy.destinations
    )


def main(argv: list[str]) -> int:
    try:
        refresh = FeedRefresh.model_validate_json(argv[1])
        archive = Path("/inputs") / ARCHIVE_INPUT / ARCHIVE_FILE
        code = run_refresh(refresh, Path(argv[2]), Path(argv[3]), archive=archive)
    # Never print raw exceptions: they can carry paths or upstream responses.
    except Exception:  # noqa: BLE001
        print("Feed refresh failed; evidence is incomplete.")
        return 2
    print(
        "Feed snapshot published."
        if code == 0
        else "Feed refresh incomplete; inspect refresh.json."
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
