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
