from __future__ import annotations

from pathlib import Path

from moonmind.capabilities.input_contracts import (
    parse_skill_capability_input_contract,
)


def test_batch_dependabot_resolver_exposes_structured_inputs() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    skill_path = (
        repo_root
        / ".agents"
        / "skills"
        / "batch-dependabot-resolver"
        / "SKILL.md"
    )
    contract = parse_skill_capability_input_contract(
        skill_id="batch-dependabot-resolver",
        label="batch-dependabot-resolver",
        markdown=skill_path.read_text(encoding="utf-8"),
        strict=True,
    )

    assert contract["hasInputSchema"] is True
    assert contract["inputSchema"]["properties"]["titleRegex"]["default"] == (
        r"^(?:Bump|[Cc]hore\(deps\): bump) .+ from \S+ to \S+(?: in /.+)?$"
    )
    assert contract["defaults"]["mergeMethod"] == "squash"
    assert contract["defaults"]["dryRun"] is False
    assert contract["diagnostics"] == []
