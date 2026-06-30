from moonmind.capabilities.input_contracts import (
    CapabilityInputOwner,
    capability_contract_from_legacy_inputs,
    normalize_capability_input_contract,
    parse_skill_capability_input_contract,
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
