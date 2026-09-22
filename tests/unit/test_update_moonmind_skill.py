"""The portable updater selects one source/image without editing live source."""
from __future__ import annotations
import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("update_release_skill", ROOT / ".agents/skills/update-moonmind/scripts/update_release.py")
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)

@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.parametrize("operator_url", [None, "http://installed.example:7000"])
def test_portable_release_pins_source_and_preserves_checkout(tmp_path, monkeypatch, mismatch, operator_url):
    repo = tmp_path / "installed"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("init", "-b", "main")
    git("config", "user.email", "qualification@example.invalid")
    git("config", "user.name", "Qualification")
    (repo / "source.txt").write_text("committed source")
    git("add", ".")
    git("commit", "-m", "source")
    revision = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    (repo / "source.txt").write_text("operator edits")
    (repo / ".env").write_text("AUTH_PROVIDER=disabled\nMOONMIND_API_PUBLISH_HOST=192.0.2.4\n")
    original_run = subprocess.run
    commands = []
    digest = "sha256:" + "a" * 64
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        commands.append(args)
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps([{"RepoDigests": [f"ghcr.io/moonladderstudios/moonmind@{digest}"], "Config": {"Labels": {"org.opencontainers.image.revision": "different" if mismatch else revision}}}])
        elif args[1:3] == ["compose", "config"]:
            output = '{"name":"existing-project"}'
        elif args[1] == "run":
            output = "services: {}"
        elif args[1] == "compose":
            payload = json.loads(args[-1])
            assert payload["inputs"]["sourceRevision"] == revision
            assert payload["inputs"]["image"]["reference"] == digest
            assert payload["context"].get("deployment_operator_urls") == ([operator_url] if operator_url else None)
            assert "--submit" in args
            assert kwargs["env"]["MOONMIND_IMAGE"].endswith("@" + digest)
            # The updater reaches Docker through docker-proxy; host updates
            # must not recreate that substrate through itself.
            assert kwargs["env"]["MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES"] == "docker-proxy,sandbox-egress-proxy,postgres"
            assert "MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES=docker-proxy,sandbox-egress-proxy,postgres" in args
            output = ""
        else:
            assert args[1] == "pull"
            assert args[2].endswith(":sha-" + revision)
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(update.subprocess, "run", command)
    args = ["--repo", str(repo)] + (["--operator-url", operator_url] if operator_url else [])
    if mismatch:
        with pytest.raises(ValueError, match="source revision"):
            update.main(args)
        assert not any("--submit" in item for item in commands)
    else:
        assert update.main(args) == 0
        submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
        assert update.main(["--repo", str(repo), "--resume", submission.stem]) == 0
        with pytest.raises(ValueError, match="original operator URLs"):
            update.main(["--repo", str(repo), "--resume", submission.stem,
                         "--operator-url", "http://different.example:7000"])
        assert len([item for item in commands if item[1] == "pull"]) == 1
        assert len(list((repo / "deploy/state/release-submissions").glob("*.json"))) == 1
    assert (repo / "source.txt").read_text() == "operator edits"
    assert (repo / ".env").read_text() == "AUTH_PROVIDER=disabled\nMOONMIND_API_PUBLISH_HOST=192.0.2.4\n"
    assert git("rev-parse", "HEAD") == revision


def test_dry_run_never_fetches_or_launches(tmp_path, monkeypatch):
    calls = []
    def inspect(args, **kwargs):
        calls.append(args)
        return ""
    monkeypatch.setattr(update, "run", inspect)
    assert update.main(["--repo", str(tmp_path), "--dry-run"]) == 0
    assert calls == [["git", "check-ref-format", "--branch", "main"]]


@pytest.mark.parametrize(
    "rendered",
    [
        {"name": "existing-project", "services": {"api": {"ports": [{"host_ip": "0.0.0.0", "published": "7000", "target": 8000}]}}},
        {"name": "existing-project", "services": {"api": {"ports": [{"host_ip": "", "published": "7000", "target": 8000}]}}},
        {"name": "existing-project", "services": {"api": {"ports": [{"host_ip": "::", "published": "7000", "target": 8000}]}}},
        {"name": "existing-project", "services": {"api": {"ports": [{"host_ip": "192.0.2.4", "published": "7000", "target": 8000}]}}},
        {"name": "existing-project", "services": {"api": {"environment": {"MOONMIND_PUBLIC_BASE_URL": "https://auth.example"}, "ports": [{"host_ip": "0.0.0.0", "published": "7000", "target": 8000}]}}},
    ],
)
def test_bare_invocation_never_invents_operator_urls(tmp_path, monkeypatch, rendered):
    """A bare invocation records no operator URLs: wildcard bindings require
    an explicit --operator-url (or MOONMIND_PUBLIC_BASE_URL) so release
    probes validate the actual operator route instead of loopback."""
    repo = tmp_path / "installed"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("init", "-b", "main")
    git("config", "user.email", "qualification@example.invalid")
    git("config", "user.name", "Qualification")
    (repo / "source.txt").write_text("committed source")
    git("add", ".")
    git("commit", "-m", "source")
    revision = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    original_run = subprocess.run
    digest = "sha256:" + "b" * 64
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps([{"RepoDigests": [f"ghcr.io/moonladderstudios/moonmind@{digest}"], "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}])
        elif args[1:3] == ["compose", "config"]:
            output = json.dumps(rendered)
        elif args[1] == "run":
            output = "services: {}"
        elif args[1] == "compose":
            output = ""
        else:
            assert args[1] == "pull"
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(update.subprocess, "run", command)
    assert update.main(["--repo", str(repo)]) == 0
    submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
    payload = json.loads(submission.read_text())
    assert "deployment_operator_urls" not in payload["context"]
def _init_repo(path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()
    git("init", "-b", "main")
    git("config", "user.email", "qualification@example.invalid")
    git("config", "user.name", "Qualification")
    (path / "source.txt").write_text("working tree source")
    git("add", ".")
    git("commit", "-m", "source")
    return git


def test_local_build_dry_run_never_builds_or_launches(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _init_repo(repo)
    calls = []
    def inspect(args, **kwargs):
        calls.append(args)
        return ""
    monkeypatch.setattr(update, "run", inspect)
    assert update.main(["--repo", str(repo), "--local-build", "--dry-run"]) == 0
    assert calls, "local dry-run must inspect the working tree"
    assert all(args[0] == "git" for args in calls)
    assert "local_source_overlay_update" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [["--resume", "00000000-0000-0000-0000-000000000000"], ["--image-repository", "example.invalid/custom"]])
def test_local_build_rejects_release_inputs(tmp_path, extra):
    with pytest.raises(ValueError):
        update.main(["--repo", str(tmp_path), "--local-build", *extra])


@pytest.mark.parametrize(
    ("daemon_output", "hint"),
    [
        (
            "Error response from daemon: manifest for ghcr.io/moonladderstudios/moonmind:sha-abc123 not found: manifest unknown",
            "no published image",
        ),
        (
            'Error response from daemon: Head "https://ghcr.io/v2/x/manifests/sha-abc": unauthorized: authentication required',
            "docker login ghcr.io",
        ),
        (
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
            "Docker daemon is unreachable",
        ),
    ],
)
def test_run_reports_actionable_docker_pull_diagnostics(tmp_path, monkeypatch, daemon_output, hint):
    def failing(args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr=daemon_output)
    monkeypatch.setattr(update.subprocess, "run", failing)
    with pytest.raises(RuntimeError) as excinfo:
        update.run(["docker", "pull", "ghcr.io/moonladderstudios/moonmind:sha-abc123"], cwd=tmp_path)
    message = str(excinfo.value)
    assert "docker pull failed (exit 1)" in message
    assert "ghcr.io/moonladderstudios/moonmind:sha-abc123" in message
    assert daemon_output in message
    assert hint in message
    assert "deployment remains owned by its recorded release job" in message


def test_run_redacts_credentials_in_diagnostics(tmp_path, monkeypatch):
    def failing(args, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Head https://user:s3cr3t-token@ghcr.io/v2/x: unauthorized token=abcdef123456",
        )
    monkeypatch.setattr(update.subprocess, "run", failing)
    with pytest.raises(RuntimeError) as excinfo:
        update.run(["docker", "pull", "ghcr.io/moonladderstudios/moonmind:sha-abc123"], cwd=tmp_path)
    message = str(excinfo.value)
    assert "s3cr3t-token" not in message
    assert "***@" in message
    assert "token=***" in message


def test_main_pull_failure_names_branch_revision_and_image(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    git = _init_repo(repo)
    revision = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    original_run = subprocess.run
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        assert args[1] == "pull"
        return SimpleNamespace(returncode=1, stdout="", stderr="manifest unknown")
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: None)
    monkeypatch.setattr(update, "_PULL_RETRY_MAX_ATTEMPTS", 2)
    with pytest.raises(RuntimeError) as excinfo:
        update.main(["--repo", str(repo)])
    message = str(excinfo.value)
    assert revision in message
    assert f":sha-{revision}" in message
    assert "origin/main" in message


def _pull_failure(stderr):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def _init_two_commit_repo(repo):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("init", "-b", "main")
    git("config", "user.email", "qualification@example.invalid")
    git("config", "user.name", "Qualification")
    (repo / "source.txt").write_text("v1")
    git("add", ".")
    git("commit", "-m", "first")
    parent = git("rev-parse", "HEAD")
    (repo / "source.txt").write_text("v2")
    git("add", ".")
    git("commit", "-m", "second")
    tip = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    return parent, tip


@pytest.mark.parametrize(
    ("daemon_output", "category"),
    [
        (
            "Error response from daemon: manifest for ghcr.io/moonladderstudios/moonmind:sha-abc not found: manifest unknown",
            "unpublished",
        ),
        (
            'Error response from daemon: Head "https://ghcr.io/v2/x": unauthorized: authentication required',
            "auth",
        ),
        (
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
            "daemon",
        ),
        ("Error: something entirely unexpected", "unknown"),
    ],
)
def test_run_marks_docker_pull_failure_category(tmp_path, monkeypatch, daemon_output, category):
    monkeypatch.setattr(
        update.subprocess, "run", lambda *args, **kwargs: _pull_failure(daemon_output)
    )
    with pytest.raises(update.DockerPullError) as excinfo:
        update.run(["docker", "pull", "ghcr.io/moonladderstudios/moonmind:sha-abc"], cwd=tmp_path)
    assert excinfo.value.category == category


def test_select_waits_for_inflight_tip_publish_before_any_fallback(tmp_path, monkeypatch, capsys):
    """A late tip publish is waited out, so no ancestor is substituted."""
    repo = tmp_path / "installed"
    repo.mkdir()
    parent, tip = _init_two_commit_repo(repo)
    original_run = subprocess.run
    pulls = []
    responses = iter([_pull_failure("manifest unknown"), SimpleNamespace(returncode=0, stdout="")])
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        assert args[1] == "pull"
        pulls.append(args[2])
        return next(responses)
    sleeps = []
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: sleeps.append(seconds))
    revision, image, skipped = update._select_release_image(
        repo=repo,
        branch="main",
        tip_revision=tip,
        image_repository="ghcr.io/moonladderstudios/moonmind",
    )
    assert revision == tip
    assert image.endswith(f":sha-{tip}")
    assert skipped == []
    assert pulls == [f"ghcr.io/moonladderstudios/moonmind:sha-{tip}"] * 2
    assert parent not in "".join(pulls)
    assert sleeps == [update._PULL_RETRY_INTERVAL_SECONDS]
    assert "waiting" in capsys.readouterr().out


def test_select_falls_back_to_ancestor_only_after_the_tip_wait(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    parent, tip = _init_two_commit_repo(repo)
    original_run = subprocess.run
    pulls = []
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        pulls.append(args[2])
        if args[2].endswith(tip):
            return _pull_failure("manifest unknown")
        return SimpleNamespace(returncode=0, stdout="pulled")
    sleeps = []
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(update, "_PULL_RETRY_MAX_ATTEMPTS", 3)
    revision, image, skipped = update._select_release_image(
        repo=repo,
        branch="main",
        tip_revision=tip,
        image_repository="ghcr.io/moonladderstudios/moonmind",
    )
    assert revision == parent
    assert image.endswith(f":sha-{parent}")
    assert skipped == [tip]
    # The tip exhausts its bounded wait; the ancestor is pulled once, not waited on.
    assert pulls == [f"ghcr.io/moonladderstudios/moonmind:sha-{tip}"] * 3 + [
        f"ghcr.io/moonladderstudios/moonmind:sha-{parent}"
    ]
    assert sleeps == [update._PULL_RETRY_INTERVAL_SECONDS] * 2


def test_select_exhaustion_keeps_branch_and_tip_context(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    parent, tip = _init_two_commit_repo(repo)
    original_run = subprocess.run
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        return _pull_failure("Error response from daemon: manifest unknown")
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: None)
    monkeypatch.setattr(update, "_PULL_RETRY_MAX_ATTEMPTS", 2)
    with pytest.raises(RuntimeError) as excinfo:
        update._select_release_image(
            repo=repo,
            branch="main",
            tip_revision=tip,
            image_repository="ghcr.io/moonladderstudios/moonmind",
        )
    message = str(excinfo.value)
    assert tip in message
    assert f":sha-{tip}" in message
    assert parent in message
    assert "origin/main" in message
    assert "manifest unknown" in message


def test_select_auth_failure_fails_fast_without_wait_or_fallback(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    _parent, tip = _init_two_commit_repo(repo)
    original_run = subprocess.run
    pulls = []
    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        pulls.append(args[2])
        return _pull_failure("unauthorized: authentication required")
    sleeps = []
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: sleeps.append(seconds))
    with pytest.raises(RuntimeError, match="docker login"):
        update._select_release_image(
            repo=repo,
            branch="main",
            tip_revision=tip,
            image_repository="ghcr.io/moonladderstudios/moonmind",
        )
    assert len(pulls) == 1
    assert sleeps == []


def test_main_records_the_published_ancestor_and_requested_tip(tmp_path, monkeypatch):
    """Default update uses newest published ancestor when the tip stays unpublished."""
    repo = tmp_path / "installed"
    repo.mkdir()
    parent, tip = _init_two_commit_repo(repo)
    assert parent != tip
    original_run = subprocess.run
    digest = "sha256:" + "b" * 64
    pulls = []

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        if args[1] == "pull":
            pulls.append(args[2])
            if args[2].endswith(tip):
                return SimpleNamespace(returncode=1, stdout="", stderr="manifest unknown")
            assert args[2].endswith(parent)
            return SimpleNamespace(returncode=0, stdout="pulled")
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps(
                [
                    {
                        "RepoDigests": [f"ghcr.io/moonladderstudios/moonmind@{digest}"],
                        "Config": {"Labels": {"org.opencontainers.image.revision": parent}},
                    }
                ]
            )
            return SimpleNamespace(returncode=0, stdout=output)
        if args[1:3] == ["compose", "config"]:
            return SimpleNamespace(returncode=0, stdout='{"name":"existing-project"}')
        if args[1] == "run":
            return SimpleNamespace(returncode=0, stdout="services: {}")
        if args[1] == "compose":
            payload = json.loads(args[-1])
            assert payload["inputs"]["sourceRevision"] == parent
            assert payload["inputs"]["requestedTipRevision"] == tip
            assert payload["inputs"]["skippedUnpublishedRevisions"] == [tip]
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected docker command: {args}")

    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: None)
    monkeypatch.setattr(update, "_PULL_RETRY_MAX_ATTEMPTS", 2)
    assert update.main(["--repo", str(repo)]) == 0
    assert pulls[0].endswith(tip)
    assert pulls[-1].endswith(parent)
    submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
    record = json.loads(submission.read_text())
    assert record["inputs"]["sourceRevision"] == parent
    assert record["inputs"]["requestedTipRevision"] == tip


def test_unpublished_tip_does_not_mask_auth_failure(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    _init_two_commit_repo(repo)
    original_run = subprocess.run
    pulls = []

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        assert args[1] == "pull"
        pulls.append(args[2])
        return SimpleNamespace(
            returncode=1, stdout="", stderr="unauthorized: authentication required"
        )

    monkeypatch.setattr(update.subprocess, "run", command)
    with pytest.raises(RuntimeError, match="docker login"):
        update.main(["--repo", str(repo)])
    assert len(pulls) == 1


def test_host_submit_records_shared_controller_operation(tmp_path, monkeypatch):
    """The host update is a client of the same controller operation the
    Operations UI observes (MoonMind#4502): the submission links the durable
    operation, and the server-side client reads the identical record."""
    from api_service.services.deployment_controller import DeploymentControllerClient

    repo = tmp_path / "installed"
    repo.mkdir()
    git = _init_repo(repo)
    revision = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    original_run = subprocess.run
    digest = "sha256:" + "c" * 64

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps([{"RepoDigests": [f"ghcr.io/moonladderstudios/moonmind@{digest}"], "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}])
        elif args[1:3] == ["compose", "config"]:
            output = '{"name":"existing-project"}'
        elif args[1] in ("run", "compose", "pull"):
            output = "services: {}" if args[1] == "run" else ""
        else:
            raise AssertionError(f"unexpected docker command: {args}")
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(update.subprocess, "run", command)
    assert update.main(["--repo", str(repo)]) == 0

    submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
    record = json.loads(submission.read_text())
    operation_id = record["controllerOperationId"]
    assert operation_id.startswith("depupd_")

    # The same file is the server-side controller operation: one identity.
    client = DeploymentControllerClient(
        state_dir=repo / "deploy" / "state" / "update-operations"
    )
    observed = client.observe(operation_id)
    assert observed.status == "SUCCEEDED"
    assert observed.requested_image == f"ghcr.io/moonladderstudios/moonmind@{digest}"
    assert observed.reference == digest
    assert client.mutation_owner_count(operation_id) == 1


def test_host_resume_after_failure_starts_bounded_attempt(tmp_path, monkeypatch):
    repo = tmp_path / "installed"
    repo.mkdir()
    git = _init_repo(repo)
    revision = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    original_run = subprocess.run
    digest = "sha256:" + "d" * 64

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps([{"RepoDigests": [f"ghcr.io/moonladderstudios/moonmind@{digest}"], "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}])
        elif args[1:3] == ["compose", "config"]:
            output = '{"name":"existing-project"}'
        elif args[1] in ("run", "pull"):
            output = ""
        elif args[1] == "compose":
            return SimpleNamespace(returncode=1, stdout="adapter failed")
        else:
            raise AssertionError(f"unexpected docker command: {args}")
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(update.subprocess, "run", command)
    assert update.main(["--repo", str(repo)]) == 1
    submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
    first_id = json.loads(submission.read_text())["controllerOperationId"]
    state_dir = repo / "deploy" / "state" / "update-operations"
    first = json.loads((state_dir / f"{first_id}.json").read_text())
    assert first["status"] == "FAILED"
    assert first["error"] == "host adapter exited with status 1"

    # Resume after a terminal operation requests a fresh bounded attempt that
    # preserves the first failure instead of reusing the failed record.
    assert update.main(["--repo", str(repo), "--resume", submission.stem]) == 1
    second_id = json.loads(submission.read_text())["controllerOperationId"]
    assert second_id != first_id
    second = json.loads((state_dir / f"{second_id}.json").read_text())
    assert second["retryOf"] == first_id
    assert second["attempt"] == 2
    assert second["firstError"] == "host adapter exited with status 1"
    assert len(list((repo / "deploy/state/release-submissions").glob("*.json"))) == 1


def test_host_duplicate_submit_reattaches_to_live_operation(tmp_path):
    state_dir = tmp_path / "operations"
    intent = {
        "stack": "moonmind",
        "repository": "ghcr.io/moonladderstudios/moonmind",
        "reference": "stable",
        "mode": "changed_services",
        "operation_kind": "update",
        "reason": "routine update",
    }
    first, created_first = update._controller_submit(state_dir, intent=intent)
    second, created_second = update._controller_submit(state_dir, intent=intent)

    assert created_first is True
    assert created_second is False
    assert second["operationId"] == first["operationId"]
    assert len(list(state_dir.glob("depupd_*.json"))) == 1


def test_host_dry_run_writes_no_controller_operation(tmp_path, monkeypatch):
    calls = []

    def inspect(args, **kwargs):
        calls.append(args)
        return ""

    monkeypatch.setattr(update, "run", inspect)
    assert update.main(["--repo", str(tmp_path), "--dry-run"]) == 0
    assert not (tmp_path / "deploy" / "state" / "update-operations").exists()


def test_host_retry_operation_without_submission_fails_truthfully(tmp_path):
    with pytest.raises(ValueError, match="no recorded host submission"):
        update.main(
            ["--repo", str(tmp_path), "--retry-operation", "depupd_missing0001"]
        )


def test_host_retry_exhaustion_is_explicit(tmp_path):
    state_dir = tmp_path / "operations"
    record, _ = update._controller_submit(
        state_dir,
        intent={
            "stack": "moonmind",
            "repository": "ghcr.io/moonladderstudios/moonmind",
            "reference": "stable",
            "mode": "changed_services",
            "operation_kind": "update",
            "reason": "exhaustion probe",
        },
    )
    update._controller_record_result(
        state_dir, record["operationId"], status="FAILED", error="boom"
    )
    second, _ = update._controller_retry(state_dir, record["operationId"])
    update._controller_record_result(
        state_dir, second["operationId"], status="FAILED", error="boom"
    )
    third, _ = update._controller_retry(state_dir, second["operationId"])
    update._controller_record_result(
        state_dir, third["operationId"], status="FAILED", error="boom"
    )
    with pytest.raises(ValueError, match="bounded retry budget"):
        update._controller_retry(state_dir, third["operationId"])
