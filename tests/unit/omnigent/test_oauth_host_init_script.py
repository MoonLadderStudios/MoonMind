"""Execute OAuth initialization without mounting or changing real credentials."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "services/omnigent/scripts/init-oauth-host.sh"
DATA_ROOTS = ("/home/app/.omnigent", "/artifacts", "/home/app/.cache")

# Source the unchanged POSIX script in /bin/sh. Redirect filesystem predicates
# and commands into tmp_path, keeping the production path validation intact.
SHELL = """
sandbox_test() {
  case "$1" in
    '!') shift; ! sandbox_test "$@" ;;
    -d|-L) command test "$1" "$SANDBOX$2" ;;
    *) command [ "$@" ;;
  esac
}
alias [=sandbox_test
. "$1"
"""
COMMAND = """
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["SANDBOX"])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "commands.jsonl").open("a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
if os.environ.get("FAIL_COMMAND") == name:
    print(f"{name} failed", file=sys.stderr)
    sys.exit(42)
path = root / args[-1].lstrip("/")
if name == "mkdir":
    assert args[0] == "-p" and len(args) == 2
    path.mkdir(parents=True, exist_ok=True)
elif name == "chmod":
    assert len(args) == 2
    path.chmod(int(args[0], 8))
elif name == "chown":
    # Verify the requested ownership without requiring privileged tests.
    assert len(args) == 2 and path.is_dir()
elif name == "stat":
    assert args[:2] == ["-c", "%u:%g"] and path.is_dir()
    print(os.environ.get("OAUTH_OWNER", "1000:1000"))
else:
    raise AssertionError(name)
"""


class InitializerSandbox:
    def __init__(self, root: Path, home: str):
        self.root = root
        self.home = home
        self.bin = root / "bin"
        self.bin.mkdir()
        for command in ("mkdir", "chmod", "chown", "stat"):
            shim = self.bin / command
            shim.write_text(f"#!{sys.executable}\n{COMMAND}")
            shim.chmod(0o755)
        oauth = self.path(home)
        oauth.mkdir(parents=True)
        oauth.chmod(0o750)
        (oauth / "auth.json").write_bytes(b'{"fixture": "not-a-credential"}\n')
        (oauth / "auth.json").chmod(0o640)
        (oauth / ".credential-generation").write_bytes(b"7\n")
        (oauth / ".credential-generation").chmod(0o600)

    def path(self, path: str) -> Path:
        return self.root / path.lstrip("/")

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", "-c", SHELL, "oauth-init-test", str(SCRIPT)],
            env={
                "PATH": str(self.bin),
                "SANDBOX": str(self.root),
                "OAUTH_HOME": self.home,
                **overrides,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def commands(self) -> list[list[str]]:
        log = self.root / "commands.jsonl"
        return (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else None,
            stat.S_IMODE(path.stat().st_mode),
            path.stat().st_uid,
            path.stat().st_gid,
            path.stat().st_mtime_ns,
        )
        for path in (root, *sorted(root.rglob("*")))
    }


@pytest.fixture(params=("/home/app/.codex", "/home/app/.claude"))
def sandbox(tmp_path: Path, request: pytest.FixtureRequest) -> InitializerSandbox:
    return InitializerSandbox(tmp_path, request.param)


@pytest.mark.parametrize("existing", (False, True))
def test_initialization_preserves_oauth_and_existing_data_and_is_idempotent(
    sandbox: InitializerSandbox, existing: bool
) -> None:
    oauth_before = snapshot(sandbox.path(sandbox.home))
    if existing:
        for root in DATA_ROOTS:
            sandbox.path(root).mkdir(parents=True)
            sandbox.path(root + "/saved-work").write_bytes(b"retained")
            sandbox.path(root + "/saved-work").chmod(0o640)
        generation = sandbox.path("/home/app/.omnigent/credential-generation")
        generation.write_bytes(b"7\n")
        generation.chmod(0o600)

    environment = (
        dict(
            zip(
                ("OMNIGENT_STATE_PATH", "OMNIGENT_ARTIFACTS_PATH", "XDG_CACHE_HOME"),
                DATA_ROOTS,
            )
        )
        if existing
        else {}
    )
    first = sandbox.run(**environment)
    assert first.returncode == 0, first.stderr
    expected = [
        command
        for root in DATA_ROOTS
        for command in (
            ["mkdir", "-p", root],
            ["chown", "1000:1000", root],
            ["chmod", "700", root],
        )
    ] + [["stat", "-c", "%u:%g", sandbox.home]]
    assert sandbox.commands() == expected
    for root in DATA_ROOTS:
        assert stat.S_IMODE(sandbox.path(root).stat().st_mode) == 0o700
        if existing:
            saved = sandbox.path(root + "/saved-work")
            assert saved.read_bytes() == b"retained"
            assert stat.S_IMODE(saved.stat().st_mode) == 0o640
    if existing:
        assert generation.read_bytes() == b"7\n"
        assert stat.S_IMODE(generation.stat().st_mode) == 0o600
    initialized = [snapshot(sandbox.path(root)) for root in DATA_ROOTS]
    second = sandbox.run(**environment)
    assert second.returncode == 0, second.stderr
    assert sandbox.commands() == expected * 2
    assert [snapshot(sandbox.path(root)) for root in DATA_ROOTS] == initialized
    assert snapshot(sandbox.path(sandbox.home)) == oauth_before


@pytest.mark.parametrize(
    "variable",
    ("OAUTH_HOME", "OMNIGENT_STATE_PATH", "OMNIGENT_ARTIFACTS_PATH", "XDG_CACHE_HOME"),
)
def test_unexpected_paths_fail_before_mutation(
    sandbox: InitializerSandbox, variable: str
) -> None:
    before = snapshot(sandbox.path(sandbox.home))
    result = sandbox.run(**{variable: "/unexpected"})
    assert result.returncode == 64, result.stderr
    assert sandbox.commands() == []
    assert not sandbox.path("/unexpected").exists()
    assert snapshot(sandbox.path(sandbox.home)) == before


def test_missing_oauth_selection_fails_before_mutation(
    sandbox: InitializerSandbox,
) -> None:
    result = sandbox.run(OAUTH_HOME="")
    assert result.returncode == 64
    assert sandbox.commands() == []


@pytest.mark.parametrize("root", (*DATA_ROOTS, "oauth"))
def test_symlinked_roots_fail_before_mutation(
    sandbox: InitializerSandbox, root: str
) -> None:
    path = sandbox.path(sandbox.home if root == "oauth" else root)
    target = sandbox.root / "symlink-target"
    if root == "oauth":
        path.rename(target)
    else:
        target.mkdir()
        path.parent.mkdir(parents=True, exist_ok=True)
    before = snapshot(target)
    path.symlink_to(target, target_is_directory=True)
    result = sandbox.run()
    assert result.returncode == 65
    assert sandbox.commands() == []
    assert snapshot(target) == before


def test_missing_oauth_home_is_not_created(sandbox: InitializerSandbox) -> None:
    sandbox.path(sandbox.home).rename(sandbox.root / "retained-oauth")
    result = sandbox.run()
    assert result.returncode == 66
    assert not sandbox.path(sandbox.home).exists()
    assert all(command[-1] != sandbox.home for command in sandbox.commands())


@pytest.mark.parametrize("owner", ("0:1000", "1000:0"))
def test_wrong_oauth_owner_fails_without_repair(
    sandbox: InitializerSandbox, owner: str
) -> None:
    before = snapshot(sandbox.path(sandbox.home))
    result = sandbox.run(OAUTH_OWNER=owner)
    assert result.returncode == 67
    assert snapshot(sandbox.path(sandbox.home)) == before
    assert [
        command for command in sandbox.commands() if command[-1] == sandbox.home
    ] == [["stat", "-c", "%u:%g", sandbox.home]]


@pytest.mark.parametrize(
    "command,code", (("mkdir", 42), ("chown", 42), ("chmod", 42), ("stat", 67))
)
def test_command_failures_stop_initialization(
    sandbox: InitializerSandbox, command: str, code: int
) -> None:
    before = snapshot(sandbox.path(sandbox.home))
    result = sandbox.run(FAIL_COMMAND=command)
    assert result.returncode == code
    assert f"{command} failed" in result.stderr
    assert sandbox.commands()[-1][0] == command
    assert snapshot(sandbox.path(sandbox.home)) == before


@pytest.mark.parametrize("runtime", ("codex", "claude"))
def test_compose_initializers_select_the_generic_script_and_matching_home(
    runtime: str,
) -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())
    service = compose["services"][f"omnigent-host-{runtime}-init"]
    assert service["entrypoint"] == ["/opt/moonmind/init-oauth-host.sh"]
    assert service["environment"] == {
        "OMNIGENT_STATE_PATH": "/home/app/.omnigent",
        "OAUTH_HOME": f"/home/app/.{runtime}",
    }
    assert f"{runtime}_auth_volume:/home/app/.{runtime}" in service["volumes"]
