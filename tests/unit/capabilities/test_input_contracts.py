import datetime as dt
import pytest

from moonmind.capabilities.input_contracts import (
    CapabilityInputContractError,
    CapabilityInputOwner,
    CapabilityInputContractParts,
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


def test_lenient_skill_contract_blocks_remote_refs_and_executable_metadata() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "description: '<script>alert(1)</script>Safe text'\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    target:\n"
        "      type: string\n"
        "      $ref: https://example.invalid/schema.json\n"
        "      onClick: steal()\n"
        "      x-moonmind-widget: remote.component\n"
        "      x-foreign-widget: unsafe\n"
        "      description: '[bad](javascript:unsafe)Safe description'\n"
        "uiSchema:\n"
        "  target:\n"
        "    widget: https://example.invalid/widget.js\n"
        "    component: RemoteComponent\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    target = contract["inputSchema"]["properties"]["target"]
    assert "$ref" not in target
    assert "onClick" not in target
    assert "x-foreign-widget" not in target
    assert contract["uiSchema"]["target"] == {}
    assert contract["description"] == "Safe text"
    assert target["description"] == "Safe description"
    diagnostic_codes = {item["code"] for item in contract["diagnostics"]}
    assert "remote_ref_disabled" in diagnostic_codes
    assert "executable_metadata_ignored" in diagnostic_codes
    assert "ignored_hint" in diagnostic_codes
    assert "fallback_renderer" in diagnostic_codes
    assert "unsafe_markdown_ignored" in diagnostic_codes


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


def test_skill_contract_omits_secret_like_schema_default() -> None:
    markdown = (
        "---\n"
        "name: Demo Skill\n"
        "inputSchema:\n"
        "  type: object\n"
        "  properties:\n"
        "    token:\n"
        "      type: string\n"
        "      default: password=raw-secret\n"
        "---\n"
        "# Demo\n"
    )

    contract = parse_skill_capability_input_contract(
        skill_id="demo-skill",
        label="Demo Skill",
        markdown=markdown,
    )

    token_schema = contract["inputSchema"]["properties"]["token"]
    assert "default" not in token_schema
    assert {item["code"] for item in contract["diagnostics"]} >= {
        "secret_like_default",
    }


def test_strict_contract_records_remote_ref_rejection(caplog: pytest.LogCaptureFixture) -> None:
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
