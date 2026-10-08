"""Recorded Provider Profile filters against a real Temporal Visibility store.

MoonLadderStudios/MoonMind#4640: the Workflows list filters recorded Provider
Profile membership through opaque tokens in the ``mm_provider_profile`` Text
Search Attribute. Unit tests assert the generated query strings; this test
proves those production clauses (``=``, ``!=`` and ``IS NULL`` on a Text
attribute) select exactly the intended workflows on a real Temporal dev server,
for both ``list_workflows`` and ``count_workflows``, and that similar Provider
Profile IDs never cross-match.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import pytest
from starlette.requests import Request
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers.executions import (
    _build_temporal_execution_query,
    _detect_optional_temporal_search_attributes,
    _provider_profile_facet_response,
)
from api_service.db.models import TemporalWorkflowType
from moonmind.workflows.executions.provider_profile_projection import (
    PROVIDER_PROFILE_MEMO_KEY,
    PROVIDER_PROFILE_SEARCH_ATTRIBUTE,
    build_provider_profile_projection,
    provider_profile_id_token,
    provider_profile_state_token,
    provider_profile_summary_from_memo,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import WORKFLOW_ENTRY_BY_TYPE
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_USER_WORKFLOW = TemporalWorkflowType.USER_WORKFLOW
# No worker polls this queue: Visibility records exist from start, and the
# filters under test depend only on start-time Search Attributes.
_TASK_QUEUE = "provider-profile-visibility-unpolled"

# Admitted parameters per workflow, keyed by a short label. ``None`` marks a
# historical record that predates the projection (no Search Attribute at all).
_RECORDS: dict[str, Mapping[str, Any] | None] = {
    "single": {
        "targetRuntime": "codex",
        "agentProfileSnapshot": {"providerProfileRef": "acct-a"},
    },
    "multi": {
        "targetRuntime": "codex",
        "agentProfileSnapshot": {"providerProfileRef": "acct-ab"},
        "workflow": {
            "steps": [
                {"runtime": {"mode": "codex", "providerProfileRef": "codex_default"}}
            ]
        },
    },
    # The same Provider Profile on the workflow and a step counts once.
    "duplicate": {
        "targetRuntime": "codex",
        "agentProfileSnapshot": {"providerProfileRef": "acct-a"},
        "workflow": {
            "steps": [{"runtime": {"mode": "codex", "providerProfileRef": "acct-a"}}]
        },
    },
    # A prefix of other IDs must not match them, nor be matched by them.
    "prefix": {
        "targetRuntime": "codex",
        "agentProfileSnapshot": {"providerProfileRef": "acct"},
    },
    "pending": {"targetRuntime": "codex"},
    "not_applicable": {},
    "legacy": None,
}
_ALL = frozenset(_RECORDS)
_RECORDED = frozenset({"single", "multi", "duplicate", "prefix"})

_CASES: list[tuple[Sequence[tuple[str, str]], frozenset[str]]] = [
    ([], _ALL),
    ([("providerProfileIn", "acct-a")], frozenset({"single", "duplicate"})),
    ([("providerProfileIn", "acct-ab")], frozenset({"multi"})),
    ([("providerProfileIn", "acct")], frozenset({"prefix"})),
    ([("providerProfileIn", "codex_default")], frozenset({"multi"})),
    ([("providerProfileIn", "codex")], frozenset()),
    ([("providerProfileIn", "unknown-profile")], frozenset()),
    (
        [("providerProfileIn", "acct-a,codex_default")],
        frozenset({"single", "duplicate", "multi"}),
    ),
    (
        [("providerProfileIn", "acct-a"), ("providerProfileIn", "acct")],
        frozenset({"single", "duplicate", "prefix"}),
    ),
    (
        [("providerProfileNotIn", "acct-a")],
        _ALL - {"single", "duplicate"},
    ),
    (
        [("providerProfileNotIn", "acct-a,acct-ab")],
        _ALL - {"single", "duplicate", "multi"},
    ),
    ([("providerProfileNotIn", "codex_default")], _ALL - {"multi"}),
    ([("providerProfileStateIn", "pending")], frozenset({"pending"})),
    ([("providerProfileStateIn", "not_applicable")], frozenset({"not_applicable"})),
    ([("providerProfileStateIn", "not_recorded")], frozenset({"legacy"})),
    (
        [("providerProfileStateIn", "pending,not_recorded")],
        frozenset({"pending", "legacy"}),
    ),
    ([("providerProfileStateNotIn", "pending")], _ALL - {"pending"}),
    ([("providerProfileStateNotIn", "not_applicable")], _ALL - {"not_applicable"}),
    ([("providerProfileStateNotIn", "not_recorded")], _ALL - {"legacy"}),
    (
        [("providerProfileStateNotIn", "pending,not_applicable,not_recorded")],
        _RECORDED,
    ),
    (
        [("providerProfileBlank", "true")],
        frozenset({"pending", "not_applicable", "legacy"}),
    ),
    ([("providerProfileBlank", "false")], _RECORDED),
    (
        [
            ("providerProfileIn", "acct-a,acct-ab"),
            ("providerProfileNotIn", "codex_default"),
        ],
        frozenset({"single", "duplicate"}),
    ),
    (
        [
            ("providerProfileIn", "acct-ab"),
            ("providerProfileStateIn", "not_recorded,pending"),
            ("providerProfileNotIn", "codex_default"),
            ("providerProfileStateNotIn", "not_applicable"),
        ],
        frozenset({"pending", "legacy"}),
    ),
]


def _request(params: Sequence[tuple[str, str]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/executions",
            "headers": [],
            "query_string": urlencode(list(params)).encode(),
        }
    )


def _production_query(
    params: Sequence[tuple[str, str]],
    *,
    owner_id: str,
    usable_search_attributes: frozenset[str],
) -> tuple[str, str]:
    """Build list/count queries exactly as the user Workflows list does."""

    unset: dict[str, Any] = dict.fromkeys(
        (
            "workflow_type",
            "state",
            "state_in",
            "state_not_in",
            "entry",
            "repo",
            "repo_exact",
            "repo_in",
            "repo_not_in",
            "integration",
            "target_runtime",
            "target_runtime_in",
            "target_runtime_not_in",
            "target_skill_in",
            "target_skill_not_in",
            "scheduled_from",
            "scheduled_to",
            "scheduled_blank",
            "updated_from",
            "updated_to",
            "created_from",
            "created_to",
            "finished_from",
            "finished_to",
            "finished_blank",
            "scope",
            "sort",
            "sort_dir",
        )
    )
    return _build_temporal_execution_query(
        request=_request(params),
        owner_type="user",
        owner_id=owner_id,
        include_order=True,
        usable_search_attributes=usable_search_attributes,
        **unset,
    )


async def _start(
    adapter: TemporalClientAdapter,
    *,
    workflow_id: str,
    owner_id: str,
    parameters: Mapping[str, Any] | None,
    task_queue: str = _TASK_QUEUE,
    input_args: Mapping[str, Any] | None = None,
    labels: Mapping[str, str] | None = None,
) -> str | None:
    """Start an unpolled user workflow with admission's Search Attributes."""

    search_attributes: dict[str, Any] = {
        "mm_owner_type": "user",
        "mm_owner_id": owner_id,
        "mm_state": "executing",
        "mm_entry": WORKFLOW_ENTRY_BY_TYPE[_USER_WORKFLOW],
    }
    memo: dict[str, Any] = {}
    profile_value = None
    if parameters is not None:
        summary, profile_value = build_provider_profile_projection(
            parameters, labels=labels
        )
        memo[PROVIDER_PROFILE_MEMO_KEY] = summary
        search_attributes[PROVIDER_PROFILE_SEARCH_ATTRIBUTE] = profile_value
    await adapter.start_workflow(
        workflow_type=_USER_WORKFLOW.value,
        workflow_id=workflow_id,
        input_args=dict(input_args or {}),
        memo=memo,
        search_attributes=search_attributes,
        task_queue=task_queue,
    )
    return profile_value


async def _listed_ids(client, query: str) -> list[str]:
    return [row.id async for row in client.list_workflows(query=query)]


async def test_recorded_provider_profile_filters_match_real_visibility_4640() -> None:
    # Reuse an installed Temporal CLI when present; otherwise the SDK downloads
    # its default dev server, as the other hermetic Visibility tests do.
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as env:
        await register_deployment_search_attributes(env)
        client = env.client
        usable = await _detect_optional_temporal_search_attributes(client)
        assert PROVIDER_PROFILE_SEARCH_ATTRIBUTE in usable

        owner_id = str(uuid4())
        run = uuid4().hex[:8]
        workflow_ids = {label: f"mm:pp-{run}-{label}" for label in _RECORDS}
        labels_by_id = {
            workflow_id: label for label, workflow_id in workflow_ids.items()
        }
        adapter = TemporalClientAdapter(client)
        written: dict[str, str | None] = {}
        for label, parameters in _RECORDS.items():
            written[label] = await _start(
                adapter,
                workflow_id=workflow_ids[label],
                owner_id=owner_id,
                parameters=parameters,
            )
        # Another owner's recorded workflow must stay outside the user scope.
        other_owner_id = f"mm:pp-{run}-other-owner"
        await _start(
            adapter,
            workflow_id=other_owner_id,
            owner_id=str(uuid4()),
            parameters=_RECORDS["single"],
        )

        # The duplicate Provider Profile is indexed once.
        duplicate_tokens = str(written["duplicate"]).split()
        assert duplicate_tokens.count(provider_profile_id_token("acct-a")) == 1

        # Wait for Visibility to observe every start before evaluating filters.
        baseline, _ = _production_query(
            [], owner_id=owner_id, usable_search_attributes=usable
        )
        async with asyncio.timeout(30):
            while True:
                seen = await _listed_ids(client, baseline)
                if set(seen) == set(labels_by_id):
                    break
                await asyncio.sleep(0.2)
        described = await client.get_workflow_handle(workflow_ids["multi"]).describe()
        stored = described.typed_search_attributes
        stored_value = next(
            pair.value
            for pair in stored
            if pair.key.name == PROVIDER_PROFILE_SEARCH_ATTRIBUTE
        )
        assert stored_value == written["multi"]

        mismatches: list[str] = []
        for params, expected in _CASES:
            count_query, list_query = _production_query(
                params, owner_id=owner_id, usable_search_attributes=usable
            )
            listed = await _listed_ids(client, list_query)
            counted = (await client.count_workflows(query=count_query)).count
            listed_labels = sorted(labels_by_id.get(item, item) for item in listed)
            if (
                listed_labels != sorted(expected)
                or len(listed) != len(set(listed))
                or counted != len(expected)
            ):
                mismatches.append(
                    f"params={list(params)} expected={sorted(expected)} "
                    f"listed={listed_labels} counted={counted} query={list_query}"
                )
        assert not mismatches, "\n".join(mismatches)


async def test_profile_facets_keep_overflow_names_counts_and_scope_4640() -> None:
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as env:
        await register_deployment_search_attributes(env)
        client = env.client
        usable = await _detect_optional_temporal_search_attributes(client)
        owner_id = str(uuid4())
        adapter = TemporalClientAdapter(client)
        labels = {
            f"retired-profile-{index}": f"Recorded name {index}" for index in range(12)
        }
        # Equal labels must still preserve separate IDs. There is no live profile
        # inventory here from which a former name or launchability can be guessed.
        labels["retired-profile-1"] = labels["retired-profile-0"]
        parameters = {
            "task": {
                "steps": [
                    {"runtime": {"providerProfileRef": profile_id}}
                    for profile_id in labels
                ]
            }
        }
        labels["later-profile"] = "A recorded profile on another workflow page"
        for suffix, recorded, owner in [
            ("many", parameters, owner_id),
            ("duplicate", {"profileId": "retired-profile-0"}, owner_id),
            ("later", {"profileId": "later-profile"}, owner_id),
            ("outside", {"profileId": "outside-scope"}, str(uuid4())),
        ]:
            await _start(
                adapter,
                workflow_id=f"mm:pp-facet-{uuid4().hex}-{suffix}",
                owner_id=owner,
                parameters=recorded,
                labels=labels,
            )
        base_query, _ = _production_query(
            [],
            owner_id=owner_id,
            usable_search_attributes=usable,
        )
        async with asyncio.timeout(30):
            while (await client.count_workflows(query=base_query)).count != 3:
                await asyncio.sleep(0.2)
        items = {}
        cursor = None
        for _ in range(15):
            page = await _provider_profile_facet_response(
                client=client,
                base_query=base_query,
                search_value=None,
                page_size=2,
                next_page_token=cursor,
            )
            assert len(page.items) <= 2
            for item in page.items:
                assert item.value != "outside-scope"
                assert item.count == (2 if item.value == "retired-profile-0" else 1)
                items[item.value] = item.label
            cursor = page.next_page_token
            assert page.truncated is bool(cursor)
            if not cursor:
                break
        else:
            pytest.fail("real Visibility facet pagination did not finish")
        assert items == labels


_LAUNCH_TASK_QUEUE = "provider-profile-launch-resolution"
_GRANTED_PROFILE = "codex-work"


@workflow.defn(name="MoonMind.AgentRun", sandboxed=False)
class _GrantedProfileAgentRun:
    """Stub child reporting the Provider Profile its managed launch was granted."""

    @workflow.run
    async def run(self, _request: Any) -> dict[str, Any]:
        return {
            "summary": "done",
            "metadata": {
                "providerProfileId": _GRANTED_PROFILE,
                "providerProfileLabel": "Work",
            },
        }


@pytest.fixture
def launch_resolution_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real workflow, memo, and Search Attribute boundaries; stub stages."""

    async def planning(self: MoonMindUserWorkflow, **_kwargs: Any) -> str:
        return "artifact://plan/launch-resolution"

    async def execution(self: MoonMindUserWorkflow, **_kwargs: Any) -> None:
        child_result = await workflow.execute_child_workflow(
            "MoonMind.AgentRun",
            {"agentKind": "managed", "agentId": "codex_cli"},
            id=f"{workflow.info().workflow_id}:agent:step-1",
            task_queue=_LAUNCH_TASK_QUEUE,
        )
        self._map_agent_run_result(child_result)

    async def finalizing(self: MoonMindUserWorkflow, **_kwargs: Any) -> None:
        return None

    async def terminal_state(self: MoonMindUserWorkflow, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_planning_stage", planning)
    monkeypatch.setattr(MoonMindUserWorkflow, "_run_execution_stage", execution)
    monkeypatch.setattr(MoonMindUserWorkflow, "_run_finalizing_stage", finalizing)
    monkeypatch.setattr(MoonMindUserWorkflow, "_record_terminal_state", terminal_state)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("initial_count", [0, 8])
async def test_launch_resolved_provider_profile_replaces_pending_selection_4640(
    launch_resolution_stages: None,
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
    initial_count: int,
) -> None:
    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=shutil.which("temporal")
    ) as env:
        await register_deployment_search_attributes(env)
        client = env.client
        usable = await _detect_optional_temporal_search_attributes(client)
        owner_id = str(uuid4())
        monkeypatch.setattr(
            MoonMindUserWorkflow,
            "_trusted_owner_metadata",
            lambda self: ("user", owner_id),
        )
        workflow_id = f"mm:pp-launch-{uuid4().hex[:8]}"
        # Exercise both unresolved selection and a ninth launch-resolved Profile.
        parameters = {"targetRuntime": "codex_cli", "runtime": {"mode": "codex_cli"}}
        if initial_count:
            parameters["task"] = {
                "steps": [
                    {"runtime": {"providerProfileRef": f"initial-{index}"}}
                    for index in range(initial_count)
                ]
            }
        admitted_value = await _start(
            TemporalClientAdapter(client),
            workflow_id=workflow_id,
            owner_id=owner_id,
            parameters=parameters,
            task_queue=_LAUNCH_TASK_QUEUE,
            input_args={
                "workflowType": _USER_WORKFLOW.value,
                "initialParameters": parameters,
            },
        )
        if not initial_count:
            assert admitted_value == provider_profile_state_token("pending")

        original_patched = workflow.patched
        if legacy:
            monkeypatch.setattr(
                workflow,
                "patched",
                lambda name: (
                    False
                    if name == "provider-profile-complete-associations-v1"
                    else original_patched(name)
                ),
            )

        async with Worker(
            client,
            task_queue=_LAUNCH_TASK_QUEUE,
            workflows=[MoonMindUserWorkflow, _GrantedProfileAgentRun],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = client.get_workflow_handle(workflow_id)
            await handle.result()
            described = await handle.describe()
            history = await handle.fetch_history()

        # Replay both previously bounded histories and the new complete memo
        # update against the current workflow, with actual recorded commands.
        monkeypatch.setattr(workflow, "patched", original_patched)
        await Replayer(
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ).replay_workflow(history)

        recorded_memo = await described.memo_value(PROVIDER_PROFILE_MEMO_KEY)
        summary = provider_profile_summary_from_memo(
            {PROVIDER_PROFILE_MEMO_KEY: recorded_memo}
        )
        initial_profiles = [
            {"id": f"initial-{index}", "label": None, "harness": None}
            for index in range(initial_count)
        ]
        all_profiles = [
            *initial_profiles,
            {"id": _GRANTED_PROFILE, "label": "Work", "harness": None},
        ]
        assert summary == {
            "selectionState": "recorded",
            "profiles": all_profiles[:8],
            "profileCount": initial_count + 1,
        }
        assert len(recorded_memo["profiles"]) == (
            min(initial_count + 1, 8) if legacy else initial_count + 1
        )
        stored_value = next(
            pair.value
            for pair in described.typed_search_attributes
            if pair.key.name == PROVIDER_PROFILE_SEARCH_ATTRIBUTE
        )
        assert stored_value.split() == [
            provider_profile_state_token("recorded"),
            *[
                provider_profile_id_token(f"initial-{index}")
                for index in range(initial_count)
            ],
            provider_profile_id_token(_GRANTED_PROFILE),
        ]

        # The production list filters now find it by the granted Profile and no
        # longer report it as an unresolved selection.
        async def matches(params: Sequence[tuple[str, str]]) -> list[str]:
            _count_query, list_query = _production_query(
                params, owner_id=owner_id, usable_search_attributes=usable
            )
            return await _listed_ids(client, list_query)

        async with asyncio.timeout(30):
            while await matches([("providerProfileIn", _GRANTED_PROFILE)]) != [
                workflow_id
            ]:
                await asyncio.sleep(0.2)
        assert await matches([("providerProfileStateIn", "pending")]) == []
        assert await matches([("providerProfileBlank", "false")]) == [workflow_id]
