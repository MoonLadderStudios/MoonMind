from __future__ import annotations

import pytest

from moonmind.workflows.temporal.deployment_safety import (
    AGENT_SESSION_CUTOVER_PLAYBOOK_PATH,
    AGENT_SESSION_REPLAYER_TEST_PATH,
    AgentSessionDeploymentSafetyError,
    changed_agent_session_sensitive_paths,
    validate_agent_session_deployment_safety,
)


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
