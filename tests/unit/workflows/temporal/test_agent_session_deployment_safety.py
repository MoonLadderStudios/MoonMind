from __future__ import annotations

import pytest

from moonmind.workflows.temporal.deployment_safety import (
    AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
    AGENT_SESSION_REPLAYER_TEST_PATH,
    AgentSessionDeploymentSafetyError,
    changed_agent_session_sensitive_paths,
    resolve_active_feature_dir,
    validate_agent_session_deployment_safety,
)
from tools import validate_agent_session_deployment_safety as cli


PLAYBOOK_TEXT = """
## Shared Prerequisites
- Worker Versioning is enabled.
- AgentSession histories replay before rollout.

## Enabling `SteerTurn`
Validation gates are required.

## Enabling Continue-As-New
Validation gates are required.

## Changing Cancel/Terminate Semantics
Use CancelSession and TerminateSession rollout gates.

## Introducing Visibility Metadata
Register Search Attributes first.
"""


def test_agent_session_sensitive_path_detection_normalizes_paths() -> None:
    assert changed_agent_session_sensitive_paths(
        [
            "./moonmind/workflows/temporal/workflows/agent_session.py",
            "README.md",
        ]
    ) == ("moonmind/workflows/temporal/workflows/agent_session.py",)


def test_validate_cli_changed_paths_uses_explicit_base_ref(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run_git(args: list[str]) -> list[str]:
        calls.append(tuple(args))
        if args == ["merge-base", "origin/main", "HEAD"]:
            return ["merge-base-sha"]
        if args == ["diff", "--name-only", "merge-base-sha..HEAD"]:
            return ["moonmind/workflows/temporal/workflows/agent_session.py"]
        if args == ["diff", "--name-only", "--cached"]:
            return ["tests/unit/workflows/temporal/test_agent_session_replayer.py"]
        if args == ["diff", "--name-only"]:
            return ["tools/validate_agent_session_deployment_safety.py"]
        if args == ["ls-files", "--others", "--exclude-standard"]:
            return ["docs/tmp/remaining-work/agent-session-deployment-safety-cutover.md"]
        raise AssertionError(f"unexpected git args: {args}")

    monkeypatch.setattr(cli, "_run_git", fake_run_git)

    assert cli._changed_paths("origin/main") == [
        "docs/tmp/remaining-work/agent-session-deployment-safety-cutover.md",
        "moonmind/workflows/temporal/workflows/agent_session.py",
        "tests/unit/workflows/temporal/test_agent_session_replayer.py",
        "tools/validate_agent_session_deployment_safety.py",
    ]
    assert calls[0] == ("merge-base", "origin/main", "HEAD")


def test_active_feature_override_resolves_spec_number(tmp_path) -> None:
    feature_dir = tmp_path / "specs" / "165-agent-session-deployment-safety"
    feature_dir.mkdir(parents=True)
    for name in ("spec.md", "plan.md", "tasks.md"):
        (feature_dir / name).write_text("# artifact\n", encoding="utf-8")

    assert (
        resolve_active_feature_dir(
            repo_root=tmp_path,
            active_feature="165-agent-session-deployment-safety",
        )
        == "specs/165-agent-session-deployment-safety"
    )


def test_active_feature_override_requires_complete_artifact_set(tmp_path) -> None:
    feature_dir = tmp_path / "specs" / "165-agent-session-deployment-safety"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("# spec\n", encoding="utf-8")

    with pytest.raises(
        AgentSessionDeploymentSafetyError,
        match="active feature override is missing artifacts",
    ):
        resolve_active_feature_dir(
            repo_root=tmp_path,
            active_feature="specs/165-agent-session-deployment-safety",
        )


def test_agent_session_deployment_safety_gate_passes_for_non_sensitive_changes() -> None:
    report = validate_agent_session_deployment_safety(
        changed_paths=["docs/ManagedAgents/CodexManagedSessionPlane.md"],
        worker_versioning_behavior="Disabled",
        repo_paths=[],
        cutover_playbook_text="",
    )

    assert report.required is False


def test_agent_session_deployment_safety_gate_requires_worker_versioning() -> None:
    with pytest.raises(
        AgentSessionDeploymentSafetyError,
        match="TEMPORAL_WORKER_VERSIONING_DEFAULT_BEHAVIOR",
    ):
        validate_agent_session_deployment_safety(
            changed_paths=["moonmind/workflows/temporal/workflows/agent_session.py"],
            worker_versioning_behavior="Disabled",
            repo_paths=[
                AGENT_SESSION_REPLAYER_TEST_PATH,
                AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
            ],
            cutover_playbook_text=PLAYBOOK_TEXT,
        )


def test_agent_session_deployment_safety_gate_requires_replay_coverage() -> None:
    with pytest.raises(
        AgentSessionDeploymentSafetyError,
        match="missing managed-session replay gate",
    ):
        validate_agent_session_deployment_safety(
            changed_paths=["moonmind/workflows/temporal/workflows/agent_session.py"],
            worker_versioning_behavior="Auto-Upgrade",
            repo_paths=[AGENT_SESSION_CUTOVER_PLAYBOOK_PATH],
            cutover_playbook_text=PLAYBOOK_TEXT,
        )


def test_agent_session_deployment_safety_gate_requires_cutover_topics() -> None:
    with pytest.raises(
        AgentSessionDeploymentSafetyError,
        match="cutover playbook is missing required topics",
    ):
        validate_agent_session_deployment_safety(
            changed_paths=["moonmind/workflows/temporal/workflows/agent_session.py"],
            worker_versioning_behavior="Pinned",
            repo_paths=[
                AGENT_SESSION_REPLAYER_TEST_PATH,
                AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
            ],
            cutover_playbook_text="Worker Versioning and replay only",
        )


def test_agent_session_deployment_safety_gate_accepts_full_gate_set() -> None:
    report = validate_agent_session_deployment_safety(
        changed_paths=["moonmind/workflows/temporal/workflows/agent_session.py"],
        worker_versioning_behavior="Auto-Upgrade",
        repo_paths=[
            AGENT_SESSION_REPLAYER_TEST_PATH,
            AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
        ],
        cutover_playbook_text=PLAYBOOK_TEXT,
    )

    assert report.required is True
    assert report.worker_versioning_behavior == "Auto-Upgrade"
    assert report.replay_gate_path == AGENT_SESSION_REPLAYER_TEST_PATH


def test_agent_session_deployment_safety_report_includes_active_feature() -> None:
    report = validate_agent_session_deployment_safety(
        changed_paths=["moonmind/workflows/temporal/workflows/agent_session.py"],
        worker_versioning_behavior="Auto-Upgrade",
        repo_paths=[
            AGENT_SESSION_REPLAYER_TEST_PATH,
            AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
        ],
        cutover_playbook_text=PLAYBOOK_TEXT,
        active_feature_dir="specs/165-agent-session-deployment-safety",
    )

    assert report.active_feature_dir == "specs/165-agent-session-deployment-safety"
