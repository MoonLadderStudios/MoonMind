import datetime as dt

from moonmind.capabilities.input_contracts import (
    CapabilityInputOwner,
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
    assert normalized["inputSchema"]["properties"]["target"]["description"] == "alert(1)Target"
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
