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
def test_portable_release_pins_source_and_preserves_checkout(tmp_path, monkeypatch, mismatch):
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
            assert "--submit" in args
            assert kwargs["env"]["MOONMIND_IMAGE"].endswith("@" + digest)
            output = ""
        else:
            assert args[1] == "pull"
            assert args[2].endswith(":sha-" + revision)
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(update.subprocess, "run", command)
    if mismatch:
        with pytest.raises(ValueError, match="source revision"):
            update.main(["--repo", str(repo)])
        assert not any("--submit" in item for item in commands)
    else:
        assert update.main(["--repo", str(repo)]) == 0
        submission = next((repo / "deploy/state/release-submissions").glob("*.json"))
        assert update.main(["--repo", str(repo), "--resume", submission.stem]) == 0
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
