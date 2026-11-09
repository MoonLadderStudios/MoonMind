from datetime import UTC, datetime

from moonmind.security.frameworks import EvidencePeriod, OrganisationalEvidenceRequest


def test_generated_documents_cannot_establish_operation():
    from moonmind.security.builtin_frameworks import review_requirement

    result = review_requirement(
        requirement_id="9.2", title="Internal audit", pack_id="iso-27001-readiness",
        edition="2022+Amd1:2024", systems=["isms"],
        period=EvidencePeriod(start="2026-11-01", end="2026-11-02"),
        requests=[OrganisationalEvidenceRequest(
            id="audit", category="internal_audit", purpose="occurrence",
            description="Audit records", owner="Security owner")],
        required_purposes=["occurrence"], now=datetime(2026,11,9,tzinfo=UTC),
        evidence_availability={},
    )
    assert result["status"] == "incomplete"
    assert result["evidenceReviews"][0]["state"] == "requested"


def test_builtin_framework_current_revision_admission():
    from moonmind.security.organisational_evidence import organisational_evidence_refs
    row = {
        "id": "audit", "category": "internal_audit", "purpose": "occurrence",
        "description": "Audit", "owner": "Owner", "revisions": [
            {"artifactRef": ref, "kind": "operational_record", "source": "Audit",
             "collectedAt": "2026-11-02T00:00:00Z", "expiresAt": "2027-01-01T00:00:00Z",
             "period": {"start": "2026-11-01", "end": "2026-11-02"},
             "population": ["isms"], "sensitivity": "internal"}
            for ref in ["art_old", "art_current"]
        ]}
    task = {"tool": {"id": "security.assess", "inputs": {"framework": {
        "packId": "iso-27001-readiness", "systemInputs": {
            "requirementEvidence": {"9.2": [row]}}}}}}
    assert organisational_evidence_refs(task) == ["art_current"]
