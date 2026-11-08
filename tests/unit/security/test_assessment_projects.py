"""Saved assessment data stays separate from execution authority."""

import copy

import pytest
from pydantic import ValidationError

from moonmind.security.assessment_projects import (
    AssessmentProjects,
    AssessmentProjectSnapshot,
    apply_assessment_defaults,
    resolve_assessment_project,
)


def project_document():
    return {
        "projects": [
            {
                "id": "shop",
                "name": "Shop",
                "purpose": "Customer checkout",
                "boundaries": "Payment provider is out of scope",
                "environments": [
                    {
                        "id": "staging",
                        "name": "Staging",
                        "assets": ["shop-staging"],
                        "originCandidates": ["https://staging.example.com"],
                        "artifactRefs": ["art_report"],
                        "credentialRefs": ["db://audit"],
                        "budget": {
                            "baseSeconds": 60,
                            "maxSeconds": 120,
                            "progressStallSeconds": 30,
                        },
                        "period": {"start": "2026-10-01", "end": "2026-10-31"},
                    }
                ],
            }
        ]
    }


def test_resolve_freezes_config_and_preserves_explicit_empty_overrides():
    document = project_document()
    snapshot = resolve_assessment_project(
        document,
        project_id="shop",
        environment_id="staging",
        version=3,
        overrides={"artifactRefs": [], "assets": ["override"]},
    )
    document["projects"][0]["environments"][0]["assets"] = ["changed"]
    assert snapshot.configuration.assets == ["override"]
    assert snapshot.configuration.artifact_refs == []
    assert snapshot.source_version == 3
    assert snapshot.configuration.credential_refs == ["db://audit"]
    assert snapshot == AssessmentProjectSnapshot.model_validate(
        snapshot.model_dump(by_alias=True)
    )


def test_duplicate_environment_is_ambiguous_and_rejected():
    document = project_document()
    document["projects"][0]["environments"].append(
        copy.deepcopy(document["projects"][0]["environments"][0])
    )
    with pytest.raises(ValidationError):
        AssessmentProjects.model_validate(document)


@pytest.mark.parametrize(
    "field,value",
    [
        ("credentialRefs", ["AKIA_RAW_KEY"]),
        ("artifactRefs", ["https://example.com/private"]),
        ("originCandidates", ["https://user:password@example.com"]),
        ("grants", []),
        ("networkRefs", ["public"]),
    ],
)
def test_saved_config_rejects_secrets_and_authority_fields(field, value):
    document = project_document()
    document["projects"][0]["environments"][0][field] = value
    with pytest.raises(ValidationError):
        AssessmentProjects.model_validate(document)


def test_snapshot_integrity_is_data_integrity_not_permission():
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    raw = snapshot.model_dump(mode="json", by_alias=True)
    raw["configuration"]["assets"] = ["other"]
    with pytest.raises(ValidationError):
        AssessmentProjectSnapshot.model_validate(raw)
    assert "assessmentScope" not in raw
    assert "grants" not in raw["configuration"]


def test_evidence_processing_constraints_are_frozen_with_assessment_context():
    document = project_document()
    policy = {"modelDelivery": "local_only", "allowedProfileRefs": ["review-private"]}
    document["projects"][0]["environments"][0]["evidenceProcessing"] = policy
    snapshot = resolve_assessment_project(
        document, project_id="shop", environment_id="staging", version=2
    )
    raw = snapshot.model_dump(mode="json", by_alias=True)
    assert raw["configuration"]["evidenceProcessing"] == policy
    raw["configuration"]["evidenceProcessing"]["modelDelivery"] = "allowed"
    with pytest.raises(ValidationError):
        AssessmentProjectSnapshot.model_validate(raw)


def test_legacy_snapshot_keeps_its_original_wire_shape_and_digest():
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    # Captured with the previous v1 schema, before evidenceProcessing existed.
    assert snapshot.digest == "c8a27dd5231d7754e15743458b8418f941cc7ab4f8f19b2dfe4b763314741761"
    raw = snapshot.model_dump(mode="json", by_alias=True)
    assert "evidenceProcessing" not in raw["configuration"]
    assert AssessmentProjectSnapshot.model_validate(raw).model_dump(
        mode="json", by_alias=True
    ) == raw
    with_null = copy.deepcopy(raw)
    with_null["configuration"]["evidenceProcessing"] = None
    assert AssessmentProjectSnapshot.model_validate(with_null).model_dump(
        mode="json", by_alias=True
    ) == raw


@pytest.mark.parametrize(
    "policy",
    [
        {"modelDelivery": "fallback"},
        {"modelDelivery": "allowed", "allowedProfileRefs": ["same", "same"]},
        {"modelDelivery": "allowed", "allowedProfileRefs": [""]},
        {"modelDelivery": "allowed", "allowedProfileRefs": ["profile with spaces"]},
        {"modelDelivery": "allowed", "rawSecrets": True},
    ],
    ids=["unknown-mode", "duplicate-ref", "empty-ref", "invalid-ref", "extra-authority"],
)
def test_evidence_processing_rejects_ambiguous_or_unsupported_constraints(policy):
    document = project_document()
    document["projects"][0]["environments"][0]["evidenceProcessing"] = policy
    with pytest.raises(ValidationError):
        AssessmentProjects.model_validate(document)


def test_defaults_fill_missing_values_never_override_or_admit_credentials():
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    workflow = {
        "steps": [
            {
                "type": "tool",
                "tool": {
                    "id": "security.assess",
                    "inputs": {
                        "scope": {"assets": ["explicit"], "exclusions": []},
                        "reportRefs": [],
                        "tools": [],
                    },
                },
            }
        ]
    }
    apply_assessment_defaults(workflow, snapshot)
    inputs = workflow["steps"][0]["tool"]["inputs"]
    assert inputs["scope"] == {"assets": ["explicit"], "exclusions": []}
    assert inputs["reportRefs"] == []
    assert inputs["tools"] == []
    assert inputs["budget"]["maxSeconds"] == 120
    assert inputs["credentialRefs"] == inputs["networkRefs"] == []
    assert inputs["assessmentProject"]["configuration"]["assets"] == ["shop-staging"]
    assert "assessmentScope" not in workflow


def test_settings_accepts_typed_refs_without_relaxing_other_secret_guards():
    from api_service.services.settings_catalog import SettingsCatalogService

    service = SettingsCatalogService()
    entry = service._entries_by_key["security.assessment_projects"]
    assert not service._validation_issues_for_value(
        entry, project_document(), boundary="write_request"
    )
    raw_secret = project_document()
    raw_secret["projects"][0]["purpose"] = "token=private-value"
    assert service._validation_issues_for_value(
        entry, raw_secret, boundary="write_request"
    )
    unknown = project_document()
    unknown["projects"][0]["environments"][0]["password"] = "private-value"
    assert service._validation_issues_for_value(
        entry, unknown, boundary="write_request"
    )
    assert service._contains_unsafe_payload({"credentialRefs": ["db://audit"]})


@pytest.mark.parametrize("selector,field", [("tool", "args"), ("skill", "inputs")])
def test_defaults_preserve_legacy_selector_and_step_input_precedence(selector, field):
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    workflow = {
        "steps": [
            {
                selector: {
                    "name": "security.assess",
                    field: {"reportRefs": ["art_original"]},
                },
                "inputs": {"scope": {"assets": ["explicit"]}, "reportRefs": []},
            }
        ]
    }
    apply_assessment_defaults(workflow, snapshot)
    step = workflow["steps"][0]
    assert step[selector]["inputs"]["reportRefs"] == []
    assert step["inputs"]["scope"] == {"assets": ["explicit"]}
    assert step["inputs"]["assessmentProject"]["digest"] == snapshot.digest


def test_settings_normalizes_supported_aliases_for_shared_ui_and_api():
    from api_service.services.settings_catalog import _canonical_setting_value

    document = project_document()
    environment = document["projects"][0]["environments"][0]
    environment["artifact_refs"] = environment.pop("artifactRefs")
    value = _canonical_setting_value("security.assessment_projects", document)
    normalized = value["projects"][0]["environments"][0]
    assert normalized["artifactRefs"] == ["art_report"]
    assert "artifact_refs" not in normalized
    assert normalized["dataClassification"] == "internal"


@pytest.mark.parametrize("metadata_key", ["assessmentProject", "assessment_project"])
@pytest.mark.parametrize("clear", [False, True])
def test_assessment_defaults_visit_root_and_steps_and_canonicalize_metadata(
    metadata_key, clear
):
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    stale = {"credentialRefs": ["db://missing"]}
    root = {"id": " security.assess ", "inputs": {metadata_key: stale}}
    step = {"name": " security.assess ", "args": {metadata_key: stale}}
    workflow = {"tool": root, "steps": [{"type": "skill", "skill": step}]}
    apply_assessment_defaults(workflow, None if clear else snapshot)
    for tool in (root, step):
        for field in ("inputs", "args"):
            inputs = tool.get(field, {})
            assert "assessment_project" not in inputs
            if clear or field == "args":
                assert "assessmentProject" not in inputs
        if not clear:
            assert tool["inputs"]["assessmentProject"] == snapshot.model_dump(
                mode="json", by_alias=True
            )
            assert tool["inputs"]["scope"]["assets"] == ["shop-staging"]
            assert (
                tool["inputs"]["credentialRefs"] == tool["inputs"]["networkRefs"] == []
            )


@pytest.mark.parametrize("shape", ["root", "step"])
def test_assessment_defaults_reject_conflicting_security_selector_aliases(shape):
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    node = {"tool": {"id": "security.assess", "name": "other", "inputs": {}}}
    workflow = node if shape == "root" else {"steps": [{"type": "tool", **node}]}
    with pytest.raises(ValueError, match="matching id and name"):
        apply_assessment_defaults(workflow, snapshot)


@pytest.mark.parametrize("alias_style", ["camel", "snake"])
@pytest.mark.parametrize("report_refs", [[], ["art_explicit"]])
def test_assessment_defaults_preserve_supported_reference_input_aliases(
    alias_style, report_refs
):
    from moonmind.security.assessments import AssessmentRequest

    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    keys = (
        ("reportRefs", "credentialRefs", "networkRefs")
        if alias_style == "camel"
        else ("report_refs", "credential_refs", "network_refs")
    )
    workflow = {
        "tool": {
            "name": "security.assess",
            "inputs": {
                keys[0]: report_refs,
                keys[1]: [],
                keys[2]: [],
                "reviewRef": "art_review",
            },
        }
    }
    apply_assessment_defaults(workflow, snapshot)
    parsed = AssessmentRequest.model_validate(workflow["tool"]["inputs"])
    assert parsed.report_refs == report_refs
    assert parsed.credential_refs == parsed.network_refs == []
    assert parsed.tools == (["retained-report"] if report_refs else [])


@pytest.mark.parametrize(
    "camel,snake",
    [
        ("reportRefs", "report_refs"),
        ("credentialRefs", "credential_refs"),
        ("networkRefs", "network_refs"),
    ],
)
@pytest.mark.parametrize("same", [False, True])
def test_assessment_defaults_reject_duplicate_reference_aliases(camel, snake, same):
    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    workflow = {
        "tool": {
            "name": "security.assess",
            "inputs": {
                camel: [],
                snake: [] if same else ["unreviewed"],
                "reviewRef": "art_review",
            },
        }
    }
    with pytest.raises(ValueError, match="only one"):
        apply_assessment_defaults(workflow, snapshot)


def test_assessment_defaults_preserve_explicit_alias_override_layer():
    from moonmind.security.assessments import AssessmentRequest

    snapshot = resolve_assessment_project(
        project_document(), project_id="shop", environment_id="staging", version=1
    )
    workflow = {
        "tool": {
            "name": "security.assess",
            "inputs": {"reportRefs": ["art_selector"], "reviewRef": "art_review"},
        },
        "inputs": {"report_refs": [], "credential_refs": [], "network_refs": []},
    }
    apply_assessment_defaults(workflow, snapshot)
    parsed = AssessmentRequest.model_validate(workflow["tool"]["inputs"])
    assert parsed.report_refs == []
    assert parsed.tools == []
    assert workflow["inputs"] == workflow["tool"]["inputs"]
