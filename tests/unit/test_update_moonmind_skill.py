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
    image = f"ghcr.io/moonladderstudios/moonmind@{digest}"
    posted = []

    class FakeResponse:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if request.method == "POST" and url.endswith("/v1/operations"):
            payload = json.loads(request.data.decode("utf-8"))
            assert payload["stack"] == "moonmind"
            assert payload["desiredImage"] == image
            assert payload["sourceRevision"] == revision
            assert payload["target"]["project"] == "existing-project"
            assert payload["target"]["projectDir"] == str(repo)
            assert "docker-compose.yaml" in payload["target"]["composeFiles"]
            # The standalone controller owns execution over its own transport;
            # proxy substrate is never recreated through the update.
            assert "docker-proxy" not in payload["target"]["services"]
            assert "sandbox-egress-proxy" not in payload["target"]["services"]
            assert request.get_header("Authorization") == "Bearer test-secret"
            posted.append(payload)
            return FakeResponse(202, {"operationId": "op-1", "status": "pending"})
        assert request.method == "GET" and url.endswith("/v1/operations/op-1")
        assert request.get_header("Authorization") == "Bearer test-secret"
        return FakeResponse(
            200,
            {"operationId": "op-1", "status": "succeeded", "installed": {"image": image}},
        )

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        commands.append(args)
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps([{"RepoDigests": [f"ghcr.io/moonladderstudios/moonmind@{digest}"], "Config": {"Labels": {"org.opencontainers.image.revision": "different" if mismatch else revision}}}])
        elif args[1:3] == ["compose", "config"]:
            output = json.dumps({"name": "existing-project", "services": {"api": {}, "worker": {}, "docker-proxy": {}}})
        else:
            assert args[1] == "pull"
            assert args[2].endswith(":sha-" + revision)
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
    secret_file = repo / "deploy" / "state" / "controller" / "secrets" / "controller-bearer"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text("test-secret\n")
    args = ["--repo", str(repo)] + (["--operator-url", operator_url] if operator_url else [])
    if mismatch:
        with pytest.raises(ValueError, match="source revision"):
            update.main(args)
        assert posted == []
    else:
        assert update.main(args) == 0
        assert len(posted) == 1
        # The standalone controller owns execution: no application-owned
        # updater container is launched from the target image.
        assert not any(item[1] == "run" for item in commands)
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


def test_submit_via_controller_requires_an_installed_controller(tmp_path):
    record = {"project": "existing-project", "image": "img", "inputs": {}}
    with pytest.raises(RuntimeError, match="Controller secret is missing"):
        update._submit_via_controller(
            record, tmp_path, controller_url="http://127.0.0.1:9", secret_file=None
        )


def test_bare_invocation_without_installed_controller_uses_application_updater(
    tmp_path, monkeypatch, capsys
):
    """The standalone controller is opt-in until it is installed: a bare
    invocation on a deployment without its secret still installs the release
    through the application-owned updater instead of refusing."""
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)
    repo = tmp_path / "installed"
    repo.mkdir()
    git = _init_repo(repo)
    revision = git("rev-parse", "HEAD")
    git("remote", "add", "origin", str(repo))
    original_run = subprocess.run
    digest = "sha256:" + "c" * 64
    image = f"ghcr.io/moonladderstudios/moonmind@{digest}"
    launched = []

    def command(args, **kwargs):
        if args[0] != "docker":
            return original_run(args, **kwargs)
        if args[1:3] == ["image", "inspect"]:
            output = json.dumps([{"RepoDigests": [image], "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}])
        elif args[1:3] == ["compose", "config"]:
            output = json.dumps({"name": "existing-project", "services": {"api": {}}})
        elif args[1] == "run":
            output = "services: {}"
        elif args[1] == "compose":
            launched.append(args)
            output = ""
        else:
            assert args[1] == "pull"
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)

    def no_controller(request, timeout=None):
        raise AssertionError("an uninstalled controller must not be contacted")

    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update.urllib.request, "urlopen", no_controller)
    assert update.main(["--repo", str(repo)]) == 0
    assert len(launched) == 1
    assert "moonmind.workflows.skills.deployment_release" in launched[0]
    assert "--project-name" in launched[0] and "existing-project" in launched[0]
    assert "controller is not installed" in capsys.readouterr().out


def test_explicit_controller_secret_file_must_exist(tmp_path, monkeypatch):
    """An explicitly selected controller is never silently bypassed."""
    record = {"project": "existing-project", "image": "img", "inputs": {}, "context": {}}
    monkeypatch.setattr(update, "_submit_legacy_direct", lambda *a, **k: pytest.fail("fallback"))
    with pytest.raises(RuntimeError, match="Controller secret is missing"):
        update._submit_release(
            record,
            tmp_path,
            controller_url="http://127.0.0.1:9",
            secret_file=str(tmp_path / "missing-secret"),
            legacy_direct=False,
        )


def _install_controller_secret(repo, secret="test-secret"):
    path = repo / "deploy" / "state" / "controller" / "secrets" / "controller-bearer"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret + "\n")
    return secret


def _stub_controller_success(monkeypatch, image):
    """Route controller HTTP calls to an immediately succeeding operation."""
    posted = []

    class FakeResponse:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        if request.method == "POST":
            posted.append(json.loads(request.data.decode("utf-8")))
            return FakeResponse(202, {"operationId": "op-1", "status": "pending"})
        return FakeResponse(
            200,
            {"operationId": "op-1", "status": "succeeded", "installed": {"image": image}},
        )

    monkeypatch.setattr(update.urllib.request, "urlopen", fake_urlopen)
    return posted


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
    _install_controller_secret(repo)
    _stub_controller_success(monkeypatch, f"ghcr.io/moonladderstudios/moonmind@{digest}")
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
            return SimpleNamespace(returncode=0, stdout='{"name":"existing-project","services":{"api":{}}}')
        if args[1] == "run":
            return SimpleNamespace(returncode=0, stdout="services: {}")
        raise AssertionError(f"unexpected docker command: {args}")

    monkeypatch.setattr(update.subprocess, "run", command)
    monkeypatch.setattr(update, "_sleep", lambda seconds: None)
    monkeypatch.setattr(update, "_PULL_RETRY_MAX_ATTEMPTS", 2)
    _install_controller_secret(repo)
    posted = _stub_controller_success(monkeypatch, f"ghcr.io/moonladderstudios/moonmind@{digest}")
    assert update.main(["--repo", str(repo)]) == 0
    assert posted[0]["sourceRevision"] == parent
    assert posted[0]["reason"] == "Update to selected branch snapshot"
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


def test_resolve_compose_files_defaults_to_base_plus_override(tmp_path, monkeypatch):
    monkeypatch.delenv("COMPOSE_FILE", raising=False)
    assert update._resolve_compose_files(tmp_path) == ["docker-compose.yaml"]
    (tmp_path / "docker-compose.override.yaml").write_text("services: {}\n")
    assert update._resolve_compose_files(tmp_path) == [
        "docker-compose.yaml",
        "docker-compose.override.yaml",
    ]


def test_resolve_compose_files_honors_deployment_selection(tmp_path, monkeypatch):
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
    (tmp_path / "site.yaml").write_text("services: {}\n")
    monkeypatch.setenv("COMPOSE_FILE", "docker-compose.yaml:site.yaml")
    assert update._resolve_compose_files(tmp_path) == [
        "docker-compose.yaml",
        "site.yaml",
    ]


def test_resolve_compose_files_rejects_missing_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", "docker-compose.yaml:missing.yaml")
    with pytest.raises(RuntimeError, match="does not exist"):
        update._resolve_compose_files(tmp_path)


def test_submit_via_controller_passes_resolved_file_set_and_idempotency(
    tmp_path, monkeypatch
):
    repo = tmp_path
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "site.yaml").write_text("services: {}\n")
    (repo / ".env").write_text("AUTH_PROVIDER=disabled\n")
    monkeypatch.setenv("COMPOSE_FILE", "docker-compose.yaml:site.yaml")
    _install_controller_secret(repo)
    image = "ghcr.io/moonladderstudios/moonmind@sha256:" + "b" * 64
    posted = _stub_controller_success(monkeypatch, image)
    monkeypatch.setattr(
        update,
        "run",
        lambda args, **kwargs: json.dumps(
            {"name": "existing-project", "services": {"api": {}}}
        ),
    )
    record = {
        "project": "existing-project",
        "image": image,
        "inputs": {"sourceRevision": "rev1", "reason": "test"},
        "context": {
            "idempotency_key": "host-update:sub-1",
            "deployment_operator_urls": ["http://installed.example:7000"],
        },
        "submissionId": "sub-1",
    }
    assert (
        update._submit_via_controller(
            record, repo, controller_url="http://127.0.0.1:8472", secret_file=None
        )
        == 0
    )
    target = posted[0]["target"]
    assert target["composeFiles"] == ["docker-compose.yaml", "site.yaml"]
    assert target["envFile"] == str(repo / ".env")
    assert target["operatorUrls"] == ["http://installed.example:7000"]
    assert target["idempotencyKey"] == "host-update:sub-1"


def test_fallback_notice_does_not_log_secret_path(tmp_path, monkeypatch, capsys):
    """CodeQL clear-text logging: the fallback notice must not log secrets."""
    record = {"project": "existing-project", "image": "img", "inputs": {}, "context": {}}
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)
    monkeypatch.setattr(update, "_submit_legacy_direct", lambda *args, **kwargs: 0)
    assert (
        update._submit_release(
            record,
            tmp_path,
            controller_url="http://127.0.0.1:9",
            secret_file=None,
            legacy_direct=False,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "controller is not installed" in out
    assert "controller-bearer" not in out
    assert "secret" not in out.lower()


def test_resume_without_controller_refuses_automatic_legacy_fallback(
    tmp_path, monkeypatch
):
    """A resumed submission must not silently fork the legacy updater.

    When a submission was originally handed to the controller, a bare
    `--resume` re-enters with no explicit secret and takes the legacy
    fallback while the controller operation may still be running, creating
    two deployment writers. Refuse the automatic fallback on resume until
    controller ownership is reconciled.
    """
    repo = tmp_path / "installed"
    repo.mkdir()
    submissions = repo / "deploy" / "state" / "release-submissions"
    submissions.mkdir(parents=True)
    submission_id = "00000000-0000-0000-0000-000000000000"
    record = {
        "repo": str(repo),
        "project": "existing-project",
        "image": "img",
        "inputs": {},
        "context": {},
    }
    (submissions / f"{submission_id}.json").write_text(json.dumps(record))
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET_FILE", raising=False)
    monkeypatch.setattr(
        update, "_submit_legacy_direct", lambda *args, **kwargs: pytest.fail("fallback")
    )
    with pytest.raises(RuntimeError, match="[Rr]esume"):
        update.main(["--repo", str(repo), "--resume", submission_id])


def test_legacy_direct_propagates_compose_file_selection(tmp_path, monkeypatch):
    """The legacy fallback must use the deployment's selected Compose files.

    When the deployment uses COMPOSE_FILE to select site-specific files, the
    fallback must propagate that same file set instead of only the base file
    plus a conventional override; otherwise reconciliation can omit custom
    services and `--remove-orphans` may remove them.
    """
    repo = tmp_path / "installed"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "site.yaml").write_text("services: {}\n")
    monkeypatch.setenv("COMPOSE_FILE", "docker-compose.yaml:site.yaml")
    launched = []

    def fake_run(args, **kwargs):
        if args[0] == "docker" and len(args) > 1 and args[1] == "run":
            return "services: {}\n"
        raise AssertionError(f"unexpected host docker command: {args}")

    original_subprocess_run = subprocess.run

    def fake_subprocess_run(command, **kwargs):
        launched.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(update, "run", fake_run)
    monkeypatch.setattr(update.subprocess, "run", fake_subprocess_run)
    record = {
        "project": "existing-project",
        "image": "ghcr.io/moonladderstudios/moonmind@sha256:" + "d" * 64,
        "inputs": {"sourceRevision": "rev", "reason": "test"},
        "context": {},
    }
    assert update._submit_legacy_direct(record, repo) == 0
    assert len(launched) == 1
    command = [str(part) for part in launched[0]]
    assert str(repo / "site.yaml") in command
