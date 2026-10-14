"""Retained Activity parameters cannot attest to server admission."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import activity

from moonmind.omnigent import git_identity
from tests.helpers.publication_identity import recorded_publication_identity

_IDENTITY = {"name": "Admitted", "email": "admitted@example.invalid"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "legacy",
    [
        {"name": "Authored", "email": "authored@example.invalid"},
        "malformed",
        {},
        {"name": "injected\n[user]", "email": "author@example.invalid"},
    ],
)
@pytest.mark.parametrize("admitted", [False, True])
async def test_retained_parameter_cannot_choose_or_break_activity_identity(
    monkeypatch, legacy, admitted
):
    recorded_publication_identity(monkeypatch, _IDENTITY if admitted else None)
    result = await git_identity.publication_git_identity_projection(
        {"publicationGitIdentityAdmission": {"schemaVersion": "v1", "identity": legacy}}
    )
    assert result == (_IDENTITY if admitted else None)


@pytest.mark.asyncio
async def test_activity_identity_reads_recorded_parent_and_exact_runs(monkeypatch):
    admission = {
        git_identity.PUBLICATION_GIT_IDENTITY_MEMO_KEY: {
            "schemaVersion": "v1",
            "identity": _IDENTITY,
        }
    }
    child = SimpleNamespace(
        id="child",
        run_id="child-run",
        parent_id="parent",
        parent_run_id="parent-run",
        memo=AsyncMock(return_value={}),
    )
    parent = SimpleNamespace(
        id="parent",
        run_id="parent-run",
        parent_id=None,
        parent_run_id=None,
        memo=AsyncMock(return_value=admission),
    )
    observed = []

    def handle(workflow_id, *, run_id):
        observed.append((workflow_id, run_id))
        return SimpleNamespace(
            describe=AsyncMock(
                return_value={
                    ("child", "child-run"): child,
                    ("parent", "parent-run"): parent,
                }[(workflow_id, run_id)]
            )
        )

    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(workflow_id="child", workflow_run_id="child-run"),
    )
    monkeypatch.setattr(
        activity, "client", lambda: SimpleNamespace(get_workflow_handle=handle)
    )
    result = await git_identity.publication_git_identity_projection(
        {"workflowId": "authored-target"}
    )
    assert result == _IDENTITY
    assert observed == [("child", "child-run"), ("parent", "parent-run")]


@pytest.mark.asyncio
async def test_activity_identity_unavailable_authority_does_not_default(monkeypatch):
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(workflow_id="child", workflow_run_id="run"),
    )
    monkeypatch.setattr(
        activity,
        "client",
        lambda: SimpleNamespace(
            get_workflow_handle=lambda *_args, **_kwargs: SimpleNamespace(
                describe=AsyncMock(
                    side_effect=RuntimeError("recorded memo unavailable")
                )
            )
        ),
    )
    with pytest.raises(RuntimeError, match="recorded memo unavailable"):
        result = await git_identity.publication_git_identity_projection({})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_run", "cycle"])
async def test_activity_identity_rejects_ambiguous_parent_authority(monkeypatch, fault):
    described = SimpleNamespace(
        id="child",
        run_id="different" if fault == "wrong_run" else "run",
        parent_id="child",
        parent_run_id="run",
        memo=AsyncMock(return_value={}),
    )
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(workflow_id="child", workflow_run_id="run"),
    )
    monkeypatch.setattr(
        activity,
        "client",
        lambda: SimpleNamespace(
            get_workflow_handle=lambda *_args, **_kwargs: SimpleNamespace(
                describe=AsyncMock(return_value=described)
            )
        ),
    )
    with pytest.raises(RuntimeError, match="publication admission"):
        await git_identity.publication_git_identity_projection({})
