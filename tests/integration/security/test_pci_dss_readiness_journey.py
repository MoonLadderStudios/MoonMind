"""PCI DSS v4.0.1 readiness through security.assess and retained artifacts.

Trivy evidence arrives as retained image and repository scanner summaries; a
controlled observation stands in for TLS. The installed pack, the producers,
the shared evaluator, report retention and history comparison are the real
owners (MoonLadderStudios/MoonMind#5438).
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from moonmind.security.framework_registry import installed_pack
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_pci_dss_readiness import (
    SERVICE_PROVIDER,
    native_trivy_summaries,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
SCOPE = {"assets": ["app"], "exclusions": [], "repositoryExceptions": []}


def coverage(source, tool, *, all_rules=True, status="complete"):
    return {
        "source": source,
        "tool": tool,
        "assets": ["app"],
        "ruleIds": [],
        "allRules": all_rules,
        "configRef": f"{tool}:fixture",
        "status": status,
        "evidenceMethod": "deterministic_check",
    }


async def observation(service):
    now = datetime.now(UTC)
    tls = coverage("tls", "openssl", all_rules=False, status="partial")
    tls["ruleIds"] = ["certificate_expired"]
    return await recurring.put(
        service,
        {
            "schemaVersion": "security-observation/v1",
            "scope": SCOPE,
            "observedAt": now.isoformat(),
            "expiresAt": (now + timedelta(days=1)).isoformat(),
            "coverage": [tls],
            "findings": [
                {
                    "source": "tls",
                    "tool": "openssl",
                    "ruleId": "certificate_expired",
                    "asset": "app",
                    "nativeId": "tls:expired",
                    "evidenceRefs": ["art_tls_native"],
                }
            ],
            "provenance": {"fixture": "controlled PCI observation"},
        },
    )


def selection(**overrides):
    day = datetime.now(UTC).date().isoformat()
    return {
        "packId": "pci-dss-readiness",
        "edition": "4.0.1",
        "revision": "1",
        "period": {"start": day, "end": day},
        "profile": SERVICE_PROVIDER,
        **overrides,
    }


async def trivy_reports(service):
    """Retained native reports and the summaries the scanner jobs publish."""
    native = [await recurring.put(service, {"Results": []}) for _ in range(2)]
    return [
        await recurring.put(service, summary.model_dump(mode="json", by_alias=True))
        for summary in native_trivy_summaries(*native)
    ]


async def request(service, **overrides):
    return {
        "scope": SCOPE,
        "tools": ["retained-report"],
        "reportRefs": [await observation(service), *await trivy_reports(service)],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "frameworkPacks": [selection(**overrides)],
    }


def by_id(report):
    return {row["requirement"]["id"]: row for row in report["requirements"]}


def pack_metadata(report):
    # One selection is retained in the singular field.
    return report["frameworkPack"]


async def test_installed_pci_pack_concludes_only_named_sub_requirements(environment):
    service = environment.service
    result = await recurring.invoke(service, await request(service))
    assert result["controlAssessmentStatus"] == "completed", result
    report = await recurring.read(service, result["controlAssessmentRef"])
    rows = by_id(report)
    assert len(rows) == len(installed_pack("pci-dss-readiness")[1].requirements)
    assert not any(row["conclusion"] == "satisfied" for row in rows.values())
    outcomes = {o["id"]: o["outcome"] for o in rows["6.3.3"]["objectives"]}
    assert outcomes == {
        "image-vulnerabilities": "passed",
        "dependency-vulnerabilities": "passed",
    }
    assert rows["6.3.3"]["conclusion"] == "inconclusive"
    assert rows["4.2.1"]["conclusion"] == "not_satisfied"
    for key in ("4", "6", "4.2.1.1", "11.3.2"):
        assert rows[key]["conclusion"] == "inconclusive"
        assert rows[key]["objectives"] == []
    asv = rows["11.3.2"]["requirement"]["evidencePlan"]
    assert asv["externalParty"] == "PCI SSC Approved Scanning Vendor (ASV)"
    tra = rows["12.3.1"]["organisationalEvidence"][0]
    assert (tra["requestId"], tra["state"]) == ("12.3.1.tra", "requested")
    readiness = pack_metadata(report)["readiness"]
    assert {c["status"] for c in readiness["externalClaims"].values()} == {"Unverified"}
    assert pack_metadata(report)["publishedOn"] == "2024-06-11"
    _, text = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"Framework pack: pci-dss-readiness / 4.0.1 / 1." in text
    assert b"validation by a QSA, ISA or acquirer remains Unverified" in text
    assert b"external-only, closed by PCI SSC Approved Scanning Vendor (ASV)" in text


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"edition": "4.0"}, b"the installed edition is 4.0.1 revision 1"),
        ({"publicationStatus": "superseded"}, b"is superseded, not operative"),
        ({"effectiveFrom": "2099-01-01"}, b"effective from 2099-01-01"),
    ],
)
async def test_superseded_or_future_pci_edition_is_refused_readably(
    environment, overrides, reason
):
    from moonmind.security.assessments import AssessmentRequest
    from moonmind.workflows.skills.security_assessment import AssessmentExecution
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure

    service = environment.service
    payload = await request(service)
    if "edition" not in overrides:
        data = json.loads(installed_pack("pci-dss-readiness")[0])
        data.update(overrides)
        raw = json.dumps(data).encode()
        payload["frameworkPacks"][0].update(
            artifactRef=await recurring.put(service, raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
    else:
        payload["frameworkPacks"][0].update(overrides)
    execution = AssessmentExecution(
        service,
        principal="operator",
        context={"namespace": "default", "workflow_id": "assessment", "run_id": "run"},
        access=recurring.access_for_test("workflow:assessment"),
    )
    with pytest.raises(ToolFailure) as refused:
        await execution.execute(AssessmentRequest.model_validate(payload))
    assert reason in refused.value.message.encode()
    assert "frameworkPacks[0] (pci-dss-readiness)" in refused.value.message


async def test_new_pci_revision_reports_intelligence_change_against_retained_assessment(
    environment,
):
    service = environment.service
    first = await recurring.invoke(service, await request(service))
    data = json.loads(installed_pack("pci-dss-readiness")[0])
    data["revision"] = "2"
    data["requirements"][0]["summary"] += " Revised for the next edition."
    raw = json.dumps(data).encode()
    second = await recurring.invoke(
        service,
        await request(
            service,
            revision="2",
            artifactRef=await recurring.put(service, raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            previousAssessmentRef=first["controlAssessmentRef"],
        ),
        key="revision-2",
    )
    report = await recurring.read(service, second["controlAssessmentRef"])
    pack = pack_metadata(report)
    assert pack["previousAssessmentStatus"] == "available"
    changes = pack["intelligenceChanges"]
    assert any(": revision;" in row for row in changes)
    assert any(": requirementsDigest;" in row for row in changes)
    assert all("not remediation evidence" in row for row in changes)
    historical = await recurring.read(service, first["controlAssessmentRef"])
    assert pack_metadata(historical)["revision"] == "1"
