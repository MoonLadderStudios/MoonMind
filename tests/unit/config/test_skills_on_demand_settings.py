import pytest

from moonmind.config.settings import WorkflowSettings


@pytest.mark.parametrize(
    "env_name",
    ["MOONMIND_SKILLS_ON_DEMAND_ENABLED", "WORKFLOW_SKILLS_ON_DEMAND_ENABLED"],
)
def test_skills_on_demand_setting_accepts_disabled_aliases(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
) -> None:
    monkeypatch.delenv("MOONMIND_SKILLS_ON_DEMAND_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_SKILLS_ON_DEMAND_ENABLED", raising=False)
    monkeypatch.setenv(env_name, "false")

    settings = WorkflowSettings(_env_file=None)

    assert settings.skills_on_demand_enabled is False


def test_skills_on_demand_setting_defaults_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOONMIND_SKILLS_ON_DEMAND_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_SKILLS_ON_DEMAND_ENABLED", raising=False)

    settings = WorkflowSettings(_env_file=None)

    assert settings.skills_on_demand_enabled is False

