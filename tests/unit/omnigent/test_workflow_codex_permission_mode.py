"""Workflow-step Codex sessions launch without approval prompts.

A MoonMind workflow step has no human at the Codex terminal. MoonMind used to
send no Codex permission flags, so steps inherited Codex's own default. The
host bubblewrap sandbox cannot create namespaces, so every first command asks
to run unsandboxed; that default changed from automatic review to asking the
user, and mm:7baa29a9-4ad1-4578-a0be-b27af4042b65-2026-11-19T07:09:16Z waited
six hours on a prompt nobody could answer.
"""

import pytest

from tests.unit.omnigent.test_headless_claude_permission_mode import (
    _bind_generic,
    _launch_args,
    _request,
)

_FULL_ACCESS = ["--sandbox", "danger-full-access", "--ask-for-approval", "never"]


def test_codex_workflow_session_launches_without_approval_prompts():
    assert _launch_args(_bind_generic(_request(), "codex-native")) == _FULL_ACCESS


def test_codex_unattended_mode_is_added_beside_other_launch_args():
    request = _request({"terminalLaunchArgs": ["--search"]})

    assert _launch_args(_bind_generic(request, "codex-native")) == [
        "--search",
        *_FULL_ACCESS,
    ]


@pytest.mark.parametrize(
    "explicit",
    [
        ["--sandbox", "read-only"],
        ["-s", "workspace-write"],
        ["--sandbox=workspace-write"],
        ["--ask-for-approval", "on-request"],
        ["-a=untrusted"],
        ["--approve-for-me"],
        ["--not-so-yolo"],
        ["--dangerously-bypass-approvals-and-sandbox"],
        ["--yolo"],
        ["-c", 'approvals_reviewer="user"'],
        ["--config=approval_policy=on-request"],
        ["-c", "sandbox_mode=read-only"],
        ["-c", "default_permissions=strict"],
    ],
)
def test_explicit_codex_permission_choice_is_preserved(explicit):
    request = _request({"terminalLaunchArgs": ["--search", *explicit]})

    assert _launch_args(_bind_generic(request, "codex-native")) == [
        "--search",
        *explicit,
    ]


def test_codex_unrelated_config_overrides_still_receive_the_default():
    request = _request({"terminalLaunchArgs": ["-c", 'model_verbosity="low"']})

    assert _launch_args(_bind_generic(request, "codex-native")) == [
        "-c",
        'model_verbosity="low"',
        *_FULL_ACCESS,
    ]


def test_codex_session_without_saved_defaults_preserves_authored_arguments():
    from moonmind.workflows.adapters.omnigent_agent_adapter import (
        OmnigentResolvedTarget,
        build_omnigent_selection,
        build_omnigent_session_create_payload,
    )

    bound = _bind_generic(
        _request({"terminalLaunchArgs": ["--search"]}), "codex-native"
    )
    payload = build_omnigent_session_create_payload(
        request=bound,
        selection=build_omnigent_selection(bound),
        target=OmnigentResolvedTarget(agent_id="agent-1", source="agent_id"),
        # A row frozen before this default keeps the arguments it recorded.
        launch_defaults={"claude-native": ["--permission-mode", "bypassPermissions"]},
    )

    assert payload["terminal_launch_args"] == ["--search"]
