"""Exercise the standalone Tactics entrypoint without a live Docker daemon."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from itertools import combinations
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".agents/skills/tactics-test/scripts/run_dood_unreal_tactics.sh"
)
CONFIGURED_IMAGE = "registry.example/team/unreal:configured"
_MANAGED_MARKERS = ("MOONMIND_AGENT_RUN_ID", "MOONMIND_RUNTIME_ID", "MOONMIND_URL")


@pytest.fixture
def standalone(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    repo = tmp_path / "tactics"
    repo.mkdir()
    (repo / "Tactics.uproject").write_text("{}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!{bash}\n"
        'printf "%q " "$@" >> "$COMMAND_LOG"\n'
        'printf "\\n" >> "$COMMAND_LOG"\n'
        'if [[ "$1" == run ]]; then\n'
        '  printf "%s\\n" "$RUN_OUTPUT"\n'
        '  exit "${RUN_EXIT_CODE:-0}"\n'
        "fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    # Force collisions in the old second-resolution artifact names.
    date = bin_dir / "date"
    date.write_text(
        f"#!{bash}\n"
        'if [[ "$*" == *%Y%m%dT%H%M%SZ* ]]; then\n'
        "  echo 20261019T091500Z\n"
        "else\n"
        "  echo 2026-10-19T09:15:00Z\n"
        "fi\n",
        encoding="utf-8",
    )
    date.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in (
            *_MANAGED_MARKERS,
            "MOONMIND_CONTAINER_JOBS_SESSION_ID",
            "MOONMIND_UNREAL_ENGINE_IMAGE",
        )
    }
    env.update(
        PATH=f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        HOME=str(tmp_path / "home"),
        COMMAND_LOG=str(tmp_path / "docker-calls.log"),
        MOONMIND_UNREAL_ENGINE_IMAGE=CONFIGURED_IMAGE,
        RUN_OUTPUT="Test Completed. Result={Success}",
    )
    return repo, env


def _run(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(SCRIPT), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _gate(repo: Path) -> dict:
    return json.loads(
        (repo / ".artifacts/dood-unreal-tactics/latest/gate.json").read_text(
            encoding="utf-8"
        )
    )


def _runs(env: dict[str, str]) -> list[list[str]]:
    return [
        args
        for line in Path(env["COMMAND_LOG"]).read_text(encoding="utf-8").splitlines()
        if (args := shlex.split(line))[0] == "run"
    ]


@pytest.mark.parametrize("explicit_default", [None, "repo", "image", "phase"])
def test_configured_defaults_match_explicit_defaults(
    standalone: tuple[Path, dict[str, str]], explicit_default: str | None
) -> None:
    repo, env = standalone
    args = {
        None: (),
        "repo": ("--repo", str(repo)),
        "image": ("--image", CONFIGURED_IMAGE),
        "phase": ("--phase", "all"),
    }[explicit_default]

    result = _run(repo, env, *args)

    assert result.returncode == 0, result.stderr
    gate = _gate(repo)
    assert (gate["repo"], gate["phase"], gate["status"]) == (str(repo), "all", "PASS")
    assert gate["image"] == CONFIGURED_IMAGE
    assert (gate["buildStatus"], gate["testStatus"]) == ("pass", "pass")
    runs = _runs(env)
    assert len(runs) == 2
    assert all(CONFIGURED_IMAGE in args for args in runs)
    assert all(f"type=bind,src={repo},dst=/project" in args for args in runs)


@pytest.mark.parametrize("phase", ["build", "test"])
def test_explicit_repo_image_and_phase_override_defaults(
    standalone: tuple[Path, dict[str, str]], phase: str
) -> None:
    cwd, env = standalone
    repo = cwd.parent / "selected-repo"
    repo.mkdir()
    (repo / "Tactics.uproject").write_text("{}\n", encoding="utf-8")
    image = "registry.example/team/alternate:operator-choice"

    result = _run(cwd, env, "--repo", str(repo), "--image", image, "--phase", phase)

    assert result.returncode == 0, result.stderr
    gate = _gate(repo)
    assert (gate["repo"], gate["phase"], gate["status"]) == (str(repo), phase, "PASS")
    assert gate["image"] == image
    assert gate[f"{phase}Status"] == "pass"
    other_phase = "test" if phase == "build" else "build"
    assert gate[f"{other_phase}Status"] == "skipped"
    assert f"{other_phase}Log" not in gate
    runs = _runs(env)
    assert len(runs) == 1
    assert image in runs[0]
    assert CONFIGURED_IMAGE not in runs[0]
    assert f"type=bind,src={repo},dst=/project" in runs[0]
    assert not (cwd / ".artifacts").exists()


@pytest.mark.parametrize("configured_image", [None, "", "  "])
def test_unconfigured_image_fails_precisely_without_invoking_docker(
    standalone: tuple[Path, dict[str, str]], configured_image: str | None
) -> None:
    repo, env = standalone
    env.pop("MOONMIND_UNREAL_ENGINE_IMAGE")
    if configured_image is not None:
        env["MOONMIND_UNREAL_ENGINE_IMAGE"] = configured_image
    gate_file = repo / ".artifacts/dood-unreal-tactics/latest/gate.json"
    gate_file.parent.mkdir(parents=True)
    gate_file.write_text('{"status":"PASS"}\n', encoding="utf-8")

    result = _run(repo, env, "--repo", str(repo))

    assert result.returncode != 0
    assert "MOONMIND_UNREAL_ENGINE_IMAGE" in result.stderr
    assert "--image" in result.stderr
    gate = _gate(repo)
    assert gate["status"] == "FAIL"
    assert "MOONMIND_UNREAL_ENGINE_IMAGE" in gate["reason"]
    assert not Path(env["COMMAND_LOG"]).exists()


def test_same_second_runs_preserve_previous_logs_and_use_distinct_containers(
    standalone: tuple[Path, dict[str, str]],
) -> None:
    repo, env = standalone
    args = ("--repo", str(repo), "--image", CONFIGURED_IMAGE, "--phase", "build")
    first = _run(repo, {**env, "RUN_OUTPUT": "first run evidence"}, *args)
    assert first.returncode == 0, first.stderr
    previous = _gate(repo)

    second = _run(repo, {**env, "RUN_OUTPUT": "second run evidence"}, *args)

    assert second.returncode == 0, second.stderr
    current = _gate(repo)
    assert current["resultsDir"] != previous["resultsDir"]
    assert (
        Path(previous["buildLog"]).read_text(encoding="utf-8") == "first run evidence\n"
    )
    assert (
        Path(current["buildLog"]).read_text(encoding="utf-8") == "second run evidence\n"
    )
    names = [args[args.index("--name") + 1] for args in _runs(env)]
    assert len(set(names)) == 2


@pytest.mark.parametrize("exit_code, status", [(0, "PASS"), (1, "FAIL")])
def test_run_archives_its_gate_before_latest_is_replaced(
    standalone: tuple[Path, dict[str, str]], exit_code: int, status: str
) -> None:
    repo, env = standalone
    args = ("--repo", str(repo), "--image", CONFIGURED_IMAGE, "--phase", "build")
    first = _run(repo, {**env, "RUN_EXIT_CODE": str(exit_code)}, *args)
    assert first.returncode == exit_code, first.stderr
    previous = _gate(repo)
    archived = Path(previous["resultsDir"]) / "gate.json"
    assert archived.is_file()
    assert json.loads(archived.read_text(encoding="utf-8")) == previous
    assert previous["status"] == status
    original = archived.read_bytes()

    second = _run(repo, env, *args)

    assert second.returncode == 0, second.stderr
    assert archived.read_bytes() == original
    assert _gate(repo)["resultsDir"] != previous["resultsDir"]


@pytest.mark.parametrize("output", ["", "LogAutomationController: Tests queued"])
def test_zero_exit_without_completed_test_is_not_success(
    standalone: tuple[Path, dict[str, str]], output: str
) -> None:
    repo, env = standalone

    result = _run(
        repo,
        {**env, "RUN_OUTPUT": output},
        "--repo",
        str(repo),
        "--image",
        CONFIGURED_IMAGE,
        "--phase",
        "test",
    )

    assert result.returncode != 0
    gate = _gate(repo)
    assert (gate["status"], gate["testStatus"]) == ("INCOMPLETE", "incomplete")
    assert "completed" in gate["reason"].lower()


@pytest.mark.parametrize(
    "markers",
    [
        *combinations(_MANAGED_MARKERS, 1),
        *combinations(_MANAGED_MARKERS, 2),
        ("MOONMIND_CONTAINER_JOBS_SESSION_ID",),
    ],
)
def test_partial_managed_identity_never_falls_back_to_standalone(
    standalone: tuple[Path, dict[str, str]], markers: tuple[str, ...]
) -> None:
    repo, env = standalone
    env.update({marker: "managed-identity" for marker in markers})

    result = _run(repo, env, "--repo", str(repo), "--image", CONFIGURED_IMAGE)

    assert result.returncode == 2
    assert "managed-session environment defect" in result.stderr
    for marker in set(_MANAGED_MARKERS) - set(markers):
        assert marker in result.stderr
    assert not Path(env["COMMAND_LOG"]).exists()
    assert not (repo / ".artifacts").exists()
