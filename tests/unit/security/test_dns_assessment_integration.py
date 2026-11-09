"""DNS evidence crosses the real collector, report, comparison and export owners."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from moonmind.security.assessments import AssessmentScope, compare_observations
from moonmind.security.dns_assessments import (
    DNSCheckSelection,
    summarize_dns_assessment,
)
from moonmind.security.workload_profiles import resolve_profile_job
from moonmind.workflows.temporal.container_job_backend import (
    DockerContainerJobBackend,
    _withholds_console,
)
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_assessment_evidence_exports import unpack
from tests.unit.security.test_dns_assessment_admission import settings, spec
from tests.unit.security.test_dns_assessments import (
    NOW,
    assessment,
    execution,
    observation,
    parsed,
    plan,
    question,
)


def selected():
    q = question(record_type="TXT")
    return plan(
        assessment(
            questions=[q], checks=(DNSCheckSelection(check="spf", subject=q.name),)
        )
    )


def native(p=None, policy='"unrelated verification"'):
    p = p or selected()
    q = p.assessment.questions[0]
    return execution(p, observation(q, parsed(q, [(q.name, 120, "IN", "TXT", policy)])))


def collector(tmp_path, body, *, state="succeeded", p=None):
    p = p or selected()
    job = spec()
    job = job.model_copy(
        update={
            "workload_profile": job.workload_profile.model_copy(
                update={"dns_assessment": p.assessment}
            ),
            "timeout_seconds": 120,
        }
    )
    config = settings()
    # This fixture uses an explicit deployment resolver and its fixed rendered command.
    profile = config.workload_profiles[0].model_copy(
        update={"dns_resolver": p.resolver, "max_timeout_seconds": 120}
    )
    job = resolve_profile_job(job, (profile,))
    out = tmp_path / "dns-assessment"
    out.mkdir()
    if body is not None:
        (out / "execution.json").write_text(json.dumps(body))
    (out / "summary.json").write_text('{"status":"complete","forged":true}')
    published = {}

    async def publish(request, name, data):
        ref = "art_" + name.removesuffix(".json")
        published[ref] = json.loads(data)
        return ref

    backend = DockerContainerJobBackend(
        workspace_root=tmp_path, evidence_publisher=publish
    )
    backend._attachment_storage = lambda request: None
    backend._output_root = lambda request: tmp_path
    request = SimpleNamespace(
        request=SimpleNamespace(spec=job), job_id="dns", terminal_state=state
    )
    return backend, request, published


@pytest.mark.asyncio
async def test_collector_recomputes_summary_and_retains_native(tmp_path):
    backend, request, published = collector(
        tmp_path, native().model_dump(mode="json", by_alias=True)
    )
    assert _withholds_console(request.request.spec)
    manifest = await backend._collect_declared_outputs(request)
    assert [item.collection_status for item in manifest] == ["collected", "collected"]
    summary = published[manifest[1].artifact_ref]
    assert summary["evidenceRef"] == manifest[0].artifact_ref
    assert summary["checks"][0]["status"] == "missing"
    assert "forged" not in json.dumps(published)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "resolver", "plan", "secret"])
async def test_collector_rejects_invalid_native_without_trusting_summary(
    tmp_path, failure
):
    body = native().model_dump(mode="json", by_alias=True)
    if failure == "missing":
        body = None
    elif failure == "resolver":
        body["resolver"]["endpoint"] = "https://other.example/dns-query"
    elif failure == "plan":
        body["planKey"] = "0" * 64
    else:
        body["secret"] = "sk-secret-marker"
    backend, request, published = collector(tmp_path, body)
    manifest = await backend._collect_declared_outputs(request)
    assert not published
    assert manifest[0].collection_status in {"missing", "rejected"}


def observe(summary):
    from moonmind.workflows.skills.security_assessment import _dns_observation

    request = SimpleNamespace(scope=AssessmentScope(assets=["dns://example.com"]))
    return _dns_observation(request, summary, "art_summary")


def test_report_recomputes_native_and_comparison_obeys_ttl_and_missing_queries():
    from moonmind.workflows.skills.security_assessment import _verify_dns_report

    p = selected()
    before = summarize_dns_assessment(p, native(p), "art_native")
    _verify_dns_report(before, native(p).model_dump_json(by_alias=True).encode())
    with pytest.raises(ValueError):
        _verify_dns_report(
            before.model_copy(update={"checks": ()}),
            native(p).model_dump_json(by_alias=True).encode(),
        )
    baseline = compare_observations(
        observe(before), None, now=NOW + timedelta(seconds=30)
    )
    after = summarize_dns_assessment(p, native(p, '"v=spf1 -all"'), "art_after")
    result = compare_observations(
        observe(after), baseline, now=NOW + timedelta(seconds=30)
    )
    assert result["findings"][0]["state"] == "absent_comparable"
    expired = compare_observations(
        observe(after), baseline, now=NOW + timedelta(seconds=150)
    )
    assert expired["findings"][0]["state"] != "absent_comparable"
    missing = summarize_dns_assessment(p, execution(p), "art_missing")
    result = compare_observations(
        observe(missing), baseline, now=NOW + timedelta(seconds=30)
    )
    assert result["findings"][0]["state"] != "absent_comparable"


def test_export_retains_typed_native_and_summary_provenance():
    from moonmind.security.evidence_exports import prepare_evidence_member

    p = selected()
    for value in [native(p), summarize_dns_assessment(p, native(p), "art_native")]:
        body, _, schema = prepare_evidence_member(
            value.model_dump_json(by_alias=True).encode(),
            content_type="application/json",
            hint="native:dns",
        )
        assert json.loads(body)["resolver"]["endpoint"] == p.resolver.endpoint
        assert schema == value.schema_version


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["canceled", "failed", "timed_out"])
async def test_failed_job_retains_answers_without_complete_coverage(tmp_path, state):
    backend, request, published = collector(
        tmp_path, native().model_dump(mode="json", by_alias=True), state=state
    )
    manifest = await backend._collect_declared_outputs(request)
    summary = published[manifest[1].artifact_ref]
    assert summary["status"] == "partial"
    assert summary["execution"]["observations"]


environment = recurring.environment


@pytest.mark.asyncio
async def test_actual_collector_to_assessment_report_and_export(tmp_path, environment):
    service = environment.service
    p = selected()
    value = native(p)
    offset = datetime.now(UTC) - timedelta(seconds=25) - NOW

    def shift(value):
        return value.model_copy(
            update={
                name: getattr(value, name) + offset
                for name in ("started_at", "finished_at", "checkpoint_at")
                if getattr(value, name, None) is not None
            }
        )

    value = shift(value).model_copy(
        update={"observations": tuple(shift(row) for row in value.observations)}
    )
    backend, request, _ = collector(
        tmp_path, value.model_dump(mode="json", by_alias=True)
    )

    async def retain(request, name, body):
        return await recurring.put(service, body)

    backend._publish = retain
    manifest = await backend._collect_declared_outputs(request)
    native_ref, summary_ref = (item.artifact_ref for item in manifest)
    result = await recurring.invoke(
        service,
        {
            "scope": {"assets": ["dns://example.com"]},
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
            "evidenceExport": {"artifactRefs": [native_ref, summary_ref]},
        },
    )
    assert result["publicationOutcome"] == "published"
    scan = await recurring.read(service, result["scanRef"])
    dns = scan["scannerSummaries"][0]
    assert dns["scanner"] == "dns"
    assert dns["execution"]["resolver"]["endpoint"] == selected().resolver.endpoint
    assert dns["execution"]["observations"][0]["response"]["records"][0]["ttl"] == 120
    assert dns["coveredQuestions"] == [{"name": "example.com", "recordType": "TXT"}]
    assert dns["nativeRefs"] == {"execution": native_ref}
    _, report = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert b"spf_policy_missing" in report and b"resolver.example" in report
    assert b"Coverage expires" in report
    assert result["evidenceExport"]["status"] == "exported", result["evidenceExport"]
    archive, export = await unpack(service, result)
    exported = [
        json.loads(archive.read(item["memberName"])) for item in export["entries"]
    ]
    assert {item["schemaVersion"] for item in exported} == {
        "dns-execution/v1",
        "dns-assessment/v1",
    }
    assert all(
        item["resolver"]["endpoint"] == selected().resolver.endpoint
        for item in exported
    )
    # Repeat the real report/comparison pipeline with fresh comparable policy
    # evidence, then missing evidence. Only the observed policy change closes
    # the absence comparison; an empty execution cannot become remediation.
    baseline_ref = result["comparisonRef"]
    for index, complete in enumerate((True, False)):
        fresh = native(p, '"v=spf1 -all"') if complete else execution(p)
        fresh = shift(fresh).model_copy(
            update={"observations": tuple(shift(row) for row in fresh.observations)}
        )
        output = tmp_path / f"repeat-{index}"
        output.mkdir()
        backend, request, _ = collector(
            output, fresh.model_dump(mode="json", by_alias=True)
        )
        backend._publish = retain
        collected = await backend._collect_declared_outputs(request)
        repeated = await recurring.invoke(
            service,
            {
                "scope": {"assets": ["dns://example.com"]},
                "tools": ["retained-report"],
                "reportRefs": [collected[1].artifact_ref],
                "baselineRef": baseline_ref,
                "credentialRefs": [],
                "networkRefs": [],
                "budget": {
                    "baseSeconds": 60,
                    "maxSeconds": 120,
                    "progressStallSeconds": 60,
                },
            },
            key=f"repeat-{index}",
        )
        compared = await recurring.read(service, repeated["comparisonRef"])
        assert compared["findings"][0]["state"] == (
            "absent_comparable" if complete else "not_reassessed"
        )


def test_resolver_or_question_coverage_change_is_not_comparable():
    from moonmind.security.dns_assessments import (
        DNSResolverConfig,
        build_dns_query_plan,
    )

    p = selected()
    baseline = compare_observations(
        observe(summarize_dns_assessment(p, native(p), "art_before")),
        None,
        now=NOW + timedelta(seconds=30),
    )
    changed = build_dns_query_plan(
        p.assessment, DNSResolverConfig(endpoint="https://another.example/dns-query")
    )
    current = summarize_dns_assessment(
        changed, native(changed, '"v=spf1 -all"'), "art_after"
    )
    comparison = compare_observations(
        observe(current), baseline, now=NOW + timedelta(seconds=30)
    )
    assert comparison["findings"][0]["state"] == "not_reassessed"


@pytest.mark.asyncio
async def test_output_publication_keeps_native_if_summary_storage_fails(tmp_path):
    backend, request, published = collector(
        tmp_path, native().model_dump(mode="json", by_alias=True)
    )
    original = backend._publish

    async def publish(request, name, body):
        if "summary" in name:
            raise OSError("retention unavailable")
        return await original(request, name, body)

    backend._publish = publish
    with pytest.raises(OSError, match="retention unavailable"):
        await backend._collect_declared_outputs(request)
    assert len(published) == 1
    assert next(iter(published.values()))["schemaVersion"] == "dns-execution/v1"


@pytest.mark.asyncio
async def test_unfinished_cancellation_retains_completed_answers(tmp_path):
    value = native().model_copy(
        update={"finished_at": None, "stop_reason": "in_progress"}
    )
    backend, request, published = collector(
        tmp_path, value.model_dump(mode="json", by_alias=True), state="canceled"
    )
    manifest = await backend._collect_declared_outputs(request)
    summary = published[manifest[1].artifact_ref]
    assert summary["execution"]["stopReason"] == "canceled"
    assert summary["checks"][0]["status"] == "missing"
    assert summary["status"] == "partial"


@pytest.mark.asyncio
async def test_assessment_tool_rejects_tampered_summary(environment):
    service = environment.service
    p = selected()
    native_ref = await recurring.put(
        service, native(p).model_dump(mode="json", by_alias=True)
    )
    summary = summarize_dns_assessment(p, native(p), native_ref).model_dump(
        mode="json", by_alias=True
    )
    summary["checks"] = []
    summary_ref = await recurring.put(service, summary)
    result = await recurring.invoke(
        service,
        {
            "scope": {"assets": ["dns://example.com"]},
            "tools": ["retained-report"],
            "reportRefs": [summary_ref],
            "credentialRefs": [],
            "networkRefs": [],
            "budget": {
                "baseSeconds": 60,
                "maxSeconds": 120,
                "progressStallSeconds": 60,
            },
        },
    )
    scan = await recurring.read(service, result["scanRef"])
    assert not scan["scannerSummaries"]
    assert scan["diagnostics"]
    assert all(row["status"] != "complete" for row in scan["observation"]["coverage"])


def test_future_completed_queries_cannot_close_present_finding():
    p = selected()
    before = summarize_dns_assessment(p, native(p), "art_before")
    baseline = compare_observations(
        observe(before), None, now=NOW + timedelta(seconds=30)
    )
    later = native(p, '"v=spf1 -all"')
    rows = tuple(
        row.model_copy(
            update={
                "started_at": row.started_at + timedelta(seconds=40),
                "finished_at": row.finished_at + timedelta(seconds=40),
            }
        )
        for row in later.observations
    )
    later = later.model_copy(
        update={
            "observations": rows,
            "checkpoint_at": later.checkpoint_at + timedelta(seconds=40),
            "finished_at": later.finished_at + timedelta(seconds=40),
        }
    )
    current = summarize_dns_assessment(p, later, "art_after")
    compared = compare_observations(
        observe(current), baseline, now=NOW + timedelta(seconds=30)
    )
    assert compared["findings"][0]["state"] == "not_reassessed"
