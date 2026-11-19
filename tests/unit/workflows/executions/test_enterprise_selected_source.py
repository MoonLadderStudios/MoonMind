"""Explicit Enterprise sources remain bound to their selected assignment."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.repository_connections import RepositoryConnectionService
from moonmind.config.settings import settings
from moonmind.workflows.temporal.runtime import managed_api_key_resolve as resolver

HOST = "https://github.enterprise.test"
REMOTE = HOST + "/Acme/Repo.git"


def connection():
    return SimpleNamespace(
        id="selected-enterprise",
        endpoint_ref=HOST,
        provider="git",
        hosting_service="github",
        allowed_operations=["read"],
    )


def service(*, canonical=False):
    row = SimpleNamespace(
        connection_id="selected-enterprise",
        provider_repo_id=None if canonical else "42",
        canonical_remote=REMOTE if canonical else None,
        display_name="Acme/Repo",
        revision=3,
        verified=True,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row])
            )
        )
    )
    return RepositoryConnectionService(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("canonical", [False, True])
async def test_selected_enterprise_url_resolves_same_verified_assignment(
    monkeypatch, canonical
):
    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.enterprise.test"
    )
    owner = service(canonical=canonical)
    for target in (REMOTE, REMOTE.removesuffix(".git"), "Acme/Repo"):
        found = await owner._recorded_launch_assignment(connection(), target)
        assert found.revision == 3
        assert found.identity.endpoint == HOST
        assert found.identity.display_name == "Acme/Repo"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remote,trusted",
    [
        ("https://other.example/Acme/Repo.git", True),
        ("https://github.enterprise.test/Other/Repo.git", True),
        ("https://github.enterprise.test/Acme/Other.git", True),
        ("https://github.enterprise.test/Acme/Repo.git?token=secret", True),
        (REMOTE, False),
    ],
)
async def test_selected_enterprise_assignment_rejects_foreign_or_untrusted_target(
    monkeypatch, remote, trusted
):
    monkeypatch.setattr(
        settings.github,
        "github_trusted_api_hosts",
        "github.enterprise.test" if trusted else "",
    )
    with pytest.raises(ValueError):
        await service(canonical=True)._recorded_launch_assignment(connection(), remote)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested", ["Acme/Repo", REMOTE, REMOTE.removesuffix(".git")]
)
async def test_step_bound_enterprise_restore_reads_recorded_selected_source(
    monkeypatch, requested
):
    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.enterprise.test"
    )
    parameters = {
        "workspace": {
            "workspaceSource": {
                "kind": "repository",
                "repository": REMOTE,
                "branch": "main",
                "connectionRef": "selected-enterprise",
                "accessMode": "explicit",
            }
        }
    }
    monkeypatch.setattr(
        resolver,
        "_admitted_repository_record",
        AsyncMock(return_value=("root", "run", parameters)),
    )
    selected = connection()
    assignment_owner = service()

    async def load(ref, *, repository=None, **kwargs):
        assert ref == selected.id
        await assignment_owner._recorded_launch_assignment(selected, repository)
        return selected

    monkeypatch.setattr(resolver, "load_repository_connection_for_launch", load)
    secret = AsyncMock(return_value=SimpleNamespace(token="selected-enterprise-token"))
    monkeypatch.setattr(
        "moonmind.auth.github_credentials.resolve_connection_github_credential", secret
    )
    result = await resolver.resolve_admitted_git_read("root:step", repository=requested)
    assert result.remote == REMOTE
    assert result.host == "github.enterprise.test"
    assert result.token == "selected-enterprise-token"
    secret.assert_awaited_once()
    assert secret.call_args.args[0] is selected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "other-host",
        "other-repo",
        "untrusted",
        "missing",
        "retargeted",
        "wrong-id",
        "generic-hosting",
        "missing-hosting",
        "anonymous",
        "no-selection",
    ],
)
async def test_enterprise_restore_fails_before_secret_on_changed_authority(
    monkeypatch, change
):
    monkeypatch.setattr(
        settings.github,
        "github_trusted_api_hosts",
        "" if change == "untrusted" else "github.enterprise.test,other.example",
    )
    source = {
        "kind": "repository",
        "repository": REMOTE,
        "branch": "main",
        "connectionRef": "selected-enterprise",
        "accessMode": "explicit",
    }
    if change == "anonymous":
        source["accessMode"] = "anonymous"
    if change == "no-selection":
        source.pop("connectionRef")
    parameters = {"workspace": {"workspaceSource": source}}
    monkeypatch.setattr(
        resolver,
        "_admitted_repository_record",
        AsyncMock(return_value=("root", "run", parameters)),
    )
    selected = connection()
    if change == "retargeted":
        selected.endpoint_ref = "https://other.example"
    if change == "generic-hosting":
        selected.hosting_service = "generic_git"
    if change == "missing-hosting":
        del selected.hosting_service
    if change == "wrong-id":
        selected.id = "another-connection"

    async def load(ref, *, repository=None, **kwargs):
        if change == "missing":
            return None
        await service()._recorded_launch_assignment(selected, repository)
        return selected

    monkeypatch.setattr(resolver, "load_repository_connection_for_launch", load)
    secret = AsyncMock()
    monkeypatch.setattr(
        "moonmind.auth.github_credentials.resolve_connection_github_credential", secret
    )
    requested = (
        "https://other.example/Acme/Repo.git"
        if change == "other-host"
        else "Acme/Other"
        if change == "other-repo"
        else "Acme/Repo"
    )
    with pytest.raises(ValueError):
        await resolver.resolve_admitted_git_read("root:step", repository=requested)
    secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_selected_assignment_does_not_require_github_trust(monkeypatch):
    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "")
    selected = connection()
    selected.hosting_service = "generic_git"
    found = await service(canonical=True)._recorded_launch_assignment(selected, REMOTE)
    assert found.identity.canonical_remote == REMOTE


@pytest.mark.asyncio
async def test_generic_selected_assignment_does_not_gain_enterprise_url_alias(
    monkeypatch,
):
    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.enterprise.test"
    )
    selected = connection()
    selected.hosting_service = "generic_git"
    with pytest.raises(ValueError):
        await service()._recorded_launch_assignment(selected, REMOTE)
