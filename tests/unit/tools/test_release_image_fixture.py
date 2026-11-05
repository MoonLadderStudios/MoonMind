"""Exercise immutable archive transport and ownership-limited registry cleanup."""

from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from tools.ci import release_image_fixture as fixture

MANIFEST = b'{"schemaVersion":2,"manifests":[]}'
DIGEST = "sha256:" + hashlib.sha256(MANIFEST).hexdigest()
IMAGE = fixture.REPOSITORY + "@" + DIGEST
OWNER = "run-123/attempt-1/exact-artifact"


IDENTITY = dict(revision="a" * 40, upstream="b" * 40, run_id="123", attempt="2")


def _put_blob(root, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    path = root / "docker/registry/v2/blobs/sha256" / digest[:2] / digest / "data"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return "sha256:" + digest


def _push(root, name, layers, labels):
    """Write one image the way the job-local registry stores a docker push."""
    config = _put_blob(root, json.dumps({"config": {"Labels": labels}}).encode())
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": config, "size": 1},
        "layers": [{"digest": _put_blob(root, layer), "size": 1} for layer in layers],
    }
    digest = _put_blob(root, json.dumps(manifest).encode())
    link = (
        root
        / "docker/registry/v2/repositories"
        / name
        / "_manifests/revisions/sha256"
        / digest.removeprefix("sha256:")
        / "link"
    )
    link.parent.mkdir(parents=True, exist_ok=True)
    link.write_text(digest)
    return f"{fixture.RECOVERY_REGISTRY}/{name}@{digest}", config


class RecoveryDocker:
    """Docker CLI substitute serving the images pushed into the storage."""

    def __init__(self, root):
        upstream = {"org.opencontainers.image.revision": "b" * 40}
        shared = b"shared python runtime layer" * 64
        self.images = {}
        for name, layers, labels in (
            ("moonmind-test-omnigent-server", [shared, b"server layer"], upstream),
            (
                "moonmind-test-omnigent-host",
                [shared, b"host layer", b"host overlay"],
                {**upstream, "moonmind.source.revision": "a" * 40},
            ),
        ):
            ref, config = _push(root, name, layers, labels)
            self.images[name] = {
                "ref": ref,
                "Id": config,
                "RepoDigests": [ref],
                "Labels": dict(labels),
            }
        # The host build's base image is pushed too, but no consumer needs it.
        _push(
            root, "moonmind-test-omnigent-host-base", [shared, b"host layer"], upstream
        )
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[1] == "pull":
            return subprocess.CompletedProcess(args, 0, b"")
        assert args[1:3] == ["image", "inspect"], args
        name = args[3].rsplit("/", 1)[1].split(":")[0].split("@")[0]
        image = self.images[name]
        payload = {
            "Id": image["Id"],
            "RepoDigests": image["RepoDigests"],
            "Config": {"Labels": image["Labels"]},
        }
        return subprocess.CompletedProcess(args, 0, json.dumps([payload]).encode())


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    root = tmp_path / "registry"
    docker = RecoveryDocker(root)
    monkeypatch.setattr(fixture, "_run", docker)
    return root, tmp_path / "identity.json", docker


def test_saved_recovery_storage_binds_identity_and_measures_required_bytes(
    recovery, capsys
):
    root, state, docker = recovery
    fixture.save_recovery(root, state, **IDENTITY)
    saved = json.loads(state.read_text())
    assert saved["schema"] == "moonmind.recovery-images/v2"
    assert {
        key: saved[key] for key in ("revision", "upstream", "runId", "attempt")
    } == {"revision": "a" * 40, "upstream": "b" * 40, "runId": "123", "attempt": "2"}
    assert {name: image["ref"] for name, image in saved["images"].items()} == {
        name: image["ref"] for name, image in docker.images.items()
    }
    assert {name: image["id"] for name, image in saved["images"].items()} == {
        name: image["Id"] for name, image in docker.images.items()
    }
    sizes = {
        path.parent.name: path.stat().st_size
        for path in root.glob("docker/registry/v2/blobs/sha256/*/*/data")
    }

    def closure(ref):
        manifest = json.loads(
            next(
                root.glob(f"docker/registry/v2/blobs/sha256/*/{ref[-64:]}/data")
            ).read_bytes()
        )
        return {ref[-64:], manifest["config"]["digest"][7:]} | {
            layer["digest"][7:] for layer in manifest["layers"]
        }

    server, host = (closure(image["ref"]) for image in docker.images.values())
    assert saved["images"]["moonmind-test-omnigent-server"]["bytes"] == sum(
        sizes[blob] for blob in server
    )
    assert saved["images"]["moonmind-test-omnigent-host"]["bytes"] == sum(
        sizes[blob] for blob in host
    )
    assert saved["requiredBytes"] == sum(sizes[blob] for blob in server | host)
    assert saved["sharedBytes"] == sum(sizes[blob] for blob in server & host) > 0
    # Unneeded host-base metadata and repository links are measured separately.
    assert saved["storageBytes"] > saved["requiredBytes"]
    assert saved["storageDigest"].startswith("sha256:")
    notice = capsys.readouterr().out
    assert notice.startswith("::notice title=Recovery runtime bytes::")
    assert f"required={saved['requiredBytes']}" in notice
    assert not any(call[1] in {"save", "load", "pull"} for call in docker.calls)


@pytest.mark.parametrize(
    "damage", ["unpinned", "label", "source", "corrupt", "missing", "symlink", "run"]
)
def test_saving_rejects_unbound_or_corrupt_recovery_storage(recovery, damage):
    root, state, docker = recovery
    identity = dict(IDENTITY)
    host = docker.images["moonmind-test-omnigent-host"]
    if damage == "unpinned":
        host["RepoDigests"] = ["127.0.0.1:5001/elsewhere@" + host["ref"][-71:]]
    if damage == "label":
        host["Labels"]["org.opencontainers.image.revision"] = "c" * 40
    if damage == "source":
        host["Labels"]["moonmind.source.revision"] = "c" * 40
    layer = next(
        path
        for path in root.glob("docker/registry/v2/blobs/sha256/*/*/data")
        if path.read_bytes() == b"host overlay"
    )
    if damage == "corrupt":
        layer.write_bytes(b"different host overlay")
    if damage == "missing":
        layer.unlink()
    if damage == "symlink":
        (root / "docker/link").symlink_to(layer)
    if damage == "run":
        identity["run_id"] = "latest"
    with pytest.raises(fixture.FixtureError):
        fixture.save_recovery(root, state, **identity)
    assert not state.exists()


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "blob",
        "extra",
        "link",
        "source",
        "attempt",
        "schema",
        "ref",
        "content",
        "label",
    ],
)
def test_restored_recovery_storage_is_verified_before_any_pull(recovery, damage):
    root, state, docker = recovery
    fixture.save_recovery(root, state, **IDENTITY)
    docker.calls.clear()
    identity = dict(IDENTITY, artifact="recovery-images-123-2")
    saved = json.loads(state.read_text())
    host = docker.images["moonmind-test-omnigent-host"]
    if damage == "blob":
        layer = next(
            path
            for path in root.glob("docker/registry/v2/blobs/sha256/*/*/data")
            if path.read_bytes() == b"host overlay"
        )
        layer.write_bytes(b"replaced host overlay")
    if damage == "extra":
        _push(root, "moonmind-test-omnigent-host", [b"unrecorded"], {})
    if damage == "link":
        next(
            root.glob("docker/registry/v2/repositories/*/_manifests/**/link")
        ).write_text("sha256:" + "0" * 64)
    if damage == "source":
        identity["revision"] = "d" * 40
    if damage == "attempt":
        identity["artifact"] = "recovery-images-123-1"
    if damage == "schema":
        saved["schema"] = "moonmind.recovery-images/v1"
    if damage == "ref":
        saved["images"]["moonmind-test-omnigent-host"]["ref"] = (
            "ghcr.io/other/host@" + host["ref"][-71:]
        )
    state.write_text(json.dumps(saved))
    if damage == "content":
        host["Id"] = "sha256:" + "d" * 64
    if damage == "label":
        host["Labels"]["moonmind.source.revision"] = "d" * 40
    if damage is None:
        refs = fixture.restore_recovery(root, state, **identity)
        assert refs == {
            "server": docker.images["moonmind-test-omnigent-server"]["ref"],
            "host": host["ref"],
        }
        assert [call for call in docker.calls if call[1] == "pull"] == [
            ["docker", "pull", image["ref"]] for image in docker.images.values()
        ]
        return
    with pytest.raises(fixture.FixtureError):
        fixture.restore_recovery(root, state, **identity)
    if damage not in {"content", "label"}:
        assert docker.calls == []


def test_recovery_commands_emit_the_consumer_registry_refs(recovery, capsys):
    root, state, docker = recovery
    arguments = [
        "--registry",
        str(root),
        "--state",
        str(state),
        "--revision",
        "a" * 40,
        "--upstream",
        "b" * 40,
        "--run-id",
        "123",
        "--attempt",
        "2",
    ]
    assert fixture.main(["save-recovery", *arguments]) == 0
    capsys.readouterr()
    assert (
        fixture.main(
            ["restore-recovery", *arguments, "--artifact", "recovery-images-123-2"]
        )
        == 0
    )
    assert capsys.readouterr().out.splitlines() == [
        "server=" + docker.images["moonmind-test-omnigent-server"]["ref"],
        "host=" + docker.images["moonmind-test-omnigent-host"]["ref"],
    ]
    state.write_text("{}")
    assert fixture.main(["restore-recovery", *arguments]) == 1


class Commands:
    def __init__(self):
        self.calls = []
        self.present = False
        self.owner = OWNER
        self.manifest = MANIFEST
        self.failure = None

    def __call__(self, arguments):
        self.calls.append(arguments)
        prefix = arguments[:2]
        if prefix == ["docker", "run"]:
            self.present = True
        if prefix == ["docker", "inspect"]:
            if not self.present:
                raise subprocess.CalledProcessError(
                    1, arguments, stderr=b"Error: No such object: fixture"
                )
            return subprocess.CompletedProcess(
                arguments, 0, json.dumps({fixture.OWNER_LABEL: self.owner}).encode()
            )
        if prefix == ["docker", "rm"]:
            self.present = False
        if prefix == ["skopeo", "copy"] and self.failure:
            raise self.failure
        if prefix == ["skopeo", "inspect"]:
            return subprocess.CompletedProcess(arguments, 0, self.manifest)
        return subprocess.CompletedProcess(arguments, 0, b"")


@pytest.fixture
def transport(monkeypatch, tmp_path):
    commands = Commands()
    monkeypatch.setattr(fixture, "_run", commands)
    # No external service or Docker daemon is contacted by these unit tests.
    monkeypatch.setattr(fixture, "_wait_registry", lambda: None, raising=False)
    archive = tmp_path / "candidate.tar"
    archive.write_bytes(b"oci fixture")
    return commands, archive, tmp_path / "registry.json"


def test_description_hashes_the_manifest_selected_by_the_archive_transport(transport):
    commands, archive, _ = transport
    assert fixture.describe(archive) == {"image": IMAGE, "digest": DIGEST}
    assert commands.calls == [["skopeo", "inspect", "--raw", f"oci-archive:{archive}"]]


def test_restore_preserves_the_full_image_and_verifies_its_digest(transport):
    commands, archive, state = transport
    fixture.restore(archive, IMAGE, state, OWNER)
    saved = json.loads(state.read_text())
    assert saved["owner"] == OWNER
    assert saved["image"] == IMAGE
    assert commands.present
    copy = next(call for call in commands.calls if call[:2] == ["skopeo", "copy"])
    assert "--all" in copy and "--preserve-digests" in copy
    assert f"oci-archive:{archive}" in copy
    assert "docker://" + fixture.REPOSITORY + ":assembled" in copy
    assert any("docker://" + IMAGE in call for call in commands.calls)
    run = next(call for call in commands.calls if call[:2] == ["docker", "run"])
    assert "127.0.0.1:5001:5000" in run


@pytest.mark.parametrize(
    "image",
    [
        fixture.REPOSITORY + ":latest",
        "ghcr.io/fixture/app@" + DIGEST,
        fixture.REPOSITORY + "@sha256:short",
        IMAGE + "\n",
    ],
)
def test_restore_rejects_non_fixture_or_mutable_image_before_commands(transport, image):
    commands, archive, state = transport
    with pytest.raises(fixture.FixtureError):
        fixture.restore(archive, image, state, OWNER)
    assert commands.calls == []
    assert not state.exists()


def test_restore_rejects_an_archive_from_a_different_candidate(transport):
    commands, archive, state = transport
    with pytest.raises(fixture.FixtureError, match="digest"):
        fixture.restore(
            archive, fixture.REPOSITORY + "@sha256:" + "a" * 64, state, OWNER
        )
    assert not any(call[:2] == ["docker", "run"] for call in commands.calls)
    assert not state.exists()


def test_remote_digest_mismatch_cleans_up_only_the_owned_fixture(
    transport, monkeypatch
):
    commands, archive, state = transport
    original = commands.__call__

    def changed_registry(arguments):
        if arguments[:2] == ["skopeo", "inspect"] and arguments[-1].startswith(
            "docker:"
        ):
            return subprocess.CompletedProcess(arguments, 0, b"different manifest")
        return original(arguments)

    monkeypatch.setattr(fixture, "_run", changed_registry)
    with pytest.raises(fixture.FixtureError, match="digest"):
        fixture.restore(archive, IMAGE, state, OWNER)
    assert not state.exists()
    assert not commands.present


def test_copy_failure_preserves_the_original_error_and_cleans_its_registry(transport):
    commands, archive, state = transport
    failure = subprocess.CalledProcessError(
        1, ["skopeo", "copy"], stderr=b"copy failed"
    )
    commands.failure = failure
    with pytest.raises(subprocess.CalledProcessError) as caught:
        fixture.restore(archive, IMAGE, state, OWNER)
    assert caught.value is failure
    assert not commands.present
    assert not state.exists()


def test_cleanup_checks_state_owner_and_container_label_before_removal(transport):
    commands, archive, state = transport
    fixture.restore(archive, IMAGE, state, OWNER)
    with pytest.raises(fixture.FixtureError, match="owner"):
        fixture.cleanup(state, "other-run")
    assert commands.present
    commands.owner = "other-run"
    with pytest.raises(fixture.FixtureError, match="owner"):
        fixture.cleanup(state, OWNER)
    assert commands.present
    assert state.exists()
    assert not any(call[:2] == ["docker", "rm"] for call in commands.calls)


def test_cleanup_is_idempotent_after_confirmed_absence(transport):
    commands, archive, state = transport
    fixture.restore(archive, IMAGE, state, OWNER)
    commands.present = False
    fixture.cleanup(state, OWNER)
    fixture.cleanup(state, OWNER)
    assert not state.exists()


def test_cleanup_retains_state_when_docker_cannot_confirm_ownership(
    transport, monkeypatch
):
    commands, archive, state = transport
    fixture.restore(archive, IMAGE, state, OWNER)

    def unavailable(arguments):
        raise subprocess.CalledProcessError(
            1, arguments, stderr=b"Cannot connect to the Docker daemon"
        )

    monkeypatch.setattr(fixture, "_run", unavailable)
    with pytest.raises(subprocess.CalledProcessError):
        fixture.cleanup(state, OWNER)
    assert state.exists()
    assert commands.present


def test_existing_state_and_missing_archive_are_preserved(transport):
    commands, archive, state = transport
    state.write_text("saved owner state")
    with pytest.raises(fixture.FixtureError):
        fixture.restore(archive, IMAGE, state, OWNER)
    assert state.read_text() == "saved owner state"
    assert commands.calls == []
    state.unlink()
    archive.unlink()
    with pytest.raises(fixture.FixtureError):
        fixture.restore(archive, IMAGE, state, OWNER)
    assert commands.calls == []


@pytest.mark.parametrize("saved", [[], {"registry": "unrelated", "owner": OWNER}])
def test_cleanup_rejects_invalid_state_without_docker_commands(transport, saved):
    commands, _, state = transport
    state.write_text(json.dumps(saved))
    with pytest.raises(fixture.FixtureError):
        fixture.cleanup(state, OWNER)
    assert commands.calls == []
    assert state.exists()


def test_partial_registry_start_is_reconciled_after_docker_reports_failure(
    transport, monkeypatch
):
    commands, archive, state = transport
    failure = subprocess.CalledProcessError(
        1, ["docker", "run"], stderr=b"start failed"
    )

    def partially_started(arguments):
        result = commands(arguments)
        if arguments[:2] == ["docker", "run"]:
            raise failure
        return result

    monkeypatch.setattr(fixture, "_run", partially_started)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        fixture.restore(archive, IMAGE, state, OWNER)
    assert caught.value is failure
    assert not commands.present
    assert not state.exists()


def test_cancellation_cleans_the_fixture_and_preserves_the_interrupt(transport):
    commands, archive, state = transport
    cancellation = KeyboardInterrupt()
    commands.failure = cancellation
    with pytest.raises(KeyboardInterrupt) as caught:
        fixture.restore(archive, IMAGE, state, OWNER)
    assert caught.value is cancellation
    assert not commands.present
    assert not state.exists()


def test_cleanup_failure_retains_recovery_state_without_masking_copy_failure(
    transport, monkeypatch
):
    commands, archive, state = transport
    failure = subprocess.CalledProcessError(
        1, ["skopeo", "copy"], stderr=b"copy failed"
    )
    commands.failure = failure

    def lost_docker(arguments):
        if arguments[:2] == ["docker", "inspect"]:
            raise subprocess.CalledProcessError(
                1, arguments, stderr=b"Cannot connect to the Docker daemon"
            )
        return commands(arguments)

    monkeypatch.setattr(fixture, "_run", lost_docker)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        fixture.restore(archive, IMAGE, state, OWNER)
    assert caught.value is failure
    assert any("cleanup also failed" in note for note in failure.__notes__)
    assert commands.present
    assert state.exists()
