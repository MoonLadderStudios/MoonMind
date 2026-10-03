"""Default verification retains its toolchain without receiving service authority."""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from moonmind.agents.codex_worker.worker import CodexWorker


TOOLCHAIN_ENV = {
    "JAVA_HOME": "/fixture/jdk",
    "NODE_OPTIONS": "--max-old-space-size=128",
    "CI": "true",
    "CC": "/fixture/toolchain/cc",
    "CXX": "/fixture/toolchain/c++",
    "CFLAGS": "-O0 -g",
    "PKG_CONFIG_PATH": "/fixture/toolchain/pkgconfig",
    "VIRTUAL_ENV": "/fixture/venv",
    "PYTEST_ADDOPTS": "-q",
    "MOONMIND_FORCE_LOCAL_TESTS": "1",
    "MOONMIND_PYTEST_DURATIONS": "3",
    "MOONMIND_PYTEST_JUNITXML": "artifacts/pytest.xml",
    "PYTEST_XDIST_AUTO_NUM_WORKERS": "2",
    "CPPFLAGS": "",
}


@pytest.mark.parametrize("auth", ["token", "identity", "none"])
@pytest.mark.asyncio
@pytest.mark.slow
async def test_default_verification_preserves_toolchain_through_auth_context(
    tmp_path, monkeypatch, auth
):
    """The repository's executable script checks the actual child environment."""
    for key, value in TOOLCHAIN_ENV.items():
        monkeypatch.setenv(key, value)
    for key in (
        "GITHUB_TOKEN", "GH_TOKEN", "DATABASE_URL", "AMQP_URL",
        "CUSTOM_PRIVATE_VALUE", "AWS_ACCESS_KEY_ID", "SSH_AUTH_SOCK",
    ):
        monkeypatch.setenv(key, "fixture-private-value")
    repo_env = CodexWorker._build_command_env(
        "selected-repository-fixture" if auth == "token" else None,
        git_user_name="Fixture Author" if auth == "identity" else None,
    )
    if repo_env is not None:
        assert {key: repo_env.get(key) for key in TOOLCHAIN_ENV} == TOOLCHAIN_ENV
    worker = CodexWorker.__new__(CodexWorker)
    monkeypatch.setattr(worker, "_collect_verification_evidence", lambda **kw: ((), ()))
    monkeypatch.setattr(worker, "_append_stage_log", lambda *args: None)
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    checks = [
        f'[ "${{{key}-unset}}" = {shlex.quote(value)} ] || exit 1'
        for key, value in TOOLCHAIN_ENV.items()
    ]
    checks.append(
        '[ -z "${GITHUB_TOKEN}${GH_TOKEN}${DATABASE_URL}${AMQP_URL}'
        '${CUSTOM_PRIVATE_VALUE}${AWS_ACCESS_KEY_ID}${SSH_AUTH_SOCK}" ] || exit 1'
    )
    script = repo / "tools/test_unit.sh"
    script.write_text("#!/bin/sh\n" + "\n".join(checks) + "\n")
    script.chmod(0o755)
    observed = []

    async def run(command, **kwargs):
        observed.append(kwargs["env"])
        await asyncio.to_thread(
            subprocess.run, command, cwd=kwargs["cwd"], env=kwargs["env"], check=True,
        )

    monkeypatch.setattr(worker, "_run_stage_command", run)
    prepared = SimpleNamespace(
        repo_dir=repo, publish_log_path=tmp_path / "publish.log",
        repo_command_env=repo_env,
        publish_command_env={"GH_TOKEN": "publisher-fixture"},
    )
    await worker._run_default_publish_verification_if_needed(
        prepared=prepared, publish={}, status_output=" M file.py\n"
    )
    assert len(observed) == 1
    assert {key: observed[0].get(key) for key in TOOLCHAIN_ENV} == TOOLCHAIN_ENV


@pytest.mark.asyncio
async def test_verification_uses_selected_toolchain_without_ambient_refill(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("JAVA_HOME", "/ambient/jdk")
    monkeypatch.setenv("NODE_OPTIONS", "--ambient-option")
    worker = CodexWorker.__new__(CodexWorker)
    monkeypatch.setattr(worker, "_collect_verification_evidence", lambda **kw: ((), ()))
    monkeypatch.setattr(worker, "_append_stage_log", lambda *args: None)
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools/test_unit.sh").write_text("#!/bin/sh\nexit 0\n")
    observed = []

    async def run(command, **kwargs):
        observed.append(kwargs["env"])

    monkeypatch.setattr(worker, "_run_stage_command", run)
    prepared = SimpleNamespace(
        repo_dir=repo, publish_log_path=tmp_path / "publish.log",
        repo_command_env={"JAVA_HOME": "/selected/jdk", "NODE_OPTIONS": ""},
        publish_command_env={"JAVA_HOME": "/publisher/jdk", "GH_TOKEN": "fixture"},
    )
    await worker._run_default_publish_verification_if_needed(
        prepared=prepared, publish={}, status_output=" M file.py\n"
    )
    assert observed == [{"JAVA_HOME": "/selected/jdk", "NODE_OPTIONS": ""}]
    prepared.repo_command_env = {}
    await worker._run_default_publish_verification_if_needed(
        prepared=prepared, publish={}, status_output=" M file.py\n"
    )
    assert observed[-1] == {}
