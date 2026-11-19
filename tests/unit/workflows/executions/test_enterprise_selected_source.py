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
    ids=[
        "foreign-host",
        "foreign-owner",
        "foreign-repository",
        "url-query",
        "untrusted-host",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection", ["selected-public", "repository-connection:git-default", None]
)
async def test_recorded_public_github_ssh_read_retains_existing_selection(
    monkeypatch, selection
):
    parameters = {
        "workspace": {
            "workspaceSource": {
                "kind": "repository",
                "repository": "git@github.com:Acme/Repo.git",
                "connectionRef": selection,
                "accessMode": "explicit",
            }
        }
    }
    monkeypatch.setattr(
        resolver,
        "_admitted_repository_record",
        AsyncMock(return_value=("root", "run", parameters)),
    )
    if selection is None:
        # The historical access owner resolves implicit defaults independently.
        monkeypatch.setattr(
            resolver,
            "load_admitted_repository_access",
            AsyncMock(return_value=("", False)),
        )
    selected = SimpleNamespace(endpoint_ref="https://github.com")
    access = AsyncMock(
        return_value=SimpleNamespace(
            connection=selected, credential=SimpleNamespace(token="recorded-token")
        )
    )
    monkeypatch.setattr(resolver, "select_github_access_for_launch", access)
    enterprise_load = AsyncMock(
        side_effect=AssertionError("SSH must retain the existing selection owner")
    )
    monkeypatch.setattr(
        resolver, "load_repository_connection_for_launch", enterprise_load
    )
    result = await resolver.resolve_admitted_git_read(
        "root:step", repository="Acme/Repo"
    )
    assert result == resolver.AdmittedGitRead(
        "https://github.com/Acme/Repo.git", "github.com", "recorded-token"
    )
    access.assert_awaited_once_with(
        selection or "repository-connection:git-default",
        repository="Acme/Repo",
        required_operations=("read",),
    )
    enterprise_load.assert_not_awaited()


@pytest.mark.asyncio
async def test_recorded_public_github_ssh_nonread_retains_repository_fence(monkeypatch):
    parameters = {"repository": "git@github.com:Acme/Repo.git"}
    monkeypatch.setattr(
        resolver,
        "_admitted_repository_record",
        AsyncMock(return_value=("root", "run", parameters)),
    )
    assert await resolver.acquire_admitted_repository_use(
        "root", repository="Acme/Repo", operation="branch_write"
    ) == (None, None)
    with pytest.raises(ValueError, match="conflicts with the recorded run"):
        await resolver.acquire_admitted_repository_use(
            "root", repository="Acme/Other", operation="branch_write"
        )


@pytest.mark.asyncio
async def test_untrusted_enterprise_route_cannot_block_public_route(
    tmp_path, monkeypatch
):
    from moonmind.auth.bound_acquisition import BoundAccessError
    from moonmind.workflows.executions.repository_contract import (
        RepositoryAssignment,
        RepositoryIdentity,
    )
    from tests.unit.workflows.executions.test_repository_scoped_routes_4005 import (
        _pat_connection,
        _assignment,
        route_db,
    )

    monkeypatch.setattr(
        settings.github, "github_trusted_api_hosts", "github.enterprise.test"
    )
    public = _pat_connection("repository-connection:public")
    enterprise = _pat_connection("repository-connection:enterprise").model_copy(
        update={"endpoint_ref": HOST}
    )
    authority = {"principal_ref": "owner:team-a", "principal_scope": ("system", None)}
    async with route_db(tmp_path) as sessions:
        async with sessions() as session:
            owner = RepositoryConnectionService(session)
            for selected in (public, enterprise):
                await owner.create_connection(
                    selected,
                    actor_ref="owner:team-a",
                    request_id=selected.id,
                    **authority,
                )
            await owner.set_assignment(
                _assignment(public.id, "public-id", display="Acme/Repo"),
                actor_ref="owner:team-a",
                request_id="public-assignment",
                **authority,
            )
            await owner.set_assignment(
                RepositoryAssignment(
                    connectionId=enterprise.id,
                    identity=RepositoryIdentity(
                        endpoint=HOST,
                        providerRepoId="enterprise-id",
                        displayName="Other/Repo",
                    ),
                    revision=1,
                    verified=True,
                ),
                actor_ref="owner:team-a",
                request_id="enterprise-assignment",
                **authority,
            )
        monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "")
        async with sessions() as session:
            owner = RepositoryConnectionService(session)
            # Trust removal denies explicit selection; it must not poison other routes.
            with pytest.raises(BoundAccessError):
                await owner._recorded_launch_assignment(
                    enterprise, HOST + "/Other/Repo.git"
                )
            assert (
                await owner.routed_connection_ref("Acme/Repo", **authority) == public.id
            )
            candidates = await owner.launch_candidates("Other/Repo", **authority)
            assert candidates == []
