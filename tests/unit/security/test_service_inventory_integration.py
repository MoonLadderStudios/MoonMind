"""Inventory collection and retained reports preserve the existing evidence owners."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.security.assessments import AssessmentScope, compare_observations
from moonmind.security.service_inventory import (
    ServiceInventoryOptions,
    ServiceInventoryPinnedIdentity,
    build_service_inventory_plan,
    summarize_service_inventory,
)
from moonmind.security.workload_profiles import WorkloadProfile, resolve_profile_job
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
    _withholds_console,
)
from tests.unit.security.test_native_network import native
from tests.unit.security.test_service_inventory_profile import (
    inventory_profile,
    inventory_spec,
)

NOW = datetime(2026, 11, 8, tzinfo=UTC)


def plan(options=None):
    return build_service_inventory_plan(native(), options or ServiceInventoryOptions())


def execution(selected=None, reachability="open", *, finished=True):
    selected = selected or plan()
    return {
        "schemaVersion": "service-inventory-execution/v1",
        "planKey": selected.plan_key,
        "startedAt": NOW.isoformat(),
        "finishedAt": (NOW + timedelta(seconds=1)).isoformat() if finished else None,
        "stopReason": "complete" if finished else None,
        "records": [
            {
                "authority": endpoint.authority,
                "probe": endpoint.probe,
                "reachability": reachability,
                "probeStatus": (
                    "not_requested" if endpoint.probe == "connect" else "untested"
                ),
            }
            for endpoint in selected.endpoints
        ],
    }


def job():
    spec = inventory_spec()
    spec = spec.model_copy(
        update={
            "workload_profile": spec.workload_profile.model_copy(
                update={"service_inventory": ServiceInventoryOptions()}
            )
        }
    )
    return resolve_profile_job(
        spec, (WorkloadProfile.model_validate(inventory_profile()),)
    )


def collector(
    tmp_path, payload, *, state="succeeded", egress=None, publish_failure=None
):
    out = tmp_path / "service-inventory"
    out.mkdir()
    if payload is not None:
        (out / "execution.json").write_text(json.dumps(payload))
    (out / "summary.json").write_text('{"status":"complete","forged":true}')
    published = {}

    async def publish(request, name, data):
        if publish_failure and publish_failure in name:
            raise OSError("retention unavailable")
        ref = "art_" + name.removesuffix(".json")
        published[ref] = json.loads(data)
        return ref

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publish
    )
    backend._attachment_storage = lambda request: None
    backend._output_root = lambda request: tmp_path
    backend._runtime_egress_evidence = AsyncMock(return_value=egress)
    request = SimpleNamespace(
        request=SimpleNamespace(spec=job()), job_id="inventory", terminal_state=state
    )
    return backend, request, published


@pytest.mark.asyncio
async def test_inventory_dispatch_withholds_console_and_ignores_runner_summary(
    tmp_path,
):
    backend, request, published = collector(tmp_path, execution())
    assert _withholds_console(request.request.spec)
    manifest = await backend._collect_declared_outputs(request, [])
    summary = next(
        v
        for v in published.values()
        if v.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "partial"
    assert summary["endpoints"][0]["reachability"] == "open"
    assert "forged" not in json.dumps(published)
    assert [item.name for item in manifest] == [
        "service-inventory-execution",
        "service-inventory-summary",
    ]
    assert all(item.collection_status == "collected" for item in manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["failed", "canceled", "timed_out"])
async def test_inventory_keeps_completed_records_after_job_failure(tmp_path, state):
    backend, request, published = collector(
        tmp_path, execution(finished=False), state=state
    )
    await backend._collect_declared_outputs(request, [])
    summary = next(
        v
        for v in published.values()
        if v.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "partial"
    assert summary["endpoints"][0]["reachability"] == "open"
    assert summary["comparisonKey"] is None
    assert summary["jobState"] == state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["missing", "duplicate", "out_of_scope", "forged_status", "extra_field"]
)
async def test_inventory_rejects_invalid_native_evidence(tmp_path, invalid):
    payload = execution()
    if invalid == "missing":
        payload = None
    elif invalid == "duplicate":
        payload["records"] *= 2
    elif invalid == "out_of_scope":
        payload["records"][0]["authority"] = "other.example.com:587"
    elif invalid == "forged_status":
        payload["records"][0]["reachability"] = "secure"
    else:
        payload["pinnedIdentities"] = [{"address": "8.8.8.8"}]
    backend, request, published = collector(tmp_path, payload)
    manifest = await backend._collect_declared_outputs(request, [])
    summary = next(
        v
        for v in published.values()
        if v.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "failed"
    assert summary["endpoints"][0]["reachability"] == "untested"
    assert manifest[0].collection_status == (
        "missing" if invalid == "missing" else "rejected"
    )
    assert not any(
        v.get("schemaVersion") == "service-inventory-execution/v1"
        for v in published.values()
    )


def summarized(
    reachability="open", *, selected=None, pinned=True, state="succeeded", offset=0
):
    selected = selected or plan()
    payload = execution(selected, reachability)
    payload["startedAt"] = (NOW + timedelta(seconds=offset)).isoformat()
    payload["finishedAt"] = (NOW + timedelta(seconds=offset + 1)).isoformat()
    return summarize_service_inventory(
        selected,
        payload,
        "art_native",
        pinned_identities=(
            tuple(
                ServiceInventoryPinnedIdentity(
                    authority=endpoint.authority, address="8.8.8.8"
                )
                for endpoint in selected.endpoints
            )
            if pinned
            else ()
        ),
        attestation_ref="art_attestation" if pinned else None,
        job_state=state,
    )


def observe(summary):
    from moonmind.workflows.skills.security_assessment import (
        _service_inventory_observation,
    )

    request = SimpleNamespace(
        scope=AssessmentScope(assets=["tcp://smtp.example.com:587"])
    )
    return _service_inventory_observation(request, summary, "art_summary", NOW)


def test_inventory_comparison_open_to_refused_is_comparable_despite_new_timestamp():
    baseline = compare_observations(
        observe(summarized()), None, now=NOW + timedelta(seconds=10)
    )
    result = compare_observations(
        observe(summarized("refused", offset=2)),
        baseline,
        now=NOW + timedelta(seconds=10),
    )
    finding = result["findings"][0]
    assert finding["state"] == "absent_comparable"
    assert finding["finding"]["ruleId"] == "informational-open-tcp-service"
    assert finding["finding"]["asset"] == "tcp://smtp.example.com:587"
    assert (
        "not confirmed vulnerabilities"
        in result["observation"]["provenance"]["serviceInventoryInterpretation"]
    )


@pytest.mark.parametrize(
    "state", ["timed_out", "denied", "unreachable", "untested", "transport_error"]
)
def test_uncertain_inventory_never_implies_absence(state):
    baseline = compare_observations(
        observe(summarized()), None, now=NOW + timedelta(seconds=10)
    )
    result = compare_observations(
        observe(summarized(state)), baseline, now=NOW + timedelta(seconds=10)
    )
    assert result["findings"][0]["state"] == "not_reassessed"


@pytest.mark.parametrize("change", ["missing_pins", "changed_probe", "failed_job"])
def test_incomparable_inventory_keeps_prior_open_observations(change):
    baseline = compare_observations(
        observe(summarized()), None, now=NOW + timedelta(seconds=10)
    )
    summary = summarized(
        "refused",
        pinned=change != "missing_pins",
        state="failed" if change == "failed_job" else "succeeded",
        selected=(
            plan(ServiceInventoryOptions(banner_ports=[587]))
            if change == "changed_probe"
            else None
        ),
    )
    result = compare_observations(
        observe(summary), baseline, now=NOW + timedelta(seconds=10)
    )
    assert result["findings"][0]["state"] == "not_reassessed"


@pytest.mark.asyncio
async def test_collector_retains_trusted_attestation_and_report_recomputes(tmp_path):
    from moonmind.security.service_inventory import ServiceInventorySummary
    from moonmind.workflows.skills.security_assessment import (
        _verify_service_inventory_report,
    )
    from tests.unit.security.test_service_inventory import resolution

    backend, request, published = collector(
        tmp_path, execution(), egress=resolution(plan())
    )
    await backend._collect_declared_outputs(request, [])
    summary = ServiceInventorySummary.model_validate(
        next(
            v
            for v in published.values()
            if v.get("schemaVersion") == "service-inventory/v1"
        )
    )
    assert summary.status == "complete"
    native_body = json.dumps(published[summary.evidence_ref]).encode()
    attestation_body = json.dumps(published[summary.attestation_ref]).encode()
    assert (
        _verify_service_inventory_report(summary, native_body, attestation_body)
        == summary
    )
    assert next(iter(published)) == summary.evidence_ref
    changed = summary.model_dump(by_alias=True)
    changed["endpoints"][0]["reachability"] = "refused"
    with pytest.raises(ValueError, match="retained evidence"):
        _verify_service_inventory_report(
            ServiceInventorySummary.model_validate(changed),
            native_body,
            attestation_body,
        )
    with pytest.raises(ValueError):
        _verify_service_inventory_report(summary, native_body, None)
    forged = json.loads(attestation_body)
    forged["containerJobState"] = "failed"
    with pytest.raises(ValueError):
        _verify_service_inventory_report(
            summary, native_body, json.dumps(forged).encode()
        )


@pytest.mark.asyncio
async def test_attestation_failure_preserves_native_progress(tmp_path):
    backend, request, published = collector(tmp_path, execution())
    backend._runtime_egress_evidence.side_effect = RuntimeError("gateway unavailable")
    await backend._collect_declared_outputs(request, [])
    summary = next(
        v
        for v in published.values()
        if v.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "partial"
    assert summary["attestationRef"] is None
    assert summary["endpoints"][0]["reachability"] == "open"


@pytest.mark.asyncio
async def test_counter_contradiction_reduces_coverage_without_rewriting_native(
    tmp_path,
):
    from tests.unit.security.test_service_inventory import resolution

    evidence = resolution(plan())
    evidence["nativeNetwork"]["dialOutcomes"] = {"refused": 1}
    backend, request, published = collector(tmp_path, execution(), egress=evidence)
    await backend._collect_declared_outputs(request, [])
    summary = next(
        v
        for v in published.values()
        if v.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "partial"
    assert summary["comparisonKey"] is None
    assert summary["endpoints"][0]["reachability"] == "open"


@pytest.mark.asyncio
async def test_inventory_launch_carries_original_deadline_on_each_attempt(
    tmp_path, monkeypatch
):
    from contextlib import asynccontextmanager
    from dataclasses import replace

    from tests.unit.workflows.temporal.test_container_job_backend import _request

    selected = job()
    request = _request(tmp_path).model_copy(
        update={
            "request": _request(tmp_path).request.model_copy(update={"spec": selected})
        }
    )
    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=AsyncMock(return_value="art_lease")
    )
    backend._settings = replace(
        backend._settings,
        workload_profiles=(WorkloadProfile.model_validate(inventory_profile()),),
    )
    original_expiry = 1800000000.25
    scoped = SimpleNamespace(
        network_ref="native-network",
        gateway_ref="native-gateway",
        policy=SimpleNamespace(digest="digest"),
        prepare=AsyncMock(
            return_value={
                "proxyAddress": "172.20.0.2",
                "clientAddress": "172.20.0.3",
                "gatewayIdentity": "gateway-id",
                "snapshotDigest": "digest",
                "expiresAt": original_expiry,
            }
        ),
        proxy_env=lambda: ["MOONMIND_EGRESS_PROXY=http://native-gateway:3128"],
    )

    @asynccontextmanager
    async def guard(*args, **kwargs):
        yield original_expiry

    monkeypatch.setattr(backend, "_assessment_guard", guard)
    monkeypatch.setattr(backend, "_scoped_lease", lambda request: scoped)
    monkeypatch.setattr(backend, "_reject_ownership_collision", AsyncMock())
    monkeypatch.setattr(backend, "_prepare_attachments", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        backend, "_publish_scoped_egress", AsyncMock(return_value="art_lease")
    )
    launch = AsyncMock(return_value=(0, b"", b""))
    monkeypatch.setattr(backend, "_assessment_launch_command", launch)
    for _ in range(2):
        await backend.create_container(request)
    for call in launch.call_args_list:
        argv = call.args[1]
        assert [
            item for item in argv if item.startswith("MOONMIND_EGRESS_DEADLINE=")
        ] == [f"MOONMIND_EGRESS_DEADLINE={original_expiry}"]
    assert all(
        call.kwargs["deadline"] == original_expiry
        for call in scoped.prepare.call_args_list
    )


def test_inventory_lease_uses_admitted_dial_timeout_only(tmp_path, monkeypatch):
    from moonmind.workflows.temporal import container_job_backend as module

    backend = DockerContainerJobBackend(workspace_root=tmp_path)
    spec = job()
    backend._settings = SimpleNamespace(authorize_egress=lambda request: object())
    seen = []
    monkeypatch.setattr(
        module, "ScopedEgressLease", lambda *args, **kwargs: seen.append(kwargs)
    )
    request = SimpleNamespace(
        request=SimpleNamespace(spec=spec),
        ownership_token="owner",
        job_id="job",
        container_ref="container",
    )
    backend._scoped_lease(request)
    assert seen[-1]["native_connect_timeout_seconds"] == 5
    spec = spec.model_copy(
        update={
            "workload_profile": spec.workload_profile.model_copy(
                update={"service_inventory": None}
            )
        }
    )
    request.request.spec = spec
    backend._scoped_lease(request)
    assert seen[-1]["native_connect_timeout_seconds"] is None


@pytest.mark.asyncio
async def test_failed_auxiliary_attestation_publication_keeps_primary_native_and_partial_summary(
    tmp_path,
):
    from tests.unit.security.test_service_inventory import resolution

    backend, request, published = collector(
        tmp_path, execution(), egress=resolution(plan()), publish_failure="attestation"
    )
    await backend._collect_declared_outputs(request, [])
    summary = next(
        v
        for v in published.values()
        if v.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "partial"
    assert summary["evidenceRef"] in published
    assert summary["attestationRef"] is None
    assert summary["endpoints"][0]["reachability"] == "open"
    from moonmind.security.service_inventory import ServiceInventorySummary
    from moonmind.workflows.skills.security_assessment import (
        _verify_service_inventory_report,
    )

    retained = ServiceInventorySummary.model_validate(summary)
    assert (
        _verify_service_inventory_report(
            retained, json.dumps(published[retained.evidence_ref]).encode(), None
        )
        == retained
    )


def test_banner_claim_changes_do_not_create_new_exposure_identity():
    from moonmind.security.service_inventory import ServiceInventorySummary

    selected = plan(ServiceInventoryOptions(banner_ports=[587]))
    pins = (
        ServiceInventoryPinnedIdentity(
            authority=selected.endpoints[0].authority, address="8.8.8.8"
        ),
    )
    summaries = []
    for banner in ("Service version 1", "Totally different product version 200"):
        payload = execution(selected)
        payload["records"][0].update(
            probeStatus="observed",
            bytesReceived=len(banner),
            observation={"text": banner},
        )
        summary = summarize_service_inventory(
            selected,
            payload,
            "art_native",
            pinned_identities=pins,
            attestation_ref="art_lease",
            job_state="succeeded",
        )
        summaries.append(ServiceInventorySummary.model_validate(summary))
    assert (
        summaries[0].endpoints[0].observation_key
        != summaries[1].endpoints[0].observation_key
    )
    assert (
        observe(summaries[0]).findings[0].finding_id
        == observe(summaries[1]).findings[0].finding_id
    )
    assert summaries[0].comparison_key == summaries[1].comparison_key


@pytest.mark.asyncio
async def test_native_output_byte_limit_and_symlink_boundary_are_not_bypassed(tmp_path):
    from moonmind.security.service_inventory import MAX_SERVICE_INVENTORY_OUTPUT_BYTES

    backend, request, published = collector(tmp_path, execution())
    source = tmp_path / "service-inventory" / "execution.json"
    source.write_bytes(b" " * (MAX_SERVICE_INVENTORY_OUTPUT_BYTES + 1))
    manifest = await backend._collect_declared_outputs(request, [])
    assert manifest[0].collection_status == "rejected"
    assert (
        next(
            v
            for v in published.values()
            if v.get("schemaVersion") == "service-inventory/v1"
        )["status"]
        == "failed"
    )
    source.unlink()
    source.symlink_to(tmp_path / "elsewhere.json")
    (tmp_path / "elsewhere.json").write_text(json.dumps(execution()))
    manifest = await backend._collect_declared_outputs(request, [])
    assert manifest[0].collection_status == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, True, 6])
async def test_typed_dial_timeout_must_be_attested_for_comparison(tmp_path, timeout):
    from tests.unit.security.test_service_inventory import resolution

    evidence = resolution(plan())
    if timeout is None:
        evidence.pop("nativeConnectTimeoutSeconds", None)
    else:
        evidence["nativeConnectTimeoutSeconds"] = timeout
    backend, request, published = collector(tmp_path, execution(), egress=evidence)
    await backend._collect_declared_outputs(request, [])
    summary = next(
        value
        for value in published.values()
        if value.get("schemaVersion") == "service-inventory/v1"
    )
    assert summary["status"] == "partial"
    assert summary["comparisonKey"] is None
    assert summary["endpoints"][0]["reachability"] == "open"


@pytest.mark.asyncio
async def test_mixed_width_gateway_ports_remain_complete_in_collector_and_report(
    tmp_path,
):
    from moonmind.security.service_inventory import ServiceInventorySummary
    from moonmind.workflows.skills.security_assessment import (
        _service_inventory_observation,
        _verify_service_inventory_report,
    )
    from tests.unit.security.test_service_inventory import (
        mixed_width_gateway_resolution,
        selection,
    )

    selected = build_service_inventory_plan(
        selection(ports=(80, 443)), ServiceInventoryOptions()
    )
    backend, request, published = collector(
        tmp_path, execution(selected), egress=mixed_width_gateway_resolution(selected)
    )
    spec = request.request.spec
    request.request.spec = spec.model_copy(
        update={
            "workload_profile": spec.workload_profile.model_copy(
                update={"native_network": selected.native_network}
            )
        }
    )
    await backend._collect_declared_outputs(request, [])
    summary = ServiceInventorySummary.model_validate(
        next(
            value
            for value in published.values()
            if value.get("schemaVersion") == "service-inventory/v1"
        )
    )
    assert summary.status == "complete"
    normalized = _verify_service_inventory_report(
        summary,
        json.dumps(published[summary.evidence_ref]).encode(),
        json.dumps(published[summary.attestation_ref]).encode(),
    )
    observed = _service_inventory_observation(
        SimpleNamespace(
            scope=AssessmentScope(
                assets=["tcp://service.example.com:80", "tcp://service.example.com:443"]
            )
        ),
        normalized,
        "art_summary",
        NOW,
    )
    assert observed.coverage[0].status == "complete"
    assert len(observed.findings) == 2
