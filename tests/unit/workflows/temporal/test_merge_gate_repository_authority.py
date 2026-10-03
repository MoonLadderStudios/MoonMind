"""A result URL cannot combine one repository with another repository's PR."""

import pytest

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


@pytest.mark.parametrize("new_history", [True, False])
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/other/project/pull/123",
        "https://github.com/owner/repo/pull/123extra",
        "https://github.com/owner/repo/pull/123/files",
        "https://github.com/owner/repo/pull/0",
    ],
)
def test_merge_handoff_rejects_unbound_repository_or_ambiguous_url(url, new_history):
    parent = MoonMindRunWorkflow()
    parent._repo = "owner/repo"
    parent._workflow_patch_enabled = lambda _: new_history
    arguments = {
        "parameters": {"mergeAutomation": {"enabled": True}},
        "pull_request_url": url,
        "head_sha": "a" * 40,
        "parent_workflow_id": "parent",
        "parent_run_id": "run",
    }
    if new_history:
        with pytest.raises(ValueError, match="authored repository"):
            parent._build_merge_gate_start_payload(**arguments)
    else:
        assert parent._build_merge_gate_start_payload(**arguments) is not None


def test_merge_handoff_preserves_canonical_same_repository_url():
    parent = MoonMindRunWorkflow()
    parent._repo = "Owner/Repo"
    parent._workflow_patch_enabled = lambda _: True
    payload = parent._build_merge_gate_start_payload(
        parameters={"mergeAutomation": {"enabled": True}},
        pull_request_url="https://github.com/owner/repo/pull/123/",
        head_sha="a" * 40,
        parent_workflow_id="parent",
        parent_run_id="run",
    )
    assert payload["pullRequest"]["repo"] == "Owner/Repo"
    assert payload["pullRequest"]["number"] == 123


def test_merge_handoff_cannot_derive_repository_authority_from_result_url():
    parent = MoonMindRunWorkflow()
    parent._workflow_patch_enabled = lambda _: True
    with pytest.raises(ValueError, match="authored repository"):
        parent._build_merge_gate_start_payload(
            parameters={"mergeAutomation": {"enabled": True}},
            pull_request_url="https://github.com/owner/repo/pull/123",
            head_sha="a" * 40,
            parent_workflow_id="parent",
            parent_run_id="run",
        )
