"""Issue #5047: real artifact storage and scheduled native tool boundaries."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from temporalio import activity
from temporalio.testing import ActivityEnvironment

from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.skills import security_assessment
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
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.report_artifacts import build_report_projection_summary
from tests.artifact_access import access_for_test
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

    async def execute():
        return await TemporalSkillActivities(
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

    if activity.in_activity():
        # Keep the real worker's original schedule/budget and execution identity.
        result = await execute()
    else:
        # These fixtures invoke a workflow Activity implementation directly.
        # Bind its real SDK context instead of treating authored context fields
        # or the principal string as permission to read retained evidence.
        environment = ActivityEnvironment()
        # The SDK's default timestamps are the Unix epoch. This newly simulated
        # Activity starts now; real worker retries above retain their originals.
        # Follow the handler's injected clock for metadata-expiry scenarios;
        # time travel must not age a newly created synthetic Activity.
        started = security_assessment.datetime.now(UTC)
        environment.info = replace(
            environment.info,
            namespace="default",
            workflow_id=workflow_id,
            workflow_run_id=run_id,
            scheduled_time=started,
            current_attempt_scheduled_time=started,
            started_time=started,
        )
        result = await environment.run(execute)
    assert result.status == "COMPLETED"
    return result.outputs


async def read(service, ref, *, principal="workflow:assessment"):
    _, raw = await service.read(artifact_id=ref, access=access_for_test(principal))
    return json.loads(raw)


def report_ref(outputs):
    return build_report_projection_summary(outputs["reportBundle"])[
        "latest_report_ref"
    ]["artifact_id"]


async def required(service, root):
    sealed = await service._repository.get_verified_dependencies(root)
    return {entry.artifact_id for entry in sealed if entry.required}


async def age_everything(environment):
    """Pass every artifact's own expiry; pins and sealed live roots remain."""
    from sqlalchemy import select

    from api_service.db.models import TemporalArtifact

    past = datetime.now(UTC) - timedelta(days=1)
    for row in await environment.session.scalars(select(TemporalArtifact)):
        row.expires_at = past
    await environment.session.commit()


async def status(environment, ref):
    from api_service.db.models import TemporalArtifact

    row = await environment.session.get(TemporalArtifact, ref, populate_existing=True)
    return row.status.value


async def forget_seals(environment):
    """Return every root to its shape before producers sealed assessments."""
    from sqlalchemy import delete, select

    from api_service.db.models import TemporalArtifact, TemporalArtifactDependency

    await environment.session.execute(delete(TemporalArtifactDependency))
    for row in await environment.session.scalars(select(TemporalArtifact)):
        row.dependency_request_sha256 = None
        row.dependency_closure_version = None
        row.dependency_closure_sha256 = None
    await environment.session.commit()


def decision(finding_id, evidence_refs):
    return {
        "findingId": finding_id,
        "disposition": "fix_retest",
        "reason": "Upgraded the package and reran the check",
        "evidenceRefs": evidence_refs,
        "author": "operator",
        "source": "security-remediate",
        "scope": SCOPE,
        "reviewAt": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "reviewConditions": ["feed or scope change"],
        "retestOutcome": "passed",
    }


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
                link_type="security.scan",
                access=access_for_test("workflow:assessment"),
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
        artifact_id=summary_ref, access=access_for_test("workflow:assessment")
    )
    assert b"new: 1" in summary and b"Coverage: complete" in summary


async def product_inputs(service):
    """Real retained artifacts for the #5193 comparison and export boundary."""
    from tests.unit.security.test_assessment_comparison import product_evidence, report

    request, _, _ = await inputs(service)
    baseline, deployment = product_evidence()
    component_ref = await put(service, {"snapshot": "synthetic component identities"})
    for component in baseline["components"] + deployment["components"]:
        component["evidenceRefs"] = [component_ref]
    for record in baseline["records"]:
        record["evidenceRefs"] = [await put(service, {"record": record})]
    observed = report(deployment=deployment, findings=False)
    observed.update(
        observedAt=datetime.now(UTC).isoformat(),
        expiresAt=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    request.update(
        tools=["inventory-feed", "retained-report"],
        reportRefs=[await put(service, observed)],
        productBaselineRef=await put(service, baseline),
    )
    return request, baseline, observed


async def test_product_baseline_retry_export_retains_scope_identity_and_evidence(
    environment,
    monkeypatch,
):
    from api_service.api.routers import temporal_artifacts as routes

    service = environment.service
    request, baseline, observed = await product_inputs(service)
    await record_finished(environment, "assessment")
    observed["deployment"]["components"][2]["identity"] = "external-paytable-changed"
    observed["deployment"]["components"].pop()
    observed["deployment"]["omissions"] = ["External database inaccessible"]
    request["reportRefs"] = [await put(service, observed)]
    link = service.link_artifact

    async def unavailable(**kwargs):
        if kwargs["execution_ref"]["link_type"].startswith("report."):
            raise ConnectionError("publication unavailable")
        return await link(**kwargs)

    monkeypatch.setattr(service, "link_artifact", unavailable)
    first = await invoke(service, request)
    assert first["publicationOutcome"] == "failed"
    saved = await read(service, first["comparisonRef"])
    product = saved["productComparison"]
    assert product["baselineRef"] == request["productBaselineRef"]
    assert product["baseline"] == baseline
    assert product["status"] == "incomplete"
    assert product["components"][2]["status"] == "drift"
    assert product["components"][-1]["status"] == "missing"
    assert (
        product["components"][2]["observed"]["identity"] == "external-paytable-changed"
    )
    assert product["omissions"] == ["External database inaccessible"]
    assert request["productBaselineRef"] in await required(
        service, first["comparisonRef"]
    )
    assert {r["evidenceRefs"][0] for r in baseline["records"]} <= await required(
        service, first["comparisonRef"]
    )
    assert baseline["components"][0]["evidenceRefs"][0] in await required(
        service, first["scanRef"]
    )

    def repeat_forbidden(*args):
        raise AssertionError("completed observation repeated during report recovery")

    monkeypatch.setattr(security_assessment, "observe_inventory", repeat_forbidden)
    await environment.session.close()
    async with environment.sessions() as session:
        restarted = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(environment.storage_root),
        )
        retry = await invoke(restarted, request)
        assert retry["scanRef"] == first["scanRef"]
        assert retry["comparisonRef"] == first["comparisonRef"]
        assert retry["publicationOutcome"] == "published"
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[routes._get_temporal_artifact_service] = (
            lambda: restarted
        )
        app.dependency_overrides[routes.get_instance_access_optional] = (
            lambda: OperatorAdmission(via="loopback")
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            exported = await client.get(
                f"/api/artifacts/{retry['comparisonRef']}/download"
            )
            assert exported.status_code == 200
            assert exported.json()["productComparison"] == product
            readable = await client.get(f"/api/artifacts/{report_ref(retry)}/download")
            assert readable.status_code == 200
            for text in (
                request["productBaselineRef"],
                "external-paytable-changed",
                "External database inaccessible",
                "drift",
                "missing",
                "Unverified",
                "fixture snapshot signature",
                "certification",
            ):
                assert text in readable.text
        with pytest.raises(TemporalArtifactAuthorizationError):
            await read(restarted, retry["comparisonRef"], principal="workflow:other")


async def test_product_new_baseline_reuses_scan_and_keeps_old_artifacts(
    environment, monkeypatch
):
    service = environment.service
    request, baseline, _ = await product_inputs(service)
    first = await invoke(service, request)
    _, original = await service.read(
        artifact_id=first["comparisonRef"],
        access=access_for_test("workflow:assessment"),
    )
    baseline["baselineId"] = "newer-approved-release"
    for record in baseline["records"]:
        record["baselineId"] = baseline["baselineId"]
        record["evidenceRefs"] = [await put(service, {"record": record})]
    request["productBaselineRef"] = await put(service, baseline)

    def repeat_forbidden(*args):
        raise AssertionError("baseline selection reran a completed scan")

    monkeypatch.setattr(security_assessment, "observe_inventory", repeat_forbidden)
    second = await invoke(service, request)
    assert first["scanRef"] == second["scanRef"]
    assert first["comparisonRef"] != second["comparisonRef"]
    product = (await read(service, second["comparisonRef"]))["productComparison"]
    assert product["status"] == "match"
    assert product["baseline"]["baselineId"] == "newer-approved-release"
    _, unchanged = await service.read(
        artifact_id=first["comparisonRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert unchanged == original


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "denied",
        "missing_baseline",
        "url_only",
        "missing_bytes",
        "unavailable_backend",
    ],
)
async def test_product_unreadable_approval_publishes_unverified_evidence(
    environment, failure, monkeypatch
):
    service = environment.service
    request, baseline, _ = await product_inputs(service)
    approval = baseline["records"][-1]
    if failure == "missing":
        approval["evidenceRefs"] = ["art_01K00000000000000000000000"]
    elif failure == "denied":
        approval["evidenceRefs"] = [
            await put(service, {"private": "approval"}, workflow_id="other")
        ]
    elif failure == "url_only":
        approval["evidenceRefs"] = ["https://example.invalid/private-record"]
    elif failure == "missing_bytes":
        artifact, _ = await service.read(
            artifact_id=approval["evidenceRefs"][0],
            access=access_for_test("workflow:assessment"),
        )
        LocalTemporalArtifactStore(environment.storage_root).delete(
            artifact.storage_key
        )
    elif failure == "unavailable_backend":
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactUnavailableError,
        )

        original_read = service.read

        async def unavailable(**kwargs):
            if kwargs["artifact_id"] == approval["evidenceRefs"][0]:
                raise TemporalArtifactUnavailableError(
                    kwargs["artifact_id"], dependency="storage", operation="read"
                )
            return await original_read(**kwargs)

        monkeypatch.setattr(service, "read", unavailable)
    request["productBaselineRef"] = (
        "art_01K00000000000000000000000"
        if failure == "missing_baseline"
        else await put(service, baseline)
    )
    result = await invoke(service, request)
    assert result["publicationOutcome"] == "published"
    product = (await read(service, result["comparisonRef"]))["productComparison"]
    assert product["status"] == "incomplete"
    assert product["approvalStatus"] == "unverified"
    assert product["reasons"]
    assert product["externalVerification"]["status"] == "Unverified"
    if failure != "missing_baseline":
        assert product["baseline"]["records"][-1]["status"] == "unverified"
        assert (
            product["baseline"]["records"][-1]["evidenceRefs"]
            == approval["evidenceRefs"]
        )


async def test_product_evidence_survives_lifecycle_backfill(environment):
    service = environment.service
    request, baseline, _ = await product_inputs(service)
    result = await invoke(service, request)
    comparison_ref = result["comparisonRef"]
    evidence = {
        request["productBaselineRef"],
        baseline["components"][0]["evidenceRefs"][0],
    }
    evidence |= {record["evidenceRefs"][0] for record in baseline["records"]}
    await forget_seals(environment)
    await service.pin(
        artifact_id=comparison_ref, principal="operator", reason="retained comparison"
    )
    await service.seal_admitted_assessment_root(
        root_artifact_id=comparison_ref, access=access_for_test("workflow:assessment")
    )
    assert evidence <= await required(service, comparison_ref)
    await age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in evidence:
        assert await status(environment, ref) == "complete"
        assert await read(service, ref)


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


async def test_pinned_comparison_retains_cited_evidence_until_released(environment):
    """Issue #5155: a retained comparison keeps exactly what it cites."""
    service = environment.service
    request, inventory, _ = await inputs(service)
    runs = [await invoke(service, request)]
    for _ in range(2):
        request.update(
            inventoryRef=await put(service, inventory),
            baselineRef=runs[-1]["comparisonRef"],
        )
        runs.append(await invoke(service, request))
    first, second, third = runs
    pinned = third["comparisonRef"]
    cited = set((await read(service, pinned))["findings"][0]["finding"]["evidenceRefs"])
    assert len(cited) == 4  # Each run's inventory and the shared feed.
    retained = cited | {third["scanRef"], second["comparisonRef"]}
    # The baseline's bytes are necessary history; its own lineage is not.
    released = {first["comparisonRef"], first["scanRef"], second["scanRef"]}
    assert await required(service, pinned) == retained
    await service.pin(artifact_id=pinned, principal="operator", reason="audit")
    await age_everything(environment)
    for ref in sorted(retained):
        with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
            await service.soft_delete(artifact_id=ref, principal="operator")
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in retained:
        assert await status(environment, ref) == "complete"
        assert await read(service, ref)
    for ref in released:
        assert await status(environment, ref) == "deleted"

    await service.unpin(artifact_id=pinned, principal="operator")
    await age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in retained | {pinned}:
        assert await status(environment, ref) == "deleted"


async def test_pinned_report_retains_admitted_triage_and_its_evidence(environment):
    """A comparison seals the decisions it admits; successors carry them."""
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    finding_id = (await read(service, first["comparisonRef"]))["findings"][0][
        "findingId"
    ]
    retest = await put(service, {"retest": "passed"})
    request.update(
        decisionsRef=await put(service, [decision(finding_id, [retest])]),
        baselineRef=first["comparisonRef"],
    )
    triaged = await invoke(service, request, key="triaged")
    admitted = {request["decisionsRef"], retest}
    report = report_ref(triaged)
    assert admitted <= await required(service, triaged["comparisonRef"])
    assert admitted <= await required(service, report)
    request.pop("decisionsRef")
    request["baselineRef"] = triaged["comparisonRef"]
    carried = await invoke(service, request, key="carried")
    assert (await read(service, carried["comparisonRef"]))["decisions"]
    # The carried decision keeps its evidence through the baseline's seal; the
    # decisions input belongs to the comparison that consumed it.
    carried_seal = await required(service, carried["comparisonRef"])
    assert retest in carried_seal
    assert request["inventoryRef"] in carried_seal
    assert (admitted - {retest}).isdisjoint(carried_seal)

    await service.pin(artifact_id=report, principal="operator", reason="audit")
    await age_everything(environment)
    for ref in sorted(admitted):
        with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
            await service.soft_delete(artifact_id=ref, principal="operator")
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in admitted:
        assert await status(environment, ref) == "complete"
        assert await read(service, ref)

    await service.unpin(artifact_id=report, principal="operator")
    await age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in admitted | {report}:
        assert await status(environment, ref) == "deleted"


@pytest.mark.parametrize("fault", ["missing", "denied", "corrupt"])
async def test_unverifiable_decision_evidence_is_never_certified(environment, fault):
    from temporalio.exceptions import ApplicationError

    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    finding_id = (await read(service, first["comparisonRef"]))["findings"][0][
        "findingId"
    ]
    if fault == "missing":
        evidence = "art_missing_retest"
    elif fault == "denied":
        evidence = await put(service, {"retest": "passed"}, workflow_id="other")
    else:
        evidence = await put(service, {"retest": "passed"})
        artifact, _ = await service.read(
            artifact_id=evidence, access=access_for_test("workflow:assessment")
        )
        LocalTemporalArtifactStore(environment.storage_root).resolve_storage_key(
            artifact.storage_key
        ).write_bytes(b'{"retest": "failed"}')
    request.update(
        decisionsRef=await put(service, [decision(finding_id, [evidence])]),
        baselineRef=first["comparisonRef"],
    )
    with pytest.raises(ApplicationError):
        await invoke(service, request, run_id="triage")
    for comparison in await service.list_for_execution(
        namespace="default",
        workflow_id="assessment",
        run_id="triage",
        link_type="security.comparison",
        access=access_for_test("workflow:assessment"),
    ):
        assert (
            await service._repository.get_verified_dependencies(comparison.artifact_id)
            is None
        )


@pytest.mark.parametrize("legacy", ["sealable", "unverifiable"])
async def test_unsealed_baseline_is_sealed_before_admission_or_unavailable(
    environment, legacy
):
    """A baseline without a seal never stands in for verified-empty history."""
    service = environment.service
    request, inventory, _ = await inputs(service)
    previous = await invoke(service, request)
    legacy_inventory = request["inventoryRef"]
    await forget_seals(environment)
    if legacy == "unverifiable":
        missing, _ = await service.read(
            artifact_id=legacy_inventory,
            access=access_for_test("workflow:assessment"),
        )
        LocalTemporalArtifactStore(environment.storage_root).delete(missing.storage_key)
    request.update(
        inventoryRef=await put(service, inventory),
        baselineRef=previous["comparisonRef"],
    )
    successor = await invoke(service, request, key="successor")
    pinned = successor["comparisonRef"]
    sealed = await required(service, pinned)
    baseline_seal = await service._repository.get_verified_dependencies(
        previous["comparisonRef"]
    )
    if legacy == "unverifiable":
        assert successor["baselineStatus"] == "unavailable"
        assert successor["counts"]["observed_without_baseline"] == 1
        assert any(row["boundary"] == "baseline" for row in successor["diagnostics"])
        assert baseline_seal is None
        assert previous["comparisonRef"] not in sealed
        return
    assert successor["baselineStatus"] == "available"
    assert baseline_seal is not None
    cited = set((await read(service, pinned))["findings"][0]["finding"]["evidenceRefs"])
    assert legacy_inventory in cited
    assert {legacy_inventory, previous["comparisonRef"]} <= sealed
    await service.pin(artifact_id=pinned, principal="operator", reason="audit")
    await age_everything(environment)
    with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
        await service.soft_delete(artifact_id=legacy_inventory, principal="operator")
    await service.sweep_lifecycle(principal="service:sweeper")
    assert await status(environment, legacy_inventory) == "complete"


@pytest.mark.parametrize("fault", ["corrupt", "deleted"])
async def test_unverifiable_required_evidence_is_never_sealed(
    environment, monkeypatch, fault
):
    from temporalio.exceptions import ApplicationError

    service = environment.service
    request, inventory, _ = await inputs(service)
    target = request["inventoryRef"]
    if fault == "corrupt":
        artifact, _ = await service.read(
            artifact_id=target, access=access_for_test("workflow:assessment")
        )
        inventory["components"][0]["version"] = "1.1"
        LocalTemporalArtifactStore(environment.storage_root).resolve_storage_key(
            artifact.storage_key
        ).write_bytes(json.dumps(inventory).encode())
    else:
        seal = service.record_verified_dependencies

        async def deleted_after_read(**kwargs):
            await service.soft_delete(artifact_id=target, principal="operator")
            return await seal(**kwargs)

        monkeypatch.setattr(service, "record_verified_dependencies", deleted_after_read)
    with pytest.raises(ApplicationError):
        await invoke(service, request)
    execution = {"namespace": "default", "workflow_id": "assessment", "run_id": "run"}
    access = access_for_test("workflow:assessment")
    (scan,) = await service.list_for_execution(
        **execution, link_type="security.scan", access=access
    )
    assert await service._repository.get_verified_dependencies(scan.artifact_id) is None
    assert not await service.list_for_execution(
        **execution, link_type="security.comparison", access=access
    )


async def test_optional_review_evidence_stays_distinct_and_unavailable(environment):
    from tests.unit.security.test_security_review import review_payload

    service = environment.service
    evidence = [await put(service, {"observation": index}) for index in range(2)]
    review = review_payload()
    review["interpretations"][0]["evidenceRefs"] = evidence
    request = {
        "scope": review["scope"],
        "tools": [],
        "reviewRef": await put(service, review),
        "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
        "credentialRefs": [],
        "networkRefs": [],
    }
    missing = await invoke(
        service,
        {**request, "reviewRef": "art_missing_review"},
        run_id="missing-review",
    )
    assert missing["reviewStatus"] == "unavailable"
    assert missing["publicationOutcome"] == "published"
    assert await required(service, report_ref(missing)) == {
        missing["comparisonRef"],
        missing["scanRef"],
    }
    assert "art_missing_review" not in {
        entry.artifact_id
        for entry in await service._repository.get_verified_dependencies(
            report_ref(missing)
        )
    }

    result = await invoke(service, request)
    assert result["reviewStatus"] == "completed"
    report = report_ref(result)
    sealed = {
        entry.artifact_id: entry.required
        for entry in await service._repository.get_verified_dependencies(report)
    }
    assert sealed == {
        result["comparisonRef"]: True,
        result["scanRef"]: True,
        request["reviewRef"]: False,
        **{ref: False for ref in evidence},
    }
    await service.pin(artifact_id=report, principal="operator", reason="audit")
    await age_everything(environment)
    # Interpretation is optional: it never holds retention or blocks native facts.
    for ref in evidence:
        await service.soft_delete(artifact_id=ref, principal="operator")
    with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
        await service.soft_delete(
            artifact_id=result["comparisonRef"], principal="operator"
        )
    closure = await service.verified_dependencies(
        root_artifact_id=report, access=access_for_test("workflow:assessment")
    )
    # Aged optional interpretation is reported unavailable, never retained.
    assert set(closure.unavailable_optional) == {*evidence, request["reviewRef"]}


async def test_lost_seal_acknowledgement_reuses_scan_and_original_outcome(
    environment, monkeypatch
):
    from temporalio.exceptions import ApplicationError

    service = environment.service
    request, _, _ = await inputs(service)
    seal = service.record_verified_dependencies
    sealed = []

    async def lost_ack(**kwargs):
        result = await seal(**kwargs)
        sealed.append(kwargs["root_artifact_id"])
        if len(sealed) == 2:
            raise ConnectionError("lost acknowledgement after durable seal")
        return result

    monkeypatch.setattr(service, "record_verified_dependencies", lost_ack)
    with pytest.raises(ApplicationError, match="lost acknowledgement"):
        await invoke(service, request)
    scan_ref, comparison_ref = sealed
    original = (
        await service._repository.get_artifact(comparison_ref)
    ).dependency_closure_sha256
    assert original is not None
    monkeypatch.setattr(service, "record_verified_dependencies", seal)

    def repeat_forbidden(*_args):
        raise AssertionError("completed scanner work was repeated")

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
        assert [retried["scanRef"], retried["comparisonRef"]] == sealed
        assert retried["scanOutcome"] == "completed"
        assert retried["originalOutcomes"] == ["complete"]
        assert retried["publicationOutcome"] == "published"
        root = await restarted._repository.get_artifact(comparison_ref)
        assert root.dependency_closure_sha256 == original
        assert scan_ref in await required(restarted, report_ref(retried))


async def test_saved_schedule_input_root_consumes_the_comparison_seal(environment):
    """A schedule retaining a comparison keeps its evidence through that seal."""
    from api_service.db.models import (
        RecurringWorkflowDefinition,
        RecurringWorkflowScheduleType,
        RecurringWorkflowScopeType,
    )
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.workflows.temporal.artifacts import ArtifactAccessContext

    service = environment.service
    request, _, _ = await inputs(service)
    result = await invoke(service, request)
    await record_finished(environment, "assessment")
    comparison = result["comparisonRef"]
    target = {"inputArtifactRef": comparison}
    definition_id = uuid4()
    root = await RecurringWorkflowsService(
        environment.session, artifact_service=service
    )._retain_target_artifacts(
        target,
        definition_id=definition_id,
        access=ArtifactAccessContext.for_operator(
            OperatorAdmission(via="recurring-assessment-test")
        ),
    )
    environment.session.add(
        RecurringWorkflowDefinition(
            id=definition_id,
            name="Retained security baseline",
            enabled=False,
            schedule_type=RecurringWorkflowScheduleType.CRON,
            cron="0 6 * * *",
            timezone="UTC",
            owner_user_id=None,
            scope_type=RecurringWorkflowScopeType.PERSONAL,
            target=target,
            input_artifact_root_id=root,
            policy={},
        )
    )
    await environment.session.commit()
    evidence = await required(service, comparison) | {comparison}
    assert {request["inventoryRef"], request["feedRef"], result["scanRef"]} <= evidence
    assert evidence <= await required(service, root)
    await age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in evidence:
        assert await status(environment, ref) == "complete"


async def test_retained_unsealed_assessment_roots_get_bounded_disposition(
    environment, monkeypatch
):
    """Legacy roots seal from their own schema, or stay explicitly unsealed."""
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )

    service = environment.service
    request, inventory, _ = await inputs(service)
    first = await invoke(service, request)
    first_inventory = request["inventoryRef"]
    finding_id = (await read(service, first["comparisonRef"]))["findings"][0][
        "findingId"
    ]
    retest = await put(service, {"retest": "passed"})
    request.update(
        inventoryRef=await put(service, inventory),
        baselineRef=first["comparisonRef"],
        decisionsRef=await put(service, [decision(finding_id, [retest])]),
    )
    second = await invoke(service, request)
    lost_request, lost_inventory, _ = await inputs(service)
    lost_input = lost_request["inventoryRef"]
    lost = await invoke(service, lost_request, run_id="lost-run")
    lost_request.update(
        inventoryRef=await put(service, lost_inventory),
        baselineRef=lost["comparisonRef"],
    )
    lost_successor = await invoke(
        service, lost_request, run_id="lost-run", key="lost-successor"
    )
    await forget_seals(environment)
    environment.session.add(
        TemporalExecutionCanonicalRecord(
            namespace="default",
            workflow_id="assessment",
            run_id="run",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id="operator",
            state=MoonMindWorkflowState.COMPLETED,
            entry="run",
        )
    )
    await environment.session.commit()
    for result in (second, lost):
        await service.pin(
            artifact_id=result["comparisonRef"], principal="operator", reason="audit"
        )
    missing, _ = await service.read(
        artifact_id=lost_input, access=access_for_test("workflow:assessment")
    )
    LocalTemporalArtifactStore(environment.storage_root).delete(missing.storage_key)

    def replay_forbidden(*_args):
        raise AssertionError("legacy disposition replayed a completed scan")

    monkeypatch.setattr(
        "moonmind.workflows.skills.security_assessment.observe_inventory",
        replay_forbidden,
    )
    assert await service._backfill_retained_dependencies(
        now=datetime.now(UTC), limit=100
    )
    assert await required(service, first["comparisonRef"]) == {
        first["scanRef"],
        first_inventory,
        request["feedRef"],
    }
    # The older baseline sealed earlier in the same ordered page, so the later
    # comparison also carries the first inventory and the triage evidence it
    # cites. Its decisions input is not recorded, so it is not guessed.
    assert await required(service, second["comparisonRef"]) == {
        second["scanRef"],
        request["inventoryRef"],
        request["feedRef"],
        first["comparisonRef"],
        first_inventory,
        retest,
    }
    # A comparison whose baseline stays unsealed keeps no incomplete seal, while
    # its own scan still seals from its inputs.
    assert await required(service, lost_successor["scanRef"]) == {
        lost_request["inventoryRef"],
        lost_request["feedRef"],
    }
    for ref in (
        lost["scanRef"],
        lost["comparisonRef"],
        lost_successor["comparisonRef"],
        report_ref(second),
    ):
        assert await service._repository.get_verified_dependencies(ref) is None


async def attach(service, ref, workflow_id):
    """Delegate an exact input from its producer, as trusted save admission does."""
    await service.delegate_read_access(
        artifact_ids=[ref], workflow_id=workflow_id,
        access=access_for_test("workflow:assessment"),
    )
    await service.link_artifact(
        artifact_id=ref,
        principal="operator",
        execution_ref={
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": "run",
            "link_type": "input.security",
        },
    )


async def record_finished(environment, *workflow_ids):
    """Canonical records of finished producers, which backfill acts for."""
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )

    for workflow_id in workflow_ids:
        environment.session.add(
            TemporalExecutionCanonicalRecord(
                namespace="default",
                workflow_id=workflow_id,
                run_id="run",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_id=access_for_test("operator").principal,
                state=MoonMindWorkflowState.COMPLETED,
                entry="run",
            )
        )
    await environment.session.commit()


@pytest.mark.parametrize(
    ("change", "baseline"),
    [
        ("inventory", "sealed"),
        ("feed", "sealed"),
        ("feed", "legacy"),
        ("inventory", "backfilled"),
    ],
)
async def test_later_occurrence_in_another_workflow_seals_carried_history(
    environment, change, baseline
):
    """Each schedule occurrence is a new workflow admitting only its attachments."""
    service = environment.service
    request, inventory, feed = await inputs(service)
    first = await invoke(service, request)
    earlier = request[f"{change}Ref"]
    if baseline != "sealed":
        await forget_seals(environment)
    if baseline == "backfilled":
        await record_finished(environment, "assessment")
        await service._backfill_retained_dependencies(now=datetime.now(UTC), limit=100)
        assert first["scanRef"] in await required(service, first["comparisonRef"])
    replacement = inventory if change == "inventory" else {**feed, "revision": "feed-2"}
    request.update(
        {
            f"{change}Ref": await put(
                service, replacement, workflow_id="next-occurrence"
            ),
            "baselineRef": first["comparisonRef"],
        }
    )
    for key in ("inventoryRef", "feedRef", "baselineRef"):
        if request[key] != request[f"{change}Ref"]:
            await attach(service, request[key], "next-occurrence")
    successor = await invoke(service, request, workflow_id="next-occurrence")
    assert successor["baselineStatus"] == "available"
    assert successor["counts"]["still_observed"] == 1
    pinned = successor["comparisonRef"]
    comparison = await read(service, pinned, principal="workflow:next-occurrence")
    assert earlier in comparison["findings"][0]["finding"]["evidenceRefs"]
    assert {earlier, first["comparisonRef"]} <= await required(service, pinned)
    # Sealing the carried history grants the later occurrence no read of it.
    with pytest.raises(TemporalArtifactAuthorizationError):
        await read(service, earlier, principal="workflow:next-occurrence")
    await service.pin(artifact_id=pinned, principal="operator", reason="audit")
    await age_everything(environment)
    with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
        await service.soft_delete(artifact_id=earlier, principal="operator")
    await service.sweep_lifecycle(principal="service:sweeper")
    assert await status(environment, earlier) == "complete"
    assert await read(service, earlier)


async def test_backfill_seals_a_recurring_chain_across_workflows(environment):
    """A legacy successor seals history its baseline's producer verified."""
    service = environment.service
    request, inventory, _ = await inputs(service)
    first = await invoke(service, request)
    earlier = request["inventoryRef"]
    request.update(
        inventoryRef=await put(service, inventory, workflow_id="next-occurrence"),
        baselineRef=first["comparisonRef"],
    )
    for ref in (request["feedRef"], request["baselineRef"]):
        await attach(service, ref, "next-occurrence")
    successor = await invoke(service, request, workflow_id="next-occurrence")
    await forget_seals(environment)
    await record_finished(environment, "assessment", "next-occurrence")
    await service._backfill_retained_dependencies(now=datetime.now(UTC), limit=100)
    assert {
        earlier,
        successor["scanRef"],
        first["comparisonRef"],
    } <= await required(service, successor["comparisonRef"])


async def test_decision_may_cite_non_artifact_evidence(environment):
    """Only artifact evidence is read and sealed; other references stay text."""
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    finding_id = (await read(service, first["comparisonRef"]))["findings"][0][
        "findingId"
    ]
    retest = await put(service, {"retest": "passed"})
    ticket = "https://tracker.invalid/SEC-1"
    request.update(
        decisionsRef=await put(service, [decision(finding_id, [ticket, retest])]),
        baselineRef=first["comparisonRef"],
    )
    triaged = await invoke(service, request, key="triaged")
    # The scanner still reports the finding, so the passing retest needs review.
    assert triaged["counts"]["review_required"] == 1
    comparison = await read(service, triaged["comparisonRef"])
    assert comparison["decisions"][0]["decision"]["evidenceRefs"] == [ticket, retest]
    sealed = await required(service, triaged["comparisonRef"])
    assert {request["decisionsRef"], retest} <= sealed
    assert ticket not in sealed
    # Lifecycle backfill applies the same contract to a legacy comparison.
    await forget_seals(environment)
    await record_finished(environment, "assessment")
    await service._backfill_retained_dependencies(now=datetime.now(UTC), limit=100)
    assert retest in await required(service, triaged["comparisonRef"])


async def test_duplicate_feed_in_later_occurrence_reuses_admitted_baseline(
    environment, monkeypatch
):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    request["baselineRef"] = first["comparisonRef"]
    for ref in (request["inventoryRef"], request["feedRef"], first["comparisonRef"]):
        await service.delegate_read_access(
            artifact_ids=[ref], workflow_id="next-occurrence",
            access=access_for_test("workflow:assessment"),
        )
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
    from moonmind.workflows.temporal.artifacts import TemporalArtifactBytesMissingError

    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    baseline, _ = await service.read(
        artifact_id=first["comparisonRef"],
        access=access_for_test("workflow:assessment"),
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
    assert {
        "boundary": "baseline",
        "error": "TemporalArtifactBytesMissingError",
    } in result["diagnostics"]


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


async def test_github_upstream_closure_survives_assessment_aggregation(
    environment, monkeypatch
):
    from moonmind.workflows.adapters.github_service import GitHubService
    from tests.unit.security.test_github_alert_import import (
        FakeGitHub,
        assessment_request,
        code_alert,
        collect,
    )

    alerts = [code_alert(1), code_alert(2)]

    async def read_security_alerts(_self, request, *, admitted_workflow_id):
        return await collect(FakeGitHub({"code-scanning/alerts": [alerts]}), request)

    monkeypatch.setattr(GitHubService, "read_security_alerts", read_security_alerts)
    request = assessment_request(
        githubAlerts={
            "repository": "acme/repo",
            "categories": ["code-scanning"],
            "state": "all",
        }
    )
    baseline = await invoke(environment.service, request, key="first")
    alerts[:] = [code_alert(1, state="fixed"), code_alert(2, state="dismissed")]
    result = await invoke(
        environment.service,
        {**request, "baselineRef": baseline["comparisonRef"]},
        run_id="next-occurrence",
        key="second",
    )
    comparison = await read(environment.service, result["comparisonRef"])
    states = {
        row["finding"]["nativeId"]: (row["state"], row["verification"])
        for row in comparison["findings"]
    }
    assert states == {
        "1": ("remediated_verified", "upstream_fixed"),
        "2": ("closed_upstream", None),
    }


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
        link_type="security.scan",
        access=access_for_test("workflow:assessment"),
    )


async def test_occurrence_revalidates_current_authority_before_reusing_scan(
    environment,
):
    service = environment.service
    request, _, _ = await inputs(service)
    first = await invoke(service, request)
    # A live sealed comparison retains its input; after its retention passes,
    # ordinary cleanup may remove the input and reuse must then revalidate.
    with pytest.raises(TemporalArtifactStateError, match="DELETE_BLOCKED"):
        await service.soft_delete(
            artifact_id=request["inventoryRef"], principal="operator"
        )
    await age_everything(environment)
    await service.soft_delete(artifact_id=request["inventoryRef"], principal="operator")
    with pytest.raises(Exception):
        await invoke(service, request)
    # Completed records remain intact, but expired unretained bytes do not
    # become readable merely because a retry failed.
    assert await status(environment, first["comparisonRef"]) == "complete"
    with pytest.raises(TemporalArtifactStateError, match="expired"):
        await read(service, first["comparisonRef"])


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
    user = OperatorAdmission(via="recurring-assessment-test")
    service = environment.service
    request, _, feed = await inputs(service)
    # The schedule's baseline is a sealed comparison from an earlier feed.
    baseline = await invoke(
        service,
        {**request, "feedRef": await put(service, {**feed, "rules": []})},
        key="baseline",
    )
    request["baselineRef"] = baseline["comparisonRef"]
    await record_finished(environment, "assessment")
    async def artifact_client(_self):
        return client
    monkeypatch.setattr(TemporalClientAdapter, "get_client", artifact_client)
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
                    {"artifactId": request[key]}
                    for key in ("inventoryRef", "feedRef", "baselineRef")
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
    if authored_runtime is None:
        # The default runtime also exercises the actual step-only attachment
        # producer, not merely top-level workflow attachments.
        workflow_target = target["initialParameters"]["workflow"]
        workflow_target["steps"][0]["inputAttachments"] = workflow_target.pop("inputAttachments")
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
                activity_environment = ActivityEnvironment()
                activity_environment.info = replace(
                    activity_environment.info, namespace="default",
                    workflow_id=action.workflow_id,
                    workflow_run_id=action.first_execution_run_id,
                )
                return await activity_environment.run(
                    TemporalArtifactActivities(service).artifact_link, **payload
                )

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
            # The occurrence admits the attached baseline through its seal.
            assert result["baselineStatus"] == "available"
            assert {request["baselineRef"], request["inventoryRef"]} <= await required(
                service, result["comparisonRef"]
            )
            assert (await read(service, result["comparisonRef"], principal=f"workflow:{action.workflow_id}"))[
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
