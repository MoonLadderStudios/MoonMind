import datetime as dt

import pytest

from moonmind.capabilities.input_contracts import (
    CapabilityInputContractError,
    CapabilityInputOwner,
    CapabilityInputContractParts,
    capability_contract_from_legacy_inputs,
    normalize_capability_input_contract,
    parse_skill_capability_input_contract,
    validate_capability_inputs,
)


def test_skill_frontmatter_snake_case_normalizes_to_camel_case_contract() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "description: Collect a target.\n"
        "input_schema:\n"
        "  type: object\n"
        "  required:\n"
        "    - target\n"
        "  properties:\n"
        "    target:\n"
        "      type: string\n"
        "      title: Target\n"
        "ui_schema:\n"
        "  target:\n"
        "    widget: textarea\n"
        "defaults:\n"
        "  target: MM-1047\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
        source={"kind": "test"},
    )

    assert contract["kind"] == "skill"
    assert contract["inputSchema"]["required"] == ["target"]
    assert contract["uiSchema"] == {"target": {"widget": "textarea"}}
    assert contract["defaults"] == {"target": "MM-1047"}
    assert contract["contractDigest"].startswith("sha256:")
    assert contract["contentDigest"].startswith("sha256:")
    assert contract["diagnostics"] == []


def test_non_object_skill_schema_is_diagnosed_without_invalidating_skill() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: string\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    assert contract["kind"] == "skill"
    assert contract["inputSchema"] == {}
    assert contract["diagnostics"][0]["code"] == "input_schema_root_not_object"


def test_skill_defaults_with_secret_like_keys_are_omitted() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    apiToken:\n"
        "      type: string\n"
        "defaults:\n"
        "  apiToken: example-value\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    assert contract["defaults"] == {}
    assert contract["diagnostics"][0]["code"] == "defaults_secret_like_value"


def test_skill_frontmatter_yaml_dates_are_json_compatible() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    due_date:\n"
        "      type: string\n"
        "      default: 2026-06-30\n"
        "defaults:\n"
        "  due_date: 2026-06-30\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    assert contract["inputSchema"]["properties"]["due_date"]["default"] == "2026-06-30"
    assert contract["defaults"] == {"due_date": "2026-06-30"}
    assert contract["contractDigest"].startswith("sha256:")


def test_equivalent_skill_and_preset_schemas_share_renderer_fields() -> None:
    schema = {
        "type": "object",
        "required": ["target"],
        "properties": {
            "target": {
                "type": "string",
                "title": "Target",
                "x-moonmind-context-default": "issue",
            }
        },
    }
    ui_schema = {"target": {"widget": "jira.issue-picker"}}
    defaults = {"target": "MM-1047"}
    skill = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=(
            "---\n"
            "name: Demo Skill\n"
            "inputSchema:\n"
            "  type: object\n"
            "  required: [target]\n"
            "  properties:\n"
            "    target:\n"
            "      type: string\n"
            "      title: Target\n"
            "      x-moonmind-context-default: issue\n"
            "uiSchema:\n"
            "  target:\n"
            "    widget: jira.issue-picker\n"
            "defaults:\n"
            "  target: MM-1047\n"
            "---\n"
            "# Demo\n"
        ),
    )
    preset = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id="demo-preset",
            kind="preset",
            label="Demo Preset",
        ),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": schema,
                "uiSchema": ui_schema,
                "defaults": defaults,
            },
        ),
    )

    for field in ("inputSchema", "uiSchema", "defaults"):
        assert skill[field] == preset[field]


def test_preset_contract_dates_are_json_compatible() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id="demo-preset",
            kind="preset",
            label="Demo Preset",
        ),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "due_date": {
                            "type": "string",
                            "default": dt.date(2026, 6, 30),
                        }
                    },
                },
                "defaults": {"due_date": dt.date(2026, 6, 30)},
            },
        ),
    )

    assert contract["inputSchema"]["properties"]["due_date"]["default"] == "2026-06-30"
    assert contract["defaults"] == {"due_date": "2026-06-30"}
    assert contract["contractDigest"].startswith("sha256:")


def test_one_of_const_options_are_preserved_for_renderer_choices() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id="demo-preset",
            kind="preset",
            label="Demo Preset",
        ),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "title": "Mode",
                            "oneOf": [
                                {"const": "safe", "title": "Safe"},
                                {"const": "fast", "title": "Fast"},
                            ],
                        }
                    },
                }
            },
        ),
    )

    assert contract["inputSchema"]["properties"]["mode"]["oneOf"] == [
        {"const": "safe", "title": "Safe"},
        {"const": "fast", "title": "Fast"},
    ]
    assert contract["diagnostics"] == []


def test_schema_object_defaults_are_preserved_as_values() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id="demo-preset",
            kind="preset",
            label="Demo Preset",
        ),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "issue": {
                            "type": "object",
                            "title": "Issue",
                            "default": {"key": "MM-1"},
                        }
                    },
                }
            },
        ),
    )

    assert contract["inputSchema"]["properties"]["issue"]["default"] == {"key": "MM-1"}
    assert contract["diagnostics"] == []


def test_unsupported_safe_schema_and_widget_metadata_are_diagnosed() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    target:\n"
        "      type: string\n"
        "      allOf:\n"
        "        - minLength: 3\n"
        "      x-moonmind-unknown-hint: true\n"
        "      customWidget: no\n"
        "uiSchema:\n"
        "  target:\n"
        "    widget: deployment.secret-picker\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    diagnostics = {item["code"]: item for item in contract["diagnostics"]}
    assert contract["inputSchema"]["properties"]["target"]["allOf"] == [
        {"minLength": 3}
    ]
    assert "customWidget" not in contract["inputSchema"]["properties"]["target"]
    assert diagnostics["unsupported_keyword"]["path"].startswith("inputSchema")
    assert diagnostics["ignored_hint"]["path"].endswith("x-moonmind-unknown-hint")
    assert diagnostics["unsupported_widget"]["path"] == "uiSchema.target.widget"


def test_secret_like_defaults_are_removed_from_schema_and_defaults() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    api_token:\n"
        "      type: string\n"
        "      default: ghp_thisShouldNotReachTheContract\n"
        "    branch:\n"
        "      type: string\n"
        "      default: main\n"
        "defaults:\n"
        "  api_token: password=hidden\n"
        "  branch: main\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    properties = contract["inputSchema"]["properties"]
    assert "default" not in properties["api_token"]
    assert properties["branch"]["default"] == "main"
    assert contract["defaults"] == {"branch": "main"}
    assert [
        item["code"] for item in contract["diagnostics"]
    ].count("defaults_secret_like_value") == 2


def test_strict_skill_contract_rejects_secret_like_defaults() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    token:\n"
        "      type: string\n"
        "defaults:\n"
        "  token: ghp_1234567890abcdef\n"
        "---\n"
        "# Demo\n"
    )

    with pytest.raises(CapabilityInputContractError, match="secret-like value"):
        parse_skill_capability_input_contract(
            skill_id="demo-skill",
            label="Demo Skill",
            markdown=markdown,
            strict=True,
        )


def test_strict_contract_records_remote_ref_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    target:\n"
        "      type: string\n"
        "      $ref: https://example.invalid/schema.json\n"
        "---\n"
        "# Demo\n"
    )

    caplog.set_level("INFO", logger="moonmind.capabilities.input_contracts")

    with pytest.raises(CapabilityInputContractError, match="Remote schema references"):
        parse_skill_capability_input_contract(
            skill_id="demo-skill",
            label="Demo Skill",
            markdown=markdown,
            strict=True,
        )

    assert any(
        getattr(record, "event", None) == "skill_input_schema_strict_policy_rejection"
        and getattr(record, "attributes", None) == {"code": "remote_ref_disabled"}
        for record in caplog.records
    )


def test_strict_contract_rejects_schema_size_limit() -> None:
    huge_description = "a" * (128 * 1024)

    with pytest.raises(CapabilityInputContractError, match="inputSchema exceeds"):
        normalize_capability_input_contract(
            owner=CapabilityInputOwner(
                id="demo-skill",
                kind="skill",
                label="Demo Skill",
            ),
            parts=CapabilityInputContractParts(
                input_schema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": huge_description,
                        }
                    },
                },
            ),
            strict=True,
        )


def test_oversized_skill_frontmatter_is_omitted_with_diagnostic() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        f"    target:\n      type: string\n      description: {'x' * 140000}\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    assert contract["inputSchema"] == {}
    assert contract["diagnostics"][0]["code"] == "frontmatter_too_large"
    assert contract["diagnostics"][0]["path"] == "frontmatter"


def test_validate_capability_inputs_reports_field_errors() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id="demo-skill",
            kind="skill",
            label="Demo Skill",
        ),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "required": ["issue_key", "due_date"],
                    "properties": {
                        "issue_key": {"type": "string"},
                        "priority": {"type": "string", "enum": ["low", "high"]},
                        "due_date": {"type": "string", "format": "date"},
                    },
                },
                "defaults": {"priority": "low"},
            },
        ),
    )

    result = validate_capability_inputs(
        contract=contract,
        values={"priority": "urgent", "due_date": "not-a-date"},
    )

    assert result["values"]["priority"] == "urgent"
    assert [error["code"] for error in result["errors"]] == [
        "required",
        "enum",
        "format",
    ]
    assert result["contractDigest"] == contract["contractDigest"]


def test_validate_capability_inputs_uses_input_schema_defaults_and_field_paths() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id="demo-skill",
            kind="skill",
            label="Demo Skill",
            content_digest="sha256:content",
        ),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "required": ["repository", "issue"],
                    "properties": {
                        "repository": {
                            "type": "string",
                            "x-moonmind-context-default": "repository",
                        },
                        "issue": {"type": "integer"},
                        "branch": {"type": "string", "default": "schema-main"},
                    },
                },
                "uiSchema": {"issue": {"widget": "jira.issue-picker"}},
                "defaults": {"branch": "default-main"},
            },
        ),
    )

    result = validate_capability_inputs(
        contract=contract,
        values={"issue": "MM-1057"},
        workflow_context={"repository": "MoonLadderStudios/MoonMind"},
        path_prefix="steps[0].skill.inputs",
    )

    assert result["values"]["repository"] == "MoonLadderStudios/MoonMind"
    assert result["values"]["branch"] == "default-main"
    assert result["errors"] == [
        {
            "path": "steps[0].skill.inputs.issue",
            "message": "Value must be an integer.",
            "code": "type",
            "recoverable": True,
        }
    ]
    assert result["contractDigest"] == contract["contractDigest"]
    assert result["contentDigest"] == "sha256:content"


def test_validate_capability_inputs_ignores_secret_defaults_and_remote_code_metadata() -> None:
    contract = {
        "inputSchema": {
            "type": "object",
            "$ref": "https://example.invalid/schema.json",
            "x-code": "return secret",
            "properties": {
                "token": {"type": "string"},
                "target": {
                    "type": "string",
                    "description": "<script>alert(1)</script>Target",
                },
            },
        },
        "uiSchema": {"target": {"widget": "remote.widget"}},
        "defaults": {"token": "ghp_not_a_real_token"},
        "contractDigest": "sha256:contract",
    }

    normalized = normalize_capability_input_contract(
        owner=CapabilityInputOwner(id="demo", kind="skill", label="Demo"),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations=contract,
        ),
    )
    result = validate_capability_inputs(
        contract=normalized,
        values={"target": "MM-1057"},
        path_prefix="steps[1].skill.inputs",
    )

    assert normalized["defaults"] == {}
    assert (
        normalized["inputSchema"]["properties"]["target"]["description"]
        == "alert(1)Target"
    )
    warning_codes = {warning["code"] for warning in result["warnings"]}
    assert "remote_ref_ignored" in warning_codes
    assert "schema_code_ignored" in warning_codes
    assert "unsupported_widget" in warning_codes
    assert "token" not in result["values"]


def test_validate_capability_inputs_applies_json_schema_constraints() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(id="demo", kind="skill", label="Demo"),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title"],
                    "properties": {
                        "title": {
                            "type": "string",
                            "minLength": 3,
                            "pattern": "^MM-",
                        },
                        "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                        "labels": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": ["bug", "docs"]},
                        },
                    },
                }
            },
        ),
    )

    result = validate_capability_inputs(
        contract=contract,
        values={
            "title": "no",
            "priority": 10,
            "labels": ["bug", "bug", "other"],
            "extra": True,
        },
        path_prefix="steps[2].skill.inputs",
    )

    errors_by_code = {error["code"]: error["path"] for error in result["errors"]}
    assert errors_by_code["additionalProperties"] == "steps[2].skill.inputs.extra"
    assert errors_by_code["minLength"] == "steps[2].skill.inputs.title"
    assert errors_by_code["pattern"] == "steps[2].skill.inputs.title"
    assert errors_by_code["maximum"] == "steps[2].skill.inputs.priority"
    assert errors_by_code["uniqueItems"] == "steps[2].skill.inputs.labels"
    assert "steps[2].skill.inputs.labels[2]" in [
        error["path"] for error in result["errors"] if error["code"] == "enum"
    ]


def test_validate_capability_inputs_checks_registered_reference_fields() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(id="demo", kind="skill", label="Demo"),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "issue": {
                            "type": "object",
                            "x-moonmind-semantic-type": "issue-reference",
                        },
                        "repository": {
                            "type": "string",
                            "x-moonmind-semantic-type": "repository",
                        },
                        "branch": {
                            "type": "string",
                            "x-moonmind-semantic-type": "branch",
                        },
                    },
                },
                "uiSchema": {
                    "issue": {"widget": "jira.issue-picker"},
                    "repository": {"widget": "github.repository-picker"},
                    "branch": {"widget": "github.branch-picker"},
                },
            },
        ),
    )

    result = validate_capability_inputs(
        contract=contract,
        values={"issue": {}, "repository": "MoonMind", "branch": ""},
        path_prefix="steps[3].skill.inputs",
    )

    assert [
        (error["path"], error["code"])
        for error in result["errors"]
        if error["code"] == "reference"
    ] == [
        ("steps[3].skill.inputs.issue", "reference"),
        ("steps[3].skill.inputs.repository", "reference"),
        ("steps[3].skill.inputs.branch", "reference"),
    ]


def test_validate_capability_inputs_handles_object_without_properties() -> None:
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(id="demo", kind="skill", label="Demo"),
        parts=capability_contract_from_legacy_inputs(
            inputs_schema=[],
            annotations={
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "emptyObject": {
                            "type": "object",
                            "additionalProperties": False,
                        }
                    },
                }
            },
        ),
    )

    result = validate_capability_inputs(
        contract=contract,
        values={"emptyObject": {"unexpected": True}},
    )

    assert result["errors"] == [
        {
            "path": "inputs.emptyObject.unexpected",
            "message": "Additional properties are not allowed.",
            "code": "additionalProperties",
            "recoverable": True,
        }
    ]
