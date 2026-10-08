"""Repair partial grant projection writes without changing recorded identity."""

import logging
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from temporalio import workflow
from temporalio.common import SearchAttributeKey

from moonmind.schemas.agent_run_progress import build_progress_projection
from moonmind.workflows.executions.provider_profile_projection import (
    PROVIDER_PROFILE_MEMO_KEY,
    PROVIDER_PROFILE_SEARCH_ATTRIBUTE,
    build_provider_profile_projection,
    provider_profile_id_token,
    provider_profile_state_token,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_LAUNCH_PROVIDER_PROFILE_PROJECTION_PATCH,
    RUN_LAUNCH_PROVIDER_PROFILE_PROJECTION_RETRY_PATCH,
    MoonMindUserWorkflow,
)

_CHILD_ID = "profile-retry-child"
_NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def _progress(revision=1, profile="work", label="Frozen work"):
    return build_progress_projection(
        agent_run_workflow_id=_CHILD_ID,
        agent_run_run_id="child-run",
        source_workflow_id="parent",
        source_run_id="parent-run",
        step_execution_id="step-1",
        source_generation=_CHILD_ID,
        projection_revision=revision,
        state="launching" if revision == 1 else "running",
        reason_code="launching" if revision == 1 else "running",
        wait_code="none",
        provider_profile_id=profile,
        provider_profile_label=label,
    ).canonical_dict()


def _install_projection(monkeypatch, *, failed_store=None, disabled=(), admitted=True):
    parent = MoonMindUserWorkflow()
    parent._active_agent_child_workflow_id = _CHILD_ID
    parent._started_at = _NOW
    summary, index = build_provider_profile_projection(
        {"targetRuntime": "codex_cli", "profileId": "admitted"},
        labels={"admitted": "Admission snapshot"},
    )
    stores = {
        "memo": {PROVIDER_PROFILE_MEMO_KEY: summary} if admitted else {},
        "index": index if admitted else None,
    }
    attempts = []
    failures = [failed_store] if failed_store else []

    def upsert_memo(values):
        attempts.append("memo")
        if failures == ["memo"]:
            failures.pop()
            raise ValueError("memo encoding failed")
        stores["memo"].update(deepcopy(values))

    def upsert_index(values):
        attempts.append("index")
        if failures == ["index"]:
            failures.pop()
            raise ValueError("search attribute encoding failed")
        assert len(values) == 1
        assert values[0].key == SearchAttributeKey.for_text(
            PROVIDER_PROFILE_SEARCH_ATTRIBUTE
        )
        stores["index"] = values[0].value

    monkeypatch.setattr(workflow, "patched", lambda name: name not in disabled)
    monkeypatch.setattr(workflow, "now", lambda: _NOW)
    monkeypatch.setattr(
        workflow,
        "memo_value",
        lambda key, default=None: stores["memo"].get(key, default),
    )
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            typed_search_attributes={
                SearchAttributeKey.for_text(PROVIDER_PROFILE_SEARCH_ATTRIBUTE): stores[
                    "index"
                ]
            }
        ),
    )
    monkeypatch.setattr(workflow, "upsert_memo", upsert_memo)
    monkeypatch.setattr(workflow, "upsert_search_attributes", upsert_index)
    monkeypatch.setattr(parent, "_get_logger", lambda: logging.getLogger(__name__))
    monkeypatch.setattr(parent, "_update_memo", lambda: None)
    monkeypatch.setattr(parent, "_update_search_attributes", lambda: None)
    return parent, stores, attempts


def _assert_recorded(stores, *profiles):
    entries = [{"id": "admitted", "label": "Admission snapshot"}, *profiles]
    assert stores["memo"][PROVIDER_PROFILE_MEMO_KEY] == {
        "selectionState": "recorded",
        "profiles": entries,
        "profileCount": len(entries),
    }
    assert stores["index"].split() == [
        provider_profile_state_token("recorded"),
        *(provider_profile_id_token(entry["id"]) for entry in entries),
    ]


@pytest.mark.parametrize("failed_store", ["memo", "index"])
@pytest.mark.parametrize("retry_source", ["progress", "terminal_result"])
def test_partial_projection_retry_preserves_frozen_identity(
    monkeypatch, failed_store, retry_source
):
    parent, stores, attempts = _install_projection(
        monkeypatch, failed_store=failed_store
    )
    parent.agent_run_progress(_progress())
    failed_attempts = ["memo"] if failed_store == "memo" else ["memo", "index"]
    assert attempts == failed_attempts
    # The index has not caught up, even if the memo write succeeded.
    assert provider_profile_id_token("work") not in stores["index"].split()

    if retry_source == "progress":
        parent.agent_run_progress(_progress(revision=2, label="Changed after grant"))
    else:
        result = parent._map_agent_run_result(
            {
                "failureClass": "launch_failed",
                "metadata": {
                    "providerProfileId": "work",
                    "providerProfileLabel": "Changed after grant",
                },
            }
        )
        assert result["status"] == "FAILED"

    _assert_recorded(stores, {"id": "work", "label": "Frozen work"})
    assert attempts == [*failed_attempts, "memo", "index"]
    # Repair clears the pending write; later observations stay idempotent.
    parent.agent_run_progress(_progress(revision=3, label="Renamed again"))
    parent._map_agent_run_result({"metadata": {"providerProfileId": "work"}})
    assert attempts == [*failed_attempts, "memo", "index"]
    _assert_recorded(stores, {"id": "work", "label": "Frozen work"})


@pytest.mark.parametrize("failed_store", ["memo", "index"])
def test_new_profile_repairs_pending_projection_without_dropping_prior_grants(
    monkeypatch, failed_store
):
    parent, stores, attempts = _install_projection(
        monkeypatch, failed_store=failed_store
    )
    parent.agent_run_progress(_progress())
    parent.agent_run_progress(_progress(revision=2, profile="other", label="Other"))
    _assert_recorded(
        stores,
        {"id": "work", "label": "Frozen work"},
        {"id": "other", "label": "Other"},
    )
    repaired_attempts = list(attempts)
    parent._map_agent_run_result({"metadata": {"providerProfileId": "work"}})
    parent._map_agent_run_result({"metadata": {"providerProfileId": "other"}})
    assert attempts == repaired_attempts


@pytest.mark.parametrize("failed_store", ["memo", "index"])
def test_retained_history_keeps_original_partial_write_commands(
    monkeypatch, failed_store
):
    parent, stores, attempts = _install_projection(
        monkeypatch,
        failed_store=failed_store,
        disabled={RUN_LAUNCH_PROVIDER_PROFILE_PROJECTION_RETRY_PATCH},
    )
    parent.agent_run_progress(_progress())
    original_attempts = list(attempts)
    original_stores = deepcopy(stores)
    parent.agent_run_progress(_progress(revision=2, label="Changed after grant"))
    parent._map_agent_run_result({"metadata": {"providerProfileId": "work"}})
    assert attempts == original_attempts
    assert stores == original_stores
    # Previously recorded other-profile writes must keep their cached union too.
    parent.agent_run_progress(_progress(revision=3, profile="other", label="Other"))
    assert attempts == [*original_attempts, "memo", "index"]
    _assert_recorded(
        stores,
        {"id": "work", "label": "Frozen work"},
        {"id": "other", "label": "Other"},
    )


@pytest.mark.parametrize("admitted", [False, True])
def test_retry_does_not_backfill_missing_or_pre_projection_histories(
    monkeypatch, admitted
):
    parent, stores, attempts = _install_projection(
        monkeypatch,
        admitted=admitted,
        disabled={RUN_LAUNCH_PROVIDER_PROFILE_PROJECTION_PATCH} if admitted else (),
    )
    initial_stores = deepcopy(stores)
    parent.agent_run_progress(_progress())
    parent.agent_run_progress(_progress(revision=2))
    parent._map_agent_run_result({"metadata": {"providerProfileId": "work"}})
    assert attempts == []
    assert stores == initial_stores
