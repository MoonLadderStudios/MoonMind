"""Issue #5047: real artifact storage and scheduled native tool boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.security_assessment import (
    register_security_assessment_tool_handler,
)
from moonmind.workflows.skills.tool_definitions import default_registry_tool_payload
from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
from moonmind.workflows.skills.tool_registry import (
    create_registry_snapshot,
    parse_tool_registry,
)
from moonmind.workflows.temporal.activity_runtime import TemporalSkillActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.report_artifacts import build_report_projection_summary
from tests.unit.services.test_recurring_workflows_service import recurring_db

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
SCOPE = {"assets": ["app"], "exclusions": [], "repositoryExceptions": []}


@pytest_asyncio.fixture
async def environment(tmp_path):
    async with recurring_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "retained"),
            )
            yield SimpleNamespace(
                service=service,
                session=session,
                sessions=sessions,
                storage_root=tmp_path / "retained",
            )


@pytest_asyncio.fixture
async def temporal_environment():
    from temporalio.testing import WorkflowEnvironment

    from moonmind.workflows.temporal.data_converter import (
        MOONMIND_TEMPORAL_DATA_CONVERTER,
    )
    from tests.integration.workflows.temporal.workflows.test_merge_automation_full_topology import (
        _register_search_attributes,
    )

    async with await WorkflowEnvironment.start_local(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        await _register_search_attributes(env)
        yield env


async def put(service, payload, *, workflow_id="assessment", run_id="run"):
    artifact, _ = await service.create(
        principal="operator",
        content_type="application/json",
        link={
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "link_type": "input.security",
        },
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal="operator",
        payload=payload if isinstance(payload, bytes) else json.dumps(payload).encode(),
    )
    return artifact.artifact_id


async def inputs(service, *, rules=True):
    now = datetime.now(UTC)
    times = {
        "observedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=1)).isoformat(),
    }
    inventory = {
        "schemaVersion": "security-inventory/v1",
        "scope": SCOPE,
        "status": "complete",
        "configRef": "config:default",
        **times,
        "components": [{"asset": "app", "purl": "pkg:pypi/example", "version": "1.0"}],
    }
    feed = {
        "schemaVersion": "security-feed/v1",
        "source": "fixture-advisories",
        "revision": "feed-1",
        **times,
        "rules": (
            [
                {
                    "id": "CVE-EXAMPLE",
                    "purl": "pkg:pypi/example",
                    "affectedVersions": ["1.0"],
                }
            ]
            if rules
            else []
        ),
    }
    request = {
        "scope": SCOPE,
        "tools": ["inventory-feed"],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "inventoryRef": await put(service, inventory),
        "feedRef": await put(service, feed),
    }
    return request, inventory, feed


async def invoke(
    service, request, *, workflow_id="assessment", run_id="run", key="occurrence"
):
    dispatcher = ToolActivityDispatcher()
    register_security_assessment_tool_handler(dispatcher)
    snapshot = create_registry_snapshot(
        skills=parse_tool_registry(
            {"tools": [default_registry_tool_payload(name="security.assess")]}
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    result = await TemporalSkillActivities(
        dispatcher=dispatcher, artifact_service=service
    ).mm_skill_execute(
        invocation_payload={
            "id": "assess",
            "tool": {"name": "security.assess"},
            "inputs": request,
        },
        registry_snapshot=snapshot,
        principal=f"workflow:{workflow_id}",
        idempotency_key=key,
        context={
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "node_id": "assess",
        },
    )
    assert result.status == "COMPLETED"
    return result.outputs


async def read(service, ref, *, principal="workflow:assessment"):
    _, raw = await service.read(
        artifact_id=ref, principal=principal, allow_restricted_raw=True
    )
    return json.loads(raw)


async def test_feed_update_reassesses_inventory_without_source_change(
    environment, monkeypatch
):
    service = environment.service
    request, inventory, feed = await inputs(service, rules=False)
    first = await invoke(service, request)
    assert first["counts"]["new"] == 0
    feed["revision"] = "feed-2"
    feed["rules"] = [
        {"id": "CVE-EXAMPLE", "purl": "pkg:pypi/example", "affectedVersions": ["1.0"]}
    ]
    request.update(feedRef=await put(service, feed), baselineRef=first["comparisonRef"])
    changed = await invoke(service, request, key="feed-event-2")
    assert changed["counts"]["new"] == 1
    assert await read(service, request["inventoryRef"]) == inventory
    comparison = await read(service, changed["comparisonRef"])
    finding = comparison["findings"][0]
    assert finding["finding"]["ruleId"] == "CVE-EXAMPLE"
    assert finding["finding"]["asset"] == "app"
    assert finding["finding"]["evidenceRefs"] == [
        request["inventoryRef"],
        request["feedRef"],
    ]

    def repeat_forbidden(*_args):
        raise AssertionError("confirmed scanner work repeated")

    monkeypatch.setattr(
        "moonmind.workflows.skills.security_assessment.observe_inventory",
        repeat_forbidden,
    )
    duplicate = await invoke(service, request, key="feed-event-2")
    assert duplicate["scanRef"] == changed["scanRef"]
    assert duplicate["comparisonRef"] == changed["comparisonRef"]
    assert (
        len(
            await service.list_for_execution(
                namespace="default",
                workflow_id="assessment",
                run_id="run",
                principal="workflow:assessment",
                link_type="security.scan",
            )
        )
        == 2
    )


async def test_authorized_baseline_preserves_finding_rule_asset_and_provenance(
    environment,
):
    service = environment.service
    request, _, _ = await inputs(service)
    result = await invoke(service, request)
    baseline = await read(service, result["comparisonRef"])
    assert baseline["observation"]["provenance"]["inventory"] == request["inventoryRef"]
    assert baseline["observation"]["provenance"]["feed"] == request["feedRef"]
    with pytest.raises(TemporalArtifactAuthorizationError):
        await read(service, result["comparisonRef"], principal="workflow:other")
    projection = build_report_projection_summary(result["reportBundle"])
    assert (
        projection["report_artifact_refs"]["report.structured"]["artifact_id"]
        == result["comparisonRef"]
    )
    summary_ref = projection["latest_report_ref"]["artifact_id"]
    _, summary = await service.read(
        artifact_id=summary_ref, principal="workflow:assessment"
    )
    assert b"new: 1" in summary and b"Coverage: complete" in summary


@pytest.mark.parametrize(
    "change",
    [
        "complete",
        "partial",
        "authentication_failed",
        "scope",
        "exclusions",
        "expired",
        "unsupported",
        "removed_components",
        "removed_components_partial",
    ],
)
async def test_complete_partial_changed_scope_and_auth_failure_matrix(
    environment, change
):
    service = environment.service
    request, inventory, feed = await inputs(service)
    first = await invoke(service, request)
    inventory["components"][0]["version"] = "2.0"
    if change in {"partial", "authentication_failed", "unsupported"}:
        inventory["status"] = change
    elif change == "scope":
        request["scope"] = {"assets": ["other"]}
        inventory["scope"] = request["scope"]
        inventory["components"][0]["asset"] = "other"
    elif change == "exclusions":
        request["scope"] = {**SCOPE, "exclusions": ["vendor"]}
        inventory["scope"] = request["scope"]
    elif change == "expired":
        inventory["expiresAt"] = datetime.now(UTC).isoformat()
    elif change in {"removed_components", "removed_components_partial"}:
        inventory["components"] = []
        if change == "removed_components_partial":
            inventory["status"] = "partial"
    request.update(
        inventoryRef=await put(service, inventory), baselineRef=first["comparisonRef"]
    )
    result = await invoke(service, request)
    comparison = await read(service, result["comparisonRef"])
    assert comparison["findings"][0]["state"] == (
        "absent_comparable"
        if change in {"complete", "removed_components"}
        else "not_reassessed"
    )
    assert comparison["findings"][0]["finding"]["evidenceRefs"] == [
        (await read(service, first["comparisonRef"]))["findings"][0]["finding"][
            "evidenceRefs"
        ][0],
        request["feedRef"],
    ]


async def test_complete_empty_inventory_covers_declared_scope(environment):
    service = environment.service
    request, inventory, _ = await inputs(service)
    request["scope"] = {"assets": ["app", "empty"]}
    inventory.update(scope=request["scope"], components=[])
    request["inventoryRef"] = await put(service, inventory)
    result = await invoke(service, request)
    assert result["scanOutcome"] == "completed"
    assert result["coverageStatus"] == "complete"
    comparison = await read(service, result["comparisonRef"])
    assert comparison["observation"]["coverage"][0]["assets"] == ["app", "empty"]
    assert comparison["findings"] == []


async def test_publication_failure_preserves_scan_and_retry_reuses_confirmed_work(
    environment, monkeypatch
):
    service = environment.service
    request, _, _ = await inputs(service)
    link = service.link_artifact

    async def unavailable(**kwargs):
        if kwargs["execution_ref"]["link_type"].startswith("report."):
            raise ConnectionError("publication unavailable")
        return await link(**kwargs)

    monkeypatch.setattr(service, "link_artifact", unavailable)
    result = await invoke(service, request)
    assert result["publicationOutcome"] == "failed"
    assert result["scanOutcome"] == "completed"
    assert len((await read(service, result["scanRef"]))["observation"]["findings"]) == 1
    monkeypatch.setattr(service, "link_artifact", link)

    def repeat_forbidden(*_args):
        raise AssertionError("scan rerun while only publication remained")

    monkeypatch.setattr(
        "moonmind.workflows.skills.security_assessment.observe_inventory",
        repeat_forbidden,
    )
    await environment.session.close()
    async with environment.sessions() as restarted_session:
        restarted = TemporalArtifactService(
            TemporalArtifactRepository(restarted_session),
            store=LocalTemporalArtifactStore(environment.storage_root),
        )
        retried = await invoke(restarted, request)
    assert retried["publicationOutcome"] == "published"
    assert retried["scanRef"] == result["scanRef"]
    assert retried["comparisonRef"] == result["comparisonRef"]


async def test_lost_scan_save_acknowledgement_reconciles_before_repeating_work(
    environment, monkeypatch
):
    from temporalio.exceptions import ApplicationError

    service = environment.service
    request, _, _ = await inputs(service)
    publish = service.put_content_addressed_payload_complete
    accepted = []

    async def lost_ack(**kwargs):
        result = await publish(**kwargs)
        if kwargs["link"]["link_type"] == "security.scan":
            accepted.append(result[0].artifact_id)
            raise ConnectionError("lost acknowledgement after durable scanner save")
        return result

    monkeypatch.setattr(service, "put_content_addressed_payload_complete", lost_ack)
    with pytest.raises(ApplicationError, match="lost acknowledgement"):
        await invoke(service, request)
    assert len(accepted) == 1
    monkeypatch.setattr(service, "put_content_addressed_payload_complete", publish)

    def repeat_forbidden(*_args):
        raise AssertionError("completed scanner work was not reconciled")

    monkeypatch.setattr(
        "moonmind.workflows.skills.security_assessment.observe_inventory",
        repeat_forbidden,
    )
    recovered = await invoke(service, request)
    assert recovered["scanRef"] == accepted[0]
    assert recovered["publicationOutcome"] == "published"


async def test_duplicate_feed_in_later_occurrence_reuses_admitted_baseline(
    environment, monkeypatch
):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    request["baselineRef"] = first["comparisonRef"]
    for ref in (request["inventoryRef"], request["feedRef"], first["comparisonRef"]):
        await service.link_artifact(
            artifact_id=ref,
            principal="operator",
            execution_ref={
                "namespace": "default",
                "workflow_id": "next-occurrence",
                "run_id": "next-run",
                "link_type": "input.security",
            },
        )

    def repeat_forbidden(*_args):
        raise AssertionError("duplicate feed delivery repeated the scanner")

    monkeypatch.setattr(
        "moonmind.workflows.skills.security_assessment.observe_inventory",
        repeat_forbidden,
    )
    repeated = await invoke(
        service, request, workflow_id="next-occurrence", run_id="next-run"
    )
    assert repeated["counts"]["still_observed"] == 1
    assert (await read(service, first["scanRef"]))["scanOutcome"] == "completed"


@pytest.mark.parametrize("body", [b'{"findings": []}', b'{"findings":'])
async def test_malformed_report_preserves_baseline_and_original_evidence(
    environment, body
):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    broken = {
        k: copy.deepcopy(request[k])
        for k in ("scope", "budget", "credentialRefs", "networkRefs")
    }
    broken.update(
        tools=["retained-report"],
        reportRefs=[await put(service, body)],
        baselineRef=first["comparisonRef"],
    )
    result = await invoke(service, broken)
    assert result["scanOutcome"] == "incomplete"
    comparison = await read(service, result["comparisonRef"])
    assert comparison["findings"][0]["state"] == "not_reassessed"
    assert len((await read(service, first["scanRef"]))["observation"]["findings"]) == 1


async def test_missing_baseline_never_claims_new_or_resolved(environment):
    request, _, _ = await inputs(environment.service)
    request["baselineRef"] = "art_unavailable"
    result = await invoke(environment.service, request)
    assert result["scanOutcome"] == "completed"
    assert result["baselineStatus"] == "unavailable"
    assert result["counts"]["new"] == result["counts"]["absent_comparable"] == 0
    assert result["counts"]["observed_without_baseline"] == 1


async def test_missing_baseline_bytes_publish_current_assessment(environment):
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactBytesMissingError,
    )

    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    baseline, _ = await service.read(
        artifact_id=first["comparisonRef"], principal="workflow:assessment"
    )
    LocalTemporalArtifactStore(environment.storage_root).delete(baseline.storage_key)
    with pytest.raises(TemporalArtifactBytesMissingError):
        await read(service, first["comparisonRef"])

    request["baselineRef"] = first["comparisonRef"]
    result = await invoke(service, request)
    assert result["scanOutcome"] == "completed"
    assert result["baselineStatus"] == "unavailable"
    assert result["counts"]["new"] == result["counts"]["absent_comparable"] == 0
    assert result["counts"]["observed_without_baseline"] == 1
    assert result["publicationOutcome"] == "published"
    assert {"boundary": "baseline", "error": "TemporalArtifactBytesMissingError"} in result[
        "diagnostics"
    ]


async def test_triage_dispositions_expire_into_review(environment, monkeypatch):
    from moonmind.workflows.skills import security_assessment as module

    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    comparison = await read(service, first["comparisonRef"])
    now = datetime.now(UTC)
    decision = {
        "findingId": comparison["findings"][0]["findingId"],
        "disposition": "accepted_risk",
        "reason": "Time bounded local exception",
        "evidenceRefs": [first["scanRef"]],
        "author": "operator",
        "source": "operator",
        "scope": SCOPE,
        "reviewAt": (now + timedelta(hours=1)).isoformat(),
        "reviewConditions": ["feed or scope change"],
    }
    request.update(
        decisionsRef=await put(service, [decision]), baselineRef=first["comparisonRef"]
    )
    active = await invoke(service, request)
    assert active["counts"]["review_required"] == 0

    class Later(datetime):
        @classmethod
        def now(cls, tz=UTC):
            return now + timedelta(hours=2)

    monkeypatch.setattr(module, "datetime", Later)
    request["baselineRef"] = active["comparisonRef"]
    expired = await invoke(service, request)
    assert expired["counts"]["review_required"] == 1
    result = await read(service, expired["comparisonRef"])
    assert result["findings"][0]["state"] == "still_observed"
    assert result["decisions"][0]["decision"]["author"] == "operator"


async def test_passive_report_composes_existing_native_scanner(environment, tmp_path):
    from moonmind.security.passive_repo_scan import run_passive_scan

    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "app.txt").write_text("password=example-fixture\n")
    native = run_passive_scan(root)
    assert native.verdict == "finding_present"
    request = {
        "scope": {"assets": ["app.txt"]},
        "tools": ["retained-report"],
        "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
        "credentialRefs": [],
        "networkRefs": [],
        "reportRefs": [await put(environment.service, native.to_payload())],
    }
    result = await invoke(environment.service, request)
    comparison = await read(environment.service, result["comparisonRef"])
    assert comparison["findings"][0]["finding"]["ruleId"] == "credential"
    assert comparison["findings"][0]["finding"]["asset"] == "app.txt"
    assert "example-fixture" not in json.dumps(comparison)


async def test_multiple_reports_preserve_evidence_for_one_native_finding(environment):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    observation = (await read(service, first["scanRef"]))["observation"]
    another = copy.deepcopy(observation)
    another["findings"][0]["evidenceRefs"] = [first["scanRef"]]
    combined = {
        key: request[key]
        for key in ("scope", "budget", "credentialRefs", "networkRefs")
    }
    combined.update(
        tools=["retained-report"],
        reportRefs=[await put(service, observation), await put(service, another)],
    )
    result = await invoke(service, combined)
    finding = (await read(service, result["comparisonRef"]))["findings"][0]
    assert result["counts"]["new"] == 1
    assert set(finding["finding"]["evidenceRefs"]) == {
        request["inventoryRef"],
        request["feedRef"],
        first["scanRef"],
    }


async def test_many_report_evidence_refs_remain_bounded_and_reachable(environment):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    observation = (await read(service, first["scanRef"]))["observation"]
    reports = []
    for index in range(3):
        report = copy.deepcopy(observation)
        report["findings"][0]["evidenceRefs"] = [
            f"art_evidence_{index}_{item}" for item in range(33)
        ]
        reports.append(await put(service, report))
    request = {
        **{
            key: request[key]
            for key in ("scope", "budget", "credentialRefs", "networkRefs")
        },
        "tools": ["retained-report"],
        "reportRefs": reports,
    }
    result = await invoke(service, request)
    assert result["scanOutcome"] == "completed"
    assert result["publicationOutcome"] == "published"
    comparison = await read(service, result["comparisonRef"])
    evidence = comparison["findings"][0]["finding"]["evidenceRefs"]
    assert 1 <= len(evidence) <= 64
    reachable = set(evidence)
    for ref in set(reports) & reachable:
        retained = await read(service, ref)
        reachable.update(retained["findings"][0]["evidenceRefs"])
    assert reachable >= {
        f"art_evidence_{index}_{item}" for index in range(3) for item in range(33)
    }


async def test_activity_retry_keeps_the_original_flat_budget(environment):
    from dataclasses import replace

    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    request, _, _ = await inputs(environment.service)
    request["budget"]["mode"] = "flat"
    activity_environment = ActivityEnvironment()
    activity_environment.info = replace(
        activity_environment.info,
        attempt=2,
        scheduled_time=datetime.now(UTC) - timedelta(seconds=90),
    )
    with pytest.raises(
        ApplicationError, match="Original assessment budget is exhausted"
    ):
        await activity_environment.run(invoke, environment.service, request)
    assert not await environment.service.list_for_execution(
        namespace="default",
        workflow_id="assessment",
        run_id="run",
        principal="workflow:assessment",
        link_type="security.scan",
    )


async def test_occurrence_revalidates_current_authority_before_reusing_scan(
    environment,
):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    await service.soft_delete(artifact_id=request["inventoryRef"], principal="operator")
    with pytest.raises(Exception):
        await invoke(service, request)
    assert (await read(service, first["comparisonRef"]))["findings"][0][
        "state"
    ] == "new"


async def test_cancellation_starts_no_successor_and_retains_completed_work(
    environment, monkeypatch
):
    service = environment.service
    request, _, feed = await inputs(service)
    first = await invoke(service, request)
    feed["revision"] = "feed-2"
    request["feedRef"] = await put(service, feed)
    entered = asyncio.Event()
    read_input = service.read

    async def interrupted(**kwargs):
        if kwargs["artifact_id"] == request["feedRef"]:
            entered.set()
            await asyncio.Event().wait()
        return await read_input(**kwargs)

    monkeypatch.setattr(service, "read", interrupted)
    task = asyncio.create_task(invoke(service, request))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await read(service, first["scanRef"]))["scanOutcome"] == "completed"


@pytest.mark.parametrize("authored_runtime", ["codex_cli", None])
async def test_product_api_security_schedule_lifecycle(
    environment, temporal_environment, monkeypatch, authored_runtime
):
    """Real HTTP -> service/DB -> Temporal occurrence -> planner/Activity/artifact.

    The occurrence remains unpolled while overlap is exercised. Its recorded
    start then drives the production planning/admission and tool Activity.
    """
    from api_service.api.routers import recurring_workflows as routes
    from api_service.db.base import get_async_session
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.activity_runtime import TemporalPlanActivities
    from moonmind.workflows.temporal.client import TemporalClientAdapter
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    client = temporal_environment.client
    adapter = TemporalClientAdapter(client=client)
    queue = "security-schedule-" + uuid4().hex
    adapter._get_task_queue = lambda *_a, **_kw: queue
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    user = SimpleNamespace(id=uuid4(), is_superuser=True)
    service = environment.service
    request, _, _ = await inputs(service)
    app = FastAPI()
    app.include_router(routes.router)

    async def get_service():
        yield RecurringWorkflowsService(
            environment.session,
            temporal_client_adapter=adapter,
            artifact_service=service,
        )

    async def get_session():
        yield environment.session

    app.dependency_overrides[routes._get_service] = get_service
    app.dependency_overrides[get_async_session] = get_session
    for route in routes.router.routes:
        for dependency in getattr(
            getattr(route, "dependant", None), "dependencies", ()
        ):
            if dependency.name == "user":
                app.dependency_overrides[dependency.call] = lambda: user
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "initialParameters": {
            "publishMode": "none",
            "workflow": {
                "instructions": "Compare admitted security evidence",
                "publish": {"mode": "none"},
                "inputAttachments": [
                    {"artifactId": request[key]} for key in ("inventoryRef", "feedRef")
                ],
                "steps": [
                    {
                        "id": "assess",
                        "type": "tool",
                        "tool": {
                            "type": "skill",
                            "name": "security.assess",
                            "inputs": request,
                        },
                    }
                ],
            },
        },
    }
    if authored_runtime is not None:
        target["initialParameters"]["targetRuntime"] = authored_runtime
    schedule = None
    handle = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            created = await http.post(
                "/api/recurring-workflows",
                json={
                    "name": "Security inventory reassessment",
                    "cron": "0 6 * * *",
                    "target": target,
                    "policy": {
                        "overlap": {"mode": "skip"},
                        "catchup": {"mode": "last"},
                    },
                },
            )
            assert created.status_code == 201, created.text
            schedule = client.get_schedule_handle(created.json()["temporalScheduleId"])
            description = await schedule.describe()
            assert description.schedule.policy.overlap.name == "SKIP"
            assert description.schedule.policy.catchup_window == timedelta(minutes=15)
            endpoint = "/api/recurring-workflows/" + created.json()["id"] + "/run"
            headers = {"Idempotency-Key": str(uuid4())}
            admitted = await http.post(endpoint, headers=headers)
            duplicate = await http.post(endpoint, headers=headers)
            assert admitted.status_code == duplicate.status_code == 201
            assert admitted.json()["id"] == duplicate.json()["id"]
            for _ in range(100):
                actions = (await schedule.describe()).info.recent_actions
                if actions:
                    break
                await asyncio.sleep(0.05)
            assert len(actions) == 1
            action = actions[0].action
            handle = client.get_workflow_handle(action.workflow_id)
            recorded = await adapter.read_workflow_start_input(
                action.workflow_id, run_id=action.first_execution_run_id
            )
            parameters = recorded["initial_parameters"]
            assert parameters["workflow"]["steps"][0]["tool"]["inputs"] == request
            overlap = await http.post(
                endpoint, headers={"Idempotency-Key": str(uuid4())}
            )
            assert overlap.status_code == 201, overlap.text
            assert overlap.json()["outcome"] == "skipped"

            execution = {
                "namespace": "default",
                "workflow_id": action.workflow_id,
                "run_id": action.first_execution_run_id,
            }

            # Exercise the same occurrence input admission used by the parent,
            # including real execution-scoped attachment links.
            async def execute(name, payload, **_kwargs):
                assert name == "artifact.link"
                return await service.link_artifact(**payload)

            with patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=SimpleNamespace(
                    **{
                        "namespace": "default",
                        "workflow_id": action.workflow_id,
                        "run_id": action.first_execution_run_id,
                        "continued_run_id": None,
                    }
                ),
            ), patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ), patch(
                "moonmind.workflows.temporal.workflows.run.workflow.execute_activity",
                side_effect=execute,
            ):
                parent = MoonMindRunWorkflow()
                await parent._run_planning_stage(
                    parameters=parameters,
                    input_ref=None,
                    plan_ref="recorded-native-plan",
                )
            planned = await TemporalPlanActivities(
                artifact_service=service, planner=_build_runtime_planner()
            ).plan_generate(
                principal=f"workflow:{action.workflow_id}",
                parameters=parameters,
                execution_ref={**execution, "link_type": "plan"},
            )
            plan = await read(
                service,
                planned.plan_ref.artifact_id,
                principal=f"workflow:{action.workflow_id}",
            )
            node = plan["nodes"][0]
            assert node["tool"]["name"] == "security.assess"
            result = await invoke(
                service,
                node["inputs"],
                workflow_id=action.workflow_id,
                run_id=action.first_execution_run_id,
            )
            assert result["publicationOutcome"] == "published"
            assert (await read(service, result["comparisonRef"], principal="operator"))[
                "counts"
            ]["new"] == 1
            await handle.cancel()
            history = await handle.fetch_history()
            assert any(
                event.HasField("workflow_execution_cancel_requested_event_attributes")
                for event in history.events
            )
            assert len((await schedule.describe()).info.recent_actions) == 1
    finally:
        if handle is not None:
            await handle.terminate("Security schedule fixture cleanup")
        if schedule is not None:
            await schedule.delete()
