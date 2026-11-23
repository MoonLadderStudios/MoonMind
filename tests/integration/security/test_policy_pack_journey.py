"""Policy document to accepted pack through the real artifact, settings and assessment owners."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from api_service.services.framework_catalog import framework_catalog
from api_service.services.policy_pack_drafts import (
    PolicyPackAcceptRequest,
    PolicyPackDraftRequest,
    PolicyPackError,
    accept_policy_pack,
    draft_policy_pack,
)
from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.security.assessment_projects import SETTINGS_KEY
from moonmind.security.policy_packs import PolicyPackDraft
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_control_assessment_journey import inputs
from tests.unit.security.test_policy_packs import POLICY, response

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
OPERATOR = access_for_test("operator")
RULE = ("fixture-advisories", "inventory-feed", "CVE-EXAMPLE")


async def put(service, data: bytes, content_type, *, name, workflow_id="assessment"):
    artifact, _ = await service.create(
        principal="operator",
        content_type=content_type,
        link={
            "namespace": "default",
            "workflow_id": workflow_id,
            "run_id": "run",
            "link_type": "input.security",
        },
        metadata_json={"filename": name},
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id, principal="operator", payload=data
    )
    return artifact.artifact_id, hashlib.sha256(data).hexdigest()


async def link(service, ref, workflow_id="assessment"):
    """Admit an operator-published pack to the assessment, as its input."""
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


def harness_response(document_sha, **changes):
    """A controlled Skill response that cites the attachment by its digest."""
    value = response().model_dump(mode="json", by_alias=True, exclude_none=True)
    for row in [*value["requirements"], *value["context"]]:
        row["locator"].pop("artifactRef")
        row["locator"]["documentSha256"] = document_sha
    suggestion = value["requirements"][0]["objectiveSuggestions"][0]
    suggestion.update(source=RULE[0], tool=RULE[1], ruleIds=[RULE[2]])
    value.update(changes)
    return value


async def rule_inventory(service):
    now = datetime.now(UTC)
    observation = {
        "schemaVersion": "security-observation/v1",
        "scope": {"assets": ["app"]},
        "observedAt": now.isoformat(),
        "expiresAt": (now + timedelta(days=1)).isoformat(),
        "coverage": [
            {
                "source": RULE[0],
                "tool": RULE[1],
                "assets": ["app"],
                "ruleIds": [RULE[2]],
                "configRef": "fixture-config",
                "status": "complete",
                "evidenceMethod": "deterministic_check",
            }
        ],
        "findings": [],
        "provenance": {"fixture": "synthetic"},
    }
    ref, _ = await put(
        service,
        json.dumps(observation).encode(),
        "application/json",
        name="observation.json",
    )
    return ref


async def save_environment(session):
    settings = SettingsCatalogService(session=session)
    current = await settings.effective_value_async(SETTINGS_KEY)
    document = {
        "projects": [
            {
                "id": "acme",
                "name": "Acme",
                "purpose": "Assess Acme against its own policy",
                "boundaries": "Synthetic fixture",
                "environments": [{"id": "production", "name": "Production"}],
            }
        ]
    }
    await settings.apply_overrides(
        changes={SETTINGS_KEY: document},
        expected_versions={SETTINGS_KEY: current.value_version},
    )


async def drafted(env, text=POLICY, *, previous=None, changes=None, name="policy.md"):
    service = env.service
    doc_ref, doc_sha = await put(service, text.encode(), "text/markdown", name=name)
    response_ref, response_sha = await put(
        service,
        json.dumps(
            changes(harness_response(doc_sha)) if changes else harness_response(doc_sha)
        ).encode(),
        "application/json",
        name="draft-response.json",
    )
    unsupported, unsupported_sha = await put(
        service, b"%PDF-1.7 synthetic", "application/pdf", name="appendix.pdf"
    )
    request = PolicyPackDraftRequest.model_validate(
        {
            "policy": {
                "name": "Acme Information Security Policy",
                "owner": "Security team",
                "version": "3.0",
            },
            "documents": [
                {"artifactRef": doc_ref, "sha256": doc_sha},
                {"artifactRef": unsupported, "sha256": unsupported_sha},
            ],
            "responseArtifactRef": response_ref,
            "responseSha256": response_sha,
            "ruleInventoryRefs": [await rule_inventory(service)],
            "mappingTarget": {
                "frameworkId": "iso-27001-readiness",
                "edition": "2022+Amd1:2024",
            },
            "previousPack": previous,
        }
    )
    result = await draft_policy_pack(
        request, session=env.session, access=OPERATOR, artifacts=service
    )
    return result, doc_ref, doc_sha


async def accept(env, result, **review):
    request = PolicyPackAcceptRequest.model_validate(
        {
            "draftArtifactRef": result["draftArtifactRef"],
            "draftSha256": result["draftSha256"],
            "review": {
                "reviewedBy": "CISO",
                "reviewedOn": datetime.now(UTC).date().isoformat(),
                "effectiveFrom": datetime.now(UTC).date().isoformat(),
                **review,
            },
            "projectId": "acme",
            "environmentId": "production",
        }
    )
    return await accept_policy_pack(
        request, session=env.session, access=OPERATOR, artifacts=env.service
    )


def selection(identity):
    day = datetime.now(UTC).date().isoformat()
    applicable = {
        "applicability": "applicable",
        "applicabilityReason": "The synthetic policy applies to the selected app.",
    }
    return {
        "artifactRef": identity["artifactRef"],
        "packId": identity["packId"],
        "edition": identity["edition"],
        "revision": identity["revision"],
        "sha256": identity["sha256"],
        "period": {"start": day, "end": day},
        "requirementChoices": {"ACME-4.1-1": applicable},
    }


async def assess(env, identity, monkeypatch=None):
    request = await inputs(env.service)
    request.pop("controlAssessment")
    request["frameworkPack"] = selection(identity)
    return await recurring.invoke(env.service, request)


async def test_policy_fixture_journey_from_draft_to_requirement_results(
    environment, monkeypatch
):
    env = environment
    await save_environment(env.session)
    result, doc_ref, doc_sha = await drafted(env)
    assert result["publicationStatus"] == "draft"
    assert result["distribution"] == "restricted"
    assert {d["status"] for d in result["documents"]} == {"read", "unsupported"}
    assert any(
        f["kind"] == "unsupported_document" and f["subject"] == "appendix.pdf"
        for f in result["flags"]
    )
    assert any(f["kind"] == "instruction_in_document" for f in result["flags"])
    draft = PolicyPackDraft.model_validate(
        await recurring.read(
            env.service, result["draftArtifactRef"], principal="operator"
        )
    )
    assert {s.artifact_ref for r in draft.pack.requirements for s in r.sources} == {
        doc_ref
    }
    assert [s.rule_ids for s in draft.objective_suggestions] == [[RULE[2]]]
    _, sheet = await env.service.read(
        artifact_id=result["reviewSheetRef"], access=OPERATOR
    )
    assert (
        b"ACME-4.1-1" in sheet
        and b"appendix.pdf" in sheet
        and b"never operative" in sheet
    )

    # The draft is never selectable: neither its bundle nor its pack is operative.
    draft_pack = json.dumps(draft.pack.model_dump(mode="json", by_alias=True)).encode()
    draft_ref, draft_sha = await put(
        env.service, draft_pack, "application/json", name="draft-pack.json"
    )
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError, match="draft, not operative"):
        await assess(
            env,
            {
                "artifactRef": draft_ref,
                "packId": draft.pack.id,
                "edition": "3.0",
                "revision": "1",
                "sha256": draft_sha,
            },
        )

    accepted = await accept(
        env, result, adoptObjectiveSuggestions=["S1"], acceptMappingProposals=["M1"]
    )
    assert (accepted["publicationStatus"], accepted["revision"]) == ("published", "1")
    monkeypatch.setattr(
        "api_service.services.framework_catalog.TemporalArtifactService",
        lambda _repo: env.service,
    )
    catalog = await framework_catalog(env.session, OPERATOR)
    (entry,) = [i for i in catalog["items"] if i["source"] == "declared"]
    assert entry["available"] and entry["artifactRef"] == accepted["artifactRef"]
    assert entry["revision"] == "1" and entry["distribution"] == "restricted"

    await link(env.service, accepted["artifactRef"])
    outputs = await assess(env, accepted)
    report = await recurring.read(env.service, outputs["controlAssessmentRef"])
    rows = {row["requirement"]["id"]: row for row in report["requirements"]}
    assert set(rows) == {"ACME-4.1-1", "ACME-4.1-2", "ACME-4.2-1"}
    assert rows["ACME-4.1-1"]["conclusion"] in {
        "inconclusive",
        "not_satisfied",
        "satisfied",
    }
    assert rows["ACME-4.1-1"]["requirement"]["objectivesComplete"] is False
    pack = report["frameworkPack"]
    assert pack["textOmitted"] and pack["sourceDocuments"] == [
        {"artifactRef": doc_ref, "sha256": doc_sha}
    ]
    text = json.dumps(report)
    assert (
        "Original summary" not in text
        and "Acme Information Security Policy" not in text
    )
    assert any("Restricted pack text omitted" in item for item in report["diagnostics"])
    assert doc_ref in await recurring.required(
        env.service, outputs["controlAssessmentRef"]
    )


@pytest.mark.parametrize("fault", ["missing", "unauthorized", "changed"])
async def test_artifact_sourced_pack_is_refused_when_its_source_is_unavailable(
    environment, monkeypatch, fault
):
    env = environment
    await save_environment(env.session)
    result, doc_ref, doc_sha = await drafted(env)
    accepted = await accept(env, result)
    _, raw = await env.service.read(
        artifact_id=accepted["artifactRef"], access=OPERATOR
    )
    if fault == "missing":
        replacement, sha = "art_missing_policy_source", doc_sha
    elif fault == "unauthorized":
        replacement, sha = await put(
            env.service,
            POLICY.encode(),
            "text/markdown",
            name="foreign.md",
            workflow_id="foreign",
        )
    else:
        replacement, sha = doc_ref, "0" * 64
    tampered = raw.decode().replace(doc_ref, replacement).replace(doc_sha, sha).encode()
    ref, tampered_sha = await put(
        env.service, tampered, "application/json", name="pack.json"
    )
    identity = dict(accepted, artifactRef=ref, sha256=tampered_sha)
    request = await inputs(env.service)
    request.pop("controlAssessment")
    request["frameworkPack"] = selection(identity)

    from moonmind.security.assessments import AssessmentRequest
    from moonmind.workflows.skills.security_assessment import AssessmentExecution
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    def no_scan(*args):
        raise AssertionError("An unverified source document started native collection")

    monkeypatch.setattr(recurring.security_assessment, "observe_inventory", no_scan)
    execution = AssessmentExecution(
        env.service,
        principal="operator",
        context={"namespace": "default", "workflow_id": "assessment", "run_id": "run"},
        access=access_for_test("workflow:assessment"),
    )
    with pytest.raises(ToolFailure, match="source document"):
        await execution.execute(AssessmentRequest.model_validate(request))

    # Accepting a pack whose source cannot be verified is refused too.
    with pytest.raises(PolicyPackError, match="Source document|changed"):
        await accept_policy_pack(
            PolicyPackAcceptRequest.model_validate(
                {
                    "pack": {
                        k: identity[k]
                        for k in (
                            "artifactRef",
                            "packId",
                            "edition",
                            "revision",
                            "sha256",
                        )
                    },
                    "review": {
                        "reviewedBy": "CISO",
                        "reviewedOn": "2026-11-20",
                        "effectiveFrom": "2026-11-20",
                    },
                    "projectId": "acme",
                    "environmentId": "production",
                }
            ),
            session=env.session,
            access=(
                access_for_test("workflow:assessment")
                if fault == "unauthorized"
                else OPERATOR
            ),
            artifacts=env.service,
        )


async def test_changed_policy_rerun_proposes_a_revision_without_touching_history(
    environment,
):
    env = environment
    await save_environment(env.session)
    first, _, _ = await drafted(env)
    operative = await accept(env, first)
    await link(env.service, operative["artifactRef"])
    outputs = await assess(env, operative)
    before_report = await recurring.read(env.service, outputs["controlAssessmentRef"])
    _, before_pack = await env.service.read(
        artifact_id=operative["artifactRef"], access=OPERATOR
    )

    changed = POLICY.replace("every quarter", "every month").replace(
        "Security logs must be retained for at least 365 days.\n", ""
    )

    def revise(value):
        rows = value["requirements"]
        rows[1]["locator"]["quote"] = "Access reviews shall be performed every month."
        rows.pop(2)
        return value

    previous = {
        k: operative[k]
        for k in ("artifactRef", "packId", "edition", "revision", "sha256")
    }
    second, _, _ = await drafted(
        env, changed, previous=previous, changes=revise, name="policy-v2.md"
    )
    assert (second["revision"], second["publicationStatus"]) == ("2", "draft")
    assert second["changes"]["removed"] == ["ACME-4.2-1"]
    assert second["changes"]["reworded"] == ["ACME-4.1-2"]
    assert second["changes"]["unchanged"] == ["ACME-4.1-1"]
    assert second["changes"]["documentsAdded"] and second["changes"]["documentsRemoved"]

    _, after_pack = await env.service.read(
        artifact_id=operative["artifactRef"], access=OPERATOR
    )
    assert after_pack == before_pack
    assert (
        await recurring.read(env.service, outputs["controlAssessmentRef"])
        == before_report
    )
    settings = await SettingsCatalogService(session=env.session).effective_value_async(
        SETTINGS_KEY
    )
    registered = settings.value["projects"][0]["environments"][0]["frameworks"]
    assert [item["revision"] for item in registered] == ["1"]

    accepted = await accept(env, second)
    settings = await SettingsCatalogService(session=env.session).effective_value_async(
        SETTINGS_KEY
    )
    registered = settings.value["projects"][0]["environments"][0]["frameworks"]
    assert [item["revision"] for item in registered] == ["2"]
    assert accepted["artifactRef"] != operative["artifactRef"]
    assert (
        await recurring.read(env.service, outputs["controlAssessmentRef"])
        == before_report
    )


async def test_accept_refuses_an_unknown_environment_before_publishing(
    environment, monkeypatch
):
    env = environment
    await save_environment(env.session)
    result, _, _ = await drafted(env)
    request = PolicyPackAcceptRequest.model_validate(
        {
            "draftArtifactRef": result["draftArtifactRef"],
            "draftSha256": result["draftSha256"],
            "review": {
                "reviewedBy": "CISO",
                "reviewedOn": "2026-11-20",
                "effectiveFrom": "2026-11-20",
            },
            "projectId": "acme",
            "environmentId": "staging",
        }
    )

    async def no_publish(**kwargs):
        raise AssertionError("A pack was published for an unknown environment")

    monkeypatch.setattr(
        env.service, "put_content_addressed_payload_complete", no_publish
    )
    with pytest.raises(PolicyPackError, match="saved project environment"):
        await accept_policy_pack(
            request, session=env.session, access=OPERATOR, artifacts=env.service
        )
