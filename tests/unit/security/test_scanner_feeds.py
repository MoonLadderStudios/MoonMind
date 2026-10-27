"""Trusted scanner feed snapshots (MoonLadderStudios/MoonMind#5041).

Every case uses local fixtures shaped like Trivy's cache directory; no
external feed is contacted.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from moonmind.config.container_backend_settings import (
    CACHE_SOURCES_ENV_KEY,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobSpec,
    ContainerJobSubmitRequest,
)
from moonmind.schemas.repository_scanner_models import FeedRefresh, RepositoryScan
from moonmind.security import scanner_feeds as feeds
from moonmind.security.egress_policy import parse_egress_policies
from moonmind.security.repository_scanners import ScannerExecution, build_scanner_job
from moonmind.workflows.temporal.container_job_backend import DockerContainerJobBackend

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
WORKSPACE = {"kind": "sandbox", "workspaceId": "run-1"}


def trivy_cache(root: Path, components=("vulnerability",), *, updated=NOW, seed="a"):
    """A Trivy cache directory as ``--download-*-only`` would leave it."""
    stamp = updated.isoformat().replace("+00:00", "Z")
    for component in components:
        if component == "checks":
            content = root / "policy" / "content" / "checks"
            content.mkdir(parents=True)
            (content / "rule.rego").write_text(f"package fixture.{seed}\n")
            (root / "policy" / "metadata.json").write_text(
                json.dumps({"Digest": "sha256:" + "c" * 64, "DownloadedAt": stamp})
            )
            continue
        layout = {
            "vulnerability": ("db", "trivy.db", 2),
            "java": ("java-db", "trivy-java.db", 1),
        }
        directory, name, version = layout[component]
        (root / directory).mkdir(parents=True)
        (root / directory / name).write_bytes(f"{component}-{seed}".encode())
        (root / directory / "metadata.json").write_text(
            json.dumps(
                {
                    "Version": version,
                    "UpdatedAt": stamp,
                    "NextUpdate": (updated + timedelta(hours=6))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "DownloadedAt": stamp,
                }
            )
        )
    return root


def publish(root: Path, components=("vulnerability",), **kwargs):
    (root / "snapshots").mkdir(parents=True, exist_ok=True)
    (root / "staging").mkdir(exist_ok=True)
    stage = root / "staging" / f"fixture-{os.urandom(4).hex()}"
    stage.mkdir()
    trivy_cache(stage, components, **kwargs)
    snapshot, _ = feeds.publish_snapshot(
        root,
        stage,
        components=list(components),
        acquisition="download",
        upstreams=feeds.UPSTREAMS,
        now=NOW,
    )
    return snapshot


def scan(snapshot_id, **extra):
    return RepositoryScan(
        scanner="trivy",
        outputDirectory="artifacts/trivy",
        databaseCacheRef="scanner-db",
        databaseDigest=snapshot_id,
        **extra,
    )


def use(root, snapshot_id, tmp_path, *, assets=(), now=NOW, **extra):
    destination = tmp_path / f"private-{os.urandom(4).hex()}"
    destination.mkdir()
    evidence, limitations = feeds.scanner_feed(
        scan(snapshot_id, **extra), root, destination, list(assets), now=now
    )
    return evidence, limitations, destination


# ------------------------------------------------- snapshot states (reader)


def test_fresh_snapshot_supplies_a_private_trivy_cache_with_complete_evidence(
    tmp_path,
):
    root = tmp_path / "feeds"
    snapshot = publish(root, ("vulnerability", "java", "checks"))
    evidence, limitations, private = use(root, snapshot.snapshot_id, tmp_path)

    assert limitations == []
    assert evidence.stale_components == []
    assert evidence.checks == "snapshot"
    assert (private / "db" / "trivy.db").read_bytes() == b"vulnerability-a"
    assert not (private / feeds.MANIFEST).exists()
    record = evidence.snapshot.components["vulnerability"]
    assert record.upstream == "ghcr.io/aquasecurity/trivy-db:2"
    assert record.schema_version == 2
    assert record.updated_at == NOW
    assert record.next_update == NOW + timedelta(hours=6)
    assert record.downloaded_at == NOW
    assert (
        evidence.snapshot.components["checks"].upstream_digest == "sha256:" + "c" * 64
    )
    assert evidence.snapshot.validation == "passed"
    # The whole record survives the scanner's own evidence contract.
    execution = ScannerExecution(
        inputDigest="0" * 64,
        assets=[],
        version="0.1.0",
        exits={},
        states={},
        databaseDigest=snapshot.snapshot_id,
        feed=evidence,
    )
    restored = ScannerExecution.model_validate_json(
        execution.model_dump_json(by_alias=True)
    )
    assert restored.feed.snapshot.components["java"].upstream.endswith(
        "trivy-java-db:1"
    )


def test_stale_snapshot_is_usable_only_as_incomplete_coverage(tmp_path):
    root = tmp_path / "feeds"
    snapshot = publish(root, updated=NOW - timedelta(hours=100))
    evidence, limitations, _ = use(
        root, snapshot.snapshot_id, tmp_path, feedMaxAgeHours=72
    )
    assert evidence.stale_components == ["vulnerability"]
    assert limitations == ["feed_snapshot_stale"]
    # The operator's policy, not image maintenance, decides freshness.
    evidence, limitations, _ = use(
        root, snapshot.snapshot_id, tmp_path, feedMaxAgeHours=168
    )
    assert limitations == [] and evidence.max_age_hours == 168


def test_absent_snapshot_is_unavailable(tmp_path):
    root = tmp_path / "feeds"
    publish(root)
    with pytest.raises(feeds.FeedSnapshotError) as error:
        use(root, "f" * 64, tmp_path)
    assert error.value.code == "feed_snapshot_unavailable"


def test_partially_published_snapshot_is_never_read(tmp_path):
    root = tmp_path / "feeds"
    snapshot = publish(root)
    (root / "snapshots" / snapshot.snapshot_id / feeds.MANIFEST).unlink()
    with pytest.raises(feeds.FeedSnapshotError) as error:
        use(root, snapshot.snapshot_id, tmp_path)
    assert error.value.code == "feed_snapshot_incomplete"
    # In-progress staging is invisible to an identity-addressed reader.
    stage = root / "staging" / "in-progress"
    trivy_cache(stage, seed="in-progress")
    staged_id = feeds.tree_digest(stage)
    with pytest.raises(feeds.FeedSnapshotError) as error:
        use(root, staged_id, tmp_path)
    assert error.value.code == "feed_snapshot_unavailable"


@pytest.mark.parametrize("change", ["database", "added", "symlink"])
def test_corrupt_or_mutated_snapshot_is_rejected(tmp_path, change):
    root = tmp_path / "feeds"
    snapshot = publish(root)
    path = root / "snapshots" / snapshot.snapshot_id
    if change == "database":
        (path / "db" / "trivy.db").write_bytes(b"tampered")
    elif change == "added":
        (path / "db" / "extra.db").write_bytes(b"planted")
    else:
        (path / "db" / "trivy.db").unlink()
        (path / "db" / "trivy.db").symlink_to(tmp_path)
    with pytest.raises(feeds.FeedSnapshotError) as error:
        use(root, snapshot.snapshot_id, tmp_path)
    assert error.value.code == "feed_snapshot_corrupt"
    with pytest.raises(feeds.FeedSnapshotError):
        feeds.load_snapshot(root, snapshot.snapshot_id)


def test_checks_bundle_freshness_follows_its_recorded_download(tmp_path):
    root = tmp_path / "feeds"
    (root / "snapshots").mkdir(parents=True)
    stage = root / "staging" / "mixed"
    trivy_cache(stage, ("vulnerability",))
    trivy_cache(stage, ("checks",), updated=NOW - timedelta(hours=100))
    snapshot, _ = feeds.publish_snapshot(
        root,
        stage,
        components=["vulnerability", "checks"],
        acquisition="download",
        upstreams=feeds.UPSTREAMS,
        now=NOW,
    )
    assert snapshot.components["checks"].updated_at == NOW - timedelta(hours=100)
    evidence, limitations, _ = use(
        root, snapshot.snapshot_id, tmp_path, feedMaxAgeHours=72
    )
    assert evidence.stale_components == ["checks"]
    assert limitations == ["feed_snapshot_stale"]


@pytest.mark.parametrize("where", ["manifest", "database", "checks"])
def test_incompatible_snapshot_is_rejected(tmp_path, where):
    root = tmp_path / "feeds"
    if where == "manifest":
        snapshot = publish(root)
        manifest = root / "snapshots" / snapshot.snapshot_id / feeds.MANIFEST
        body = json.loads(manifest.read_text())
        body["schemaVersion"] = "scanner-feed-snapshot/v9"
        manifest.write_text(json.dumps(body))
        with pytest.raises(feeds.FeedSnapshotError) as error:
            use(root, snapshot.snapshot_id, tmp_path)
    else:
        stage = tmp_path / "feeds" / "staging" / "new"
        (tmp_path / "feeds" / "snapshots").mkdir(parents=True)
        if where == "database":
            components = ["vulnerability"]
            trivy_cache(stage)
            metadata = stage / "db" / "metadata.json"
            metadata.write_text(
                json.dumps({**json.loads(metadata.read_text()), "Version": 3})
            )
        else:
            # Without its download time a checks bundle's age is unknown.
            components = ["checks"]
            trivy_cache(stage, components)
            metadata = stage / "policy" / "metadata.json"
            metadata.write_text(json.dumps({"Digest": "sha256:" + "c" * 64}))
        with pytest.raises(feeds.FeedSnapshotError) as error:
            feeds.publish_snapshot(
                root,
                stage,
                components=components,
                acquisition="download",
                upstreams=feeds.UPSTREAMS,
                now=NOW,
            )
    assert error.value.code == "feed_snapshot_incompatible"


# ----------------------------------------- components and check fallback


def test_embedded_check_fallback_is_identified_separately(tmp_path):
    root = tmp_path / "feeds"
    with_checks = publish(root, ("vulnerability", "checks"), seed="1")
    without = publish(root, ("vulnerability",), seed="2")
    assert use(root, with_checks.snapshot_id, tmp_path)[0].checks == "snapshot"
    assert use(root, without.snapshot_id, tmp_path)[0].checks == "embedded"
    assert (
        use(root, without.snapshot_id, tmp_path, trivyScanners=["vuln"])[0].checks
        == "not_selected"
    )
    misconfig_only = feeds.scanner_feed(
        RepositoryScan(
            scanner="trivy",
            outputDirectory="artifacts/trivy",
            trivyScanners=["misconfig"],
        ),
        root,
        tmp_path,
        [],
        now=NOW,
    )[0]
    assert misconfig_only.snapshot is None and misconfig_only.checks == "embedded"


def test_java_database_is_required_only_for_java_archives(tmp_path):
    root = tmp_path / "feeds"
    snapshot = publish(root)
    assert use(root, snapshot.snapshot_id, tmp_path, assets=["app.py"])[1] == []
    assert use(root, snapshot.snapshot_id, tmp_path, assets=["lib/app.JAR"])[1] == [
        "java_db_unavailable"
    ]
    with_java = publish(root, ("vulnerability", "java"), seed="j")
    assert use(root, with_java.snapshot_id, tmp_path, assets=["lib/app.jar"])[1] == []


def test_refresh_selects_vulnerability_and_optional_components_only():
    with pytest.raises(ValueError, match="vulnerability"):
        FeedRefresh(feedCacheRef="feeds", egressPolicyRef="feeds", components=["java"])
    with pytest.raises(ValueError, match="duplicate"):
        FeedRefresh(
            feedCacheRef="feeds",
            egressPolicyRef="feeds",
            components=["vulnerability", "vulnerability"],
        )
    # Upstream locations are not request fields at all.
    with pytest.raises(ValueError):
        FeedRefresh.model_validate(
            {
                "feedCacheRef": "feeds",
                "egressPolicyRef": "feeds",
                "dbRepository": "attacker.example/db:2",
            }
        )


# -------------------------------------------- refresh, failure and policy


def fake_fetch(seed="new", *, updated=NOW):
    def fetch(component, stage, scratch, deadline):
        trivy_cache(stage, (component,), updated=updated, seed=seed)

    return fetch


def failing_fetch(component, stage, scratch, deadline):
    raise feeds.FeedSnapshotError("feed_download_failed")


def refresh(**extra):
    return FeedRefresh(
        feedCacheRef="feeds",
        egressPolicyRef="feeds",
        components=["vulnerability", "checks"],
        **extra,
    )


def refresh_result(output) -> dict:
    return json.loads((output / "refresh.json").read_text())


def test_refresh_publishes_attributed_snapshot_and_reuses_identical_content(
    tmp_path,
):
    root, output = tmp_path / "feeds", tmp_path / "out"
    code = feeds.run_refresh(
        refresh(), root, output, fetch=fake_fetch(), clock=lambda: NOW
    )
    result = refresh_result(output)
    assert code == 0
    assert result["outcome"] == "published" and result["usable"] is True
    snapshot = result["feed"]["snapshot"]
    assert set(snapshot["components"]) == {"vulnerability", "checks"}
    for record in snapshot["components"].values():
        assert record["upstream"] in feeds.UPSTREAMS.values()
        assert len(record["contentDigest"]) == 64 and record["downloadedAt"]
    assert snapshot["validation"] == "passed"
    assert feeds.current_snapshot(root).snapshot_id == snapshot["snapshotId"]
    assert list((root / "staging").iterdir()) == []

    assert (
        feeds.run_refresh(
            refresh(), root, output, fetch=fake_fetch(), clock=lambda: NOW
        )
        == 0
    )
    assert refresh_result(output)["outcome"] == "reused"


def test_network_failure_keeps_prior_snapshot_only_within_policy(tmp_path):
    root, output = tmp_path / "feeds", tmp_path / "out"
    feeds.run_refresh(refresh(), root, output, fetch=fake_fetch(), clock=lambda: NOW)
    prior = refresh_result(output)["feed"]["snapshot"]["snapshotId"]

    later = NOW + timedelta(hours=24)
    code = feeds.run_refresh(
        refresh(), root, output, fetch=failing_fetch, clock=lambda: later
    )
    result = refresh_result(output)
    assert code == 2
    assert result["outcome"] == "failed"
    assert result["failure"] == "feed_download_failed"
    assert result["fallback"] is True and result["usable"] is True
    assert result["feed"]["snapshot"]["snapshotId"] == prior
    assert result["limitations"] == ["feed_refresh_failed"]

    expired = NOW + timedelta(hours=73)
    feeds.run_refresh(
        refresh(), root, output, fetch=failing_fetch, clock=lambda: expired
    )
    result = refresh_result(output)
    assert result["fallback"] is True and result["usable"] is False
    assert result["feed"]["staleComponents"] == ["checks", "vulnerability"]
    assert result["limitations"] == ["feed_refresh_failed", "feed_snapshot_stale"]


def test_fallback_without_requested_components_is_not_usable(tmp_path):
    root, output = tmp_path / "feeds", tmp_path / "out"
    database_only = FeedRefresh(
        feedCacheRef="feeds", egressPolicyRef="feeds", components=["vulnerability"]
    )
    feeds.run_refresh(
        database_only, root, output, fetch=fake_fetch(), clock=lambda: NOW
    )

    code = feeds.run_refresh(
        refresh(), root, output, fetch=failing_fetch, clock=lambda: NOW
    )
    result = refresh_result(output)
    assert code == 2
    assert result["fallback"] is True and result["usable"] is False
    assert result["missingComponents"] == ["checks"]
    assert result["limitations"] == [
        "feed_components_missing",
        "feed_refresh_failed",
    ]


def test_network_failure_without_prior_snapshot_reports_missing_data(tmp_path):
    root, output = tmp_path / "feeds", tmp_path / "out"
    assert (
        feeds.run_refresh(
            refresh(), root, output, fetch=failing_fetch, clock=lambda: NOW
        )
        == 2
    )
    result = refresh_result(output)
    assert result["feed"]["snapshot"] is None
    assert result["fallback"] is False and result["usable"] is False
    assert result["limitations"] == ["feed_refresh_failed", "feed_snapshot_unavailable"]
    assert list((root / "snapshots").iterdir()) == []
    assert list((root / "staging").iterdir()) == []


def test_partial_component_download_publishes_nothing(tmp_path):
    root, output = tmp_path / "feeds", tmp_path / "out"

    def checks_fail(component, stage, scratch, deadline):
        if component == "checks":
            raise feeds.FeedSnapshotError("feed_download_failed")
        fake_fetch()(component, stage, scratch, deadline)

    feeds.run_refresh(refresh(), root, output, fetch=checks_fail, clock=lambda: NOW)
    assert list((root / "snapshots").iterdir()) == []
    assert refresh_result(output)["failure"] == "feed_download_failed"


def test_trivy_fetch_uses_fixed_upstreams_and_detects_check_fallback(
    tmp_path, monkeypatch
):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        cache = Path(argv[argv.index("--cache-dir") + 1])
        if "--download-db-only" in argv:
            trivy_cache(cache)
        # A checks bundle download failure still exits 0 with embedded checks.
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(feeds.subprocess, "run", run)
    stage, scratch = tmp_path / "stage", tmp_path / "scratch"
    stage.mkdir()
    scratch.mkdir()
    deadline = feeds.time.monotonic() + 60
    feeds.trivy_fetch("vulnerability", stage, scratch, deadline)
    assert (stage / "db" / "trivy.db").is_file()
    with pytest.raises(feeds.FeedSnapshotError) as error:
        feeds.trivy_fetch("checks", stage, scratch, deadline)
    assert error.value.code == "feed_download_failed"
    assert not (stage / "policy").exists()
    assert (
        calls[0][calls[0].index("--db-repository") + 1]
        == feeds.UPSTREAMS["vulnerability"]
    )
    assert calls[1][calls[1].index("--checks-bundle-repository") + 1] == (
        feeds.UPSTREAMS["checks"]
    )
    for argv in calls:
        assert "--skip-version-check" in argv and "--disable-telemetry" in argv


# ------------------------------------------------------- atomic handoff


def test_concurrent_refresh_and_reads_never_observe_partial_snapshots(tmp_path):
    root = tmp_path / "feeds"
    first = publish(root, seed="0")
    errors, seen = [], set()
    stop = threading.Event()

    def writer(index):
        try:
            for round_ in range(8):
                output = tmp_path / f"out-{index}-{round_}"
                # Two writers publish identical content each round.
                feeds.run_refresh(
                    refresh(),
                    root,
                    output,
                    fetch=fake_fetch(seed=str(round_)),
                    clock=lambda: NOW,
                )
                if refresh_result(output)["outcome"] == "failed":
                    errors.append(refresh_result(output))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader():
        while not stop.is_set():
            try:
                snapshot = feeds.current_snapshot(root)
                seen.add(snapshot.snapshot_id)
                destination = tmp_path / f"read-{threading.get_ident()}"
                destination.mkdir(exist_ok=True)
                feeds.copy_snapshot(root, snapshot, destination)
                feeds.shutil.rmtree(destination)
            except feeds.FeedSnapshotError as exc:
                # Retention may retire an old snapshot between pointer and
                # read: that is absence, never a partially published tree.
                if exc.code != "feed_snapshot_unavailable":
                    errors.append(exc)
            except FileNotFoundError:
                # The reader's own private copy can lose a file to a racing
                # retirement mid-copy; only shared-tree errors matter here.
                pass

    readers = [threading.Thread(target=reader) for _ in range(3)]
    writers = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for thread in readers + writers:
        thread.start()
    for thread in writers:
        thread.join()
    stop.set()
    for thread in readers:
        thread.join()

    assert errors == []
    assert seen - {first.snapshot_id}
    current = feeds.current_snapshot(root)
    assert current.snapshot_id in {p.name for p in (root / "snapshots").iterdir()}
    assert len(list((root / "snapshots").iterdir())) <= 3


# ----------------------------------------------------- imported archives


def archive(tmp_path, members) -> Path:
    path = tmp_path / "feed.tar"
    with tarfile.open(path, "w") as bundle:
        for name, kind, body in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(body)
                bundle.addfile(info, io.BytesIO(body))
                continue
            info.type = {
                "symlink": tarfile.SYMTYPE,
                "hardlink": tarfile.LNKTYPE,
                "device": tarfile.CHRTYPE,
                "fifo": tarfile.FIFOTYPE,
                "dir": tarfile.DIRTYPE,
            }[kind]
            info.linkname = body.decode()
            bundle.addfile(info)
    return path


def cache_members(tmp_path):
    source = trivy_cache(tmp_path / "source")
    return [
        (path.relative_to(source).as_posix(), "file", path.read_bytes())
        for path in sorted(source.rglob("*"))
        if path.is_file()
    ]


def test_imported_archive_publishes_attributed_snapshot_offline(tmp_path):
    root, output = tmp_path / "feeds", tmp_path / "out"
    members = cache_members(tmp_path)
    members.append(("./java-db/trivy-java.db", "file", b"unselected"))
    request = FeedRefresh(
        acquisition="import", feedCacheRef="feeds", archiveArtifactRef="art_feed1"
    )
    code = feeds.run_refresh(
        request,
        root,
        output,
        archive=archive(tmp_path, members),
        clock=lambda: NOW,
    )
    snapshot = refresh_result(output)["feed"]["snapshot"]
    assert code == 0
    assert snapshot["acquisition"] == "import"
    assert snapshot["components"]["vulnerability"]["upstream"] == "artifact:art_feed1"
    # Only selected components are published.
    assert list(snapshot["components"]) == ["vulnerability"]
    assert not (root / "snapshots" / snapshot["snapshotId"] / "java-db").exists()


@pytest.mark.parametrize(
    "member",
    [
        ("../db/trivy.db", "file", b"x"),
        ("/db/trivy.db", "file", b"x"),
        ("db/../../escape", "file", b"x"),
        ("db/link", "symlink", b"/etc/passwd"),
        ("db/hard", "hardlink", b"db/trivy.db"),
        ("db/device", "device", b""),
        ("db/fifo", "fifo", b""),
        ("fanal/fanal.db", "file", b"x"),
        ("manifest.json", "file", b"{}"),
        ("db/trivy.db", "file", b"duplicate"),
    ],
)
def test_import_rejects_traversal_links_devices_and_replacement(tmp_path, member):
    root, output = tmp_path / "feeds", tmp_path / "out"
    request = FeedRefresh(
        acquisition="import", feedCacheRef="feeds", archiveArtifactRef="art_feed1"
    )
    code = feeds.run_refresh(
        request,
        root,
        output,
        archive=archive(tmp_path, [*cache_members(tmp_path), member]),
        clock=lambda: NOW,
    )
    assert code == 2
    assert refresh_result(output)["failure"] == "feed_archive_rejected"
    assert list((root / "snapshots").iterdir()) == []
    assert not (tmp_path / "escape").exists()


def test_import_bounds_archive_size(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(feeds.FeedSnapshotError):
        feeds.extract_archive(
            archive(tmp_path, cache_members(tmp_path)), stage, max_bytes=4
        )


@pytest.mark.parametrize(
    "members",
    [
        [(f"db/empty-{index}", "dir", b"") for index in range(101)],
        [("db/" + "/".join("n" * 101) + "/trivy.db", "file", b"x")],
    ],
    ids=["explicit", "implicit"],
)
def test_import_bounds_directory_entries(tmp_path, members):
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(feeds.FeedSnapshotError):
        feeds.extract_archive(
            archive(tmp_path, members), stage, max_bytes=1024, max_files=100
        )
    assert len(list(stage.rglob("*"))) <= 100


# ------------------------------------------------ job boundaries (A4/S5)


def refresh_spec(request: FeedRefresh, **overrides) -> dict:
    spec = {**feeds.build_feed_refresh_job(request), "workspaceRef": WORKSPACE}
    spec.update(overrides)
    return spec


def test_refresh_job_is_source_free_and_credential_free():
    spec = ContainerJobSpec.model_validate(refresh_spec(refresh()))
    assert spec.input_attachments == []
    assert spec.environment == [] and spec.registry_credential_ref is None
    assert spec.network_mode == "bridge" and spec.egress_policy_ref == "feeds"
    assert [c.model_dump(by_alias=True) for c in spec.caches] == [
        {"cacheRef": "feeds", "target": feeds.FEED_CACHE_TARGET, "readOnly": False}
    ]
    imported = ContainerJobSpec.model_validate(
        refresh_spec(
            FeedRefresh(
                acquisition="import", feedCacheRef="feeds", archiveArtifactRef="art_x1"
            )
        )
    )
    assert imported.network_mode == "none" and imported.egress_policy_ref is None
    assert [a.artifact_ref for a in imported.input_attachments] == ["art_x1"]


@pytest.mark.parametrize(
    "override",
    [
        {"inputAttachments": [{"name": "source", "workspacePath": "."}]},
        {"inputAttachments": None},
        {"environment": [{"name": "GITHUB_TOKEN", "secretRef": "env://GITHUB_TOKEN"}]},
        {"environment": [{"name": "TRIVY_DB_REPOSITORY", "value": "evil.example/db"}]},
        {"command": ["-c", "import os"]},
        {"egressPolicyRef": "provider-egress"},
        {"caches": [{"cacheRef": "other", "target": "/opt/moonmind-scanner-db"}]},
        {"outputs": []},
        {"timeoutSeconds": 86400},
    ],
)
def test_refresh_job_rejects_source_credentials_and_redirection(override):
    spec = refresh_spec(refresh(), **override)
    if spec.get("inputAttachments") is None:
        spec.pop("inputAttachments")
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(spec)


def test_scanner_reads_feed_snapshot_read_only_without_network():
    spec = ContainerJobSpec.model_validate(
        {**build_scanner_job(scan("a" * 64)), "workspaceRef": WORKSPACE}
    )
    assert spec.network_mode == "none"
    assert [c.model_dump(by_alias=True) for c in spec.caches] == [
        {
            "cacheRef": "scanner-db",
            "target": "/opt/moonmind-scanner-db",
            "readOnly": True,
        }
    ]
    writable = {**build_scanner_job(scan("a" * 64)), "workspaceRef": WORKSPACE}
    writable["caches"] = [{**writable["caches"][0], "readOnly": False}]
    with pytest.raises(ValueError):
        ContainerJobSpec.model_validate(writable)


def _policy(*hosts):
    return json.dumps(
        [
            {
                "policyRef": "feeds",
                "allowedWorkflowIds": ["mm:feeds"],
                "allowedImageSourceRefs": ["repository-security"],
                "destinations": [
                    {
                        "host": h,
                        "purpose": "tool-data",
                        "protocol": "https",
                        "port": 443,
                    }
                    for h in hosts
                ],
            }
        ]
    )


def test_refresh_egress_admits_only_feed_hosts():
    submission = ContainerJobSubmitRequest.model_validate(
        {
            "idempotencyKey": "feeds",
            "source": {"source": "workflow", "workflowId": "mm:feeds"},
            "spec": refresh_spec(refresh()),
        }
    )
    settings = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": _policy(
                "ghcr.io", "pkg-containers.githubusercontent.com"
            )
        }
    )
    assert settings.authorize_egress(submission).policy_ref == "feeds"
    broad = resolve_container_backend_settings(
        {
            "MOONMIND_CONTAINER_BACKEND_EGRESS_POLICIES": _policy(
                "ghcr.io", "api.openai.com"
            )
        }
    )
    with pytest.raises(ValueError, match="feed hosts"):
        broad.authorize_egress(submission)
    assert feeds.feed_policy_admits(parse_egress_policies(_policy("ghcr.io"))[0])


@pytest.mark.asyncio
async def test_import_launch_mounts_no_workspace_and_only_the_feed_cache_writable(
    tmp_path,
):
    commands = []

    async def runner(args):
        commands.append(tuple(args))
        if args[:3] == ("inspect", "--format", "{{json .Config.Labels}}"):
            return 1, b"", b"no such container"
        return 0, b"", b""

    async def reader(_request, _ref):
        return b"archive"

    request = FeedRefresh(
        acquisition="import", feedCacheRef="feeds", archiveArtifactRef="art_x1"
    )
    workspace = tmp_path / "temporal_sandbox" / "run-1" / "repo"
    workspace.mkdir(parents=True)
    job_id = "container-job:0123456789abcdef0123456789abcdef"
    activity = ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": {
                "idempotencyKey": "feeds",
                "source": {"source": "workflow", "workflowId": "mm:feeds"},
                "spec": refresh_spec(request),
            },
            "resolvedWorkspaceRef": str(workspace),
            "resolvedImageRef": "sha256:" + "a" * 64,
        }
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path,
        command_runner=runner,
        artifact_reader=reader,
        settings=resolve_container_backend_settings(
            {
                CACHE_SOURCES_ENV_KEY: json.dumps(
                    [
                        {
                            "cacheRef": "feeds",
                            "volumeName": "scanner-feeds",
                            "target": feeds.FEED_CACHE_TARGET,
                            "readOnly": False,
                        },
                        {
                            "cacheRef": "scanner-db",
                            "volumeName": "scanner-feeds",
                            "target": "/opt/moonmind-scanner-db",
                            "readOnly": True,
                        },
                    ]
                )
            }
        ),
    )
    await backend.create_container(activity)
    create = next(c for c in commands if c[0] == "create")
    mounts = [create[i + 1] for i, item in enumerate(create) if item == "--mount"]
    assert not any("dst=/workspace" in m for m in mounts)
    assert next(m for m in mounts if "dst=/inputs/feed-archive" in m).endswith(
        ",readonly"
    )
    feed = next(m for m in mounts if f"dst={feeds.FEED_CACHE_TARGET}" in m)
    assert not feed.endswith(",readonly")
    assert create[create.index("--network") + 1] == "none"


# ------------------------------------------------ scanner execution record


@pytest.mark.parametrize("state", ["stale", "absent"])
def test_scanner_execution_records_actual_snapshot_or_missing_data(tmp_path, state):
    from moonmind.security.repository_scanner_runner import run_scanner

    root = tmp_path / "feeds"
    snapshot = publish(root, updated=datetime.now(UTC) - timedelta(days=30))
    source = tmp_path / "source"
    source.mkdir()
    (source / "requirements.txt").write_text("example==1.0\n")
    admitted = scan(snapshot.snapshot_id if state == "stale" else "e" * 64)
    output = tmp_path / "output"

    assert run_scanner(admitted, source=source, output=output, database=root) == 2

    execution = ScannerExecution.model_validate_json(
        (output / "execution.json").read_bytes()
    )
    if state == "stale":
        assert "feed_snapshot_stale" in execution.limitations
        assert execution.database_digest == snapshot.snapshot_id
        assert execution.feed.snapshot.snapshot_id == snapshot.snapshot_id
        assert execution.feed.stale_components == ["vulnerability"]
        assert execution.feed.checks == "embedded"
    else:
        assert {"feed_snapshot_unavailable", "scanner_prerequisite_failed"} <= set(
            execution.limitations
        )
        assert execution.database_digest is None and execution.feed is None
        assert execution.states == {"scan": "failed"}
