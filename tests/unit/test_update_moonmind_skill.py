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
