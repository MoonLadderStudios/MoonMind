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
