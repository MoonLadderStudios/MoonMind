"""Selected enterprise source classification is transport, not authority."""

import pytest

from moonmind.config.settings import settings
from moonmind.omnigent.repository_sources import selected_github_https_repository_source


def test_enterprise_source_requires_trusted_origin(monkeypatch):
    source = "https://git.enterprise.example.test/Owner/Repository.git"
    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "")
    assert selected_github_https_repository_source(source) is None
    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "git.enterprise.example.test")
    assert selected_github_https_repository_source(source) == (
        source, "https://git.enterprise.example.test"
    )


@pytest.mark.parametrize("source", [
    "ssh://git.enterprise.example.test/Owner/Repository.git",
    "https://git.enterprise.example.test/group/Owner/Repository.git",
    "https://git.enterprise.example.test/Owner",
    "https://git.enterprise.example.test/Owner/../Repository.git",
])
def test_unsupported_transports_are_not_enterprise_candidates(source):
    assert selected_github_https_repository_source(source) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("parameters,requires_github", [
    ({"publishMode": "branch"}, False),
    ({"publishMode": "pr"}, False),
    ({"githubOperations": ["write"]}, False),
    ({}, True),
])
async def test_enterprise_read_only_admission_never_acquires_publication_authority(
    monkeypatch, parameters, requires_github
):
    from api_service.services import omnigent_execution_plan_service as service

    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "git.enterprise.example.test")
    with pytest.raises(ValueError, match="read-only|collaboration authority"):
        await service._admit_repository_plan_inputs(
            session_factory=None, db_session=None, artifact_service=None,
            principal="workflow:test", workflow_id="test", parent_plan=None,
            requires_github=requires_github, access=None,
            initial_parameters={
                "publishMode": "none",
                "workspace": {"workspaceSource": {
                    "kind": "repository", "accessMode": "explicit",
                    "repository": "https://git.enterprise.example.test/Owner/Repository.git",
                    "connectionRef": "selected-enterprise",
                }},
                **parameters,
            },
        )
