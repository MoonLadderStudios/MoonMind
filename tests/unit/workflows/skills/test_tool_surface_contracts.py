"""Tests for MM-680 managed runtime surface contracts."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.tool_plan_contracts import ContractValidationError


def test_skill_surface_contract_parses_normalized_surfaces() -> None:
    from moonmind.workflows.skills.tool_plan_contracts import SkillSurfaceContract

    contract = SkillSurfaceContract.from_payload(
        {
            "tools": [" jira.transition_issue ", "repo.create_pr"],
            "mcpServers": ["trusted-jira"],
            "connectors": [],
            "egress": ["https://moonmind.local/mcp"],
            "publish": {"allowDirectPublish": False},
        }
    )

    assert contract.tools == ("jira.transition_issue", "repo.create_pr")
    assert contract.mcp_servers == ("trusted-jira",)
    assert contract.connectors == ()
    assert contract.egress == ("https://moonmind.local/mcp",)
    assert contract.publish.allow_direct_publish is False


def test_skill_surface_contract_requires_explicit_surface_fields() -> None:
    from moonmind.workflows.skills.tool_plan_contracts import SkillSurfaceContract

    with pytest.raises(ContractValidationError, match="mcpServers"):
        SkillSurfaceContract.from_payload(
            {
                "tools": ["repo.create_pr"],
                "connectors": [],
                "egress": [],
                "publish": {"allowDirectPublish": False},
            }
        )


def test_skill_surface_contract_allows_explicit_empty_surfaces() -> None:
    from moonmind.workflows.skills.tool_plan_contracts import SkillSurfaceContract

    contract = SkillSurfaceContract.from_payload(
        {
            "tools": [],
            "mcpServers": [],
            "connectors": [],
            "egress": [],
            "publish": {"allowDirectPublish": False},
        }
    )

    assert contract.tools == ()
    assert contract.mcp_servers == ()
    assert contract.connectors == ()
    assert contract.egress == ()

