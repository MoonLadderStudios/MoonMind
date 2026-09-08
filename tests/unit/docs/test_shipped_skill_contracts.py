"""Validate shipped executable Markdown through the production Skill loader.

Instruction/authority tests in tests/unit/agents, tests/unit/capabilities, and
the portable resolver suites remain in place: frontmatter cannot prove prose
semantics. This adds coverage of every shipped bundle, not a new Skill parser.
"""

from pathlib import Path

import pytest

from moonmind.capabilities.input_contracts import (
    CapabilityInputContractError,
    parse_skill_capability_input_contract,
    parse_skill_markdown_frontmatter,
)
from moonmind.schemas.agent_skill_models import SkillSelector
from moonmind.services.skill_resolution import (
    BuiltInSkillLoader,
    SkillResolutionContext,
    extract_required_capabilities_from_skill_markdown,
    extract_required_skill_names_from_skill_markdown,
)

SKILLS = Path(__file__).resolve().parents[3] / ".agents/skills"
PATHS = sorted(SKILLS.glob("*/SKILL.md"))


@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.parent.name)
def test_shipped_skill_metadata_parses_at_the_authoring_boundary(path):
    text = path.read_text(encoding="utf-8")
    metadata, diagnostics = parse_skill_markdown_frontmatter(text, strict=True)
    assert not diagnostics
    assert metadata["name"] == path.parent.name
    assert isinstance(metadata["description"], str) and metadata["description"].strip()
    contract = parse_skill_capability_input_contract(
        markdown=text, skill_id=path.parent.name, label=path.parent.name, strict=True
    )
    assert not [item for item in contract["diagnostics"] if item["severity"] == "error"]
    extract_required_capabilities_from_skill_markdown(text, skill_name=path.parent.name)
    required = extract_required_skill_names_from_skill_markdown(
        text, skill_name=path.parent.name
    )
    assert all((SKILLS / name / "SKILL.md").is_file() for name in required)


@pytest.mark.asyncio
async def test_shipped_skills_resolve_through_production_loader():
    entries = await BuiltInSkillLoader(SKILLS).load_skills(
        SkillSelector(), SkillResolutionContext(snapshot_id="docs-contract-test")
    )
    by_name = {entry.skill_name: entry for entry in entries}
    assert PATHS
    for path in PATHS:
        entry = by_name[path.parent.name]
        assert Path(entry.provenance.source_path).resolve() == path.parent.resolve()


@pytest.mark.parametrize("mutation", ["unclosed", "invalid_yaml", "non_mapping"])
def test_malformed_shipped_frontmatter_is_rejected(mutation):
    text = (SKILLS / "pr-resolver/SKILL.md").read_text(encoding="utf-8")
    parse_skill_markdown_frontmatter(text, strict=True)
    if mutation == "unclosed":
        text = text.replace("\n---", "\nmissing delimiter", 1)
    elif mutation == "invalid_yaml":
        text = text.replace("name: pr-resolver", "name: [unclosed", 1)
    else:
        text = "---\n- not-a-mapping\n---\n" + text.split("\n---", 1)[1]
    with pytest.raises(CapabilityInputContractError):
        parse_skill_markdown_frontmatter(text, strict=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_contract", ["unknown-terminal.v1", "unsafe-path"])
async def test_shipped_terminal_semantics_are_validated_by_loader(
    tmp_path, bad_contract
):
    text = (SKILLS / "pr-resolver/SKILL.md").read_text(encoding="utf-8")
    if bad_contract == "unsafe-path":
        metadata, _ = parse_skill_markdown_frontmatter(text, strict=True)
        current = metadata["metadata"]["sideEffect"]["outcomeArtifact"]
        text = text.replace(current, "../escape.json")
    else:
        text = text.replace("pr_resolver_terminal.v1", bad_contract)
    skill_dir = tmp_path / "pr-resolver"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        await BuiltInSkillLoader(tmp_path).load_skills(
            SkillSelector(), SkillResolutionContext(snapshot_id="invalid-contract-test")
        )
