"""Anonymous Git sources never acquire a hosting-provider credential."""

import pytest

from moonmind.omnigent.host_services.workspace import build_daemon_git_clone_argv
from moonmind.omnigent.repository_sources import anonymous_https_repository_source


def test_anonymous_remote_keeps_exact_case_sensitive_repository_path():
    source = "https://gitlab.example.test/group/nested/Project.git"
    assert anonymous_https_repository_source(source) == (
        source,
        "https://gitlab.example.test",
    )
    argv = build_daemon_git_clone_argv(
        volume="moonmind-test-workspaces",
        target_in_volume="attempt/repo",
        source=source,
        branch="main",
        image="alpine/git:v2.43.0",
        git_user_name="Test",
        git_user_email="test@example.test",
        authenticated=False,
        endpoint="https://gitlab.example.test",
    )
    script = argv[argv.index("-ceu") + 1]
    assert "credential.helper=" in script
    assert "http.followRedirects=false" in script
    assert "MM_GIT_TOKEN_FILE" not in script
    assert source in argv


@pytest.mark.parametrize(
    "source",
    [
        " https://gitlab.example.test/group/repo.git",
        "https://gitlab.example.test/group/rep\to.git",
        "https://gitlab.example.test:0/group/repo.git",
        "https://gitlab.example.test:65536/group/repo.git",
        "http://gitlab.example.test/group/repo.git",
        "/controller/local",
        "file:///controller/local",
        "ssh://gitlab.example.test/group/repo.git",
        "https://user:secret@gitlab.example.test/group/repo.git",
        "https://gitlab.example.test/group/repo.git?token=x",
        "https://gitlab.example.test/group/repo.git#branch",
        "https://gitlab.example.test/group/../other.git",
        "https://gitlab.example.test/group/%2e%2e/other.git",
    ],
    # Node IDs are retained as CI ownership evidence. Describe rejected inputs
    # without copying credential-shaped URL components into that evidence.
    ids=[
        "leading-space", "embedded-control", "zero-port", "out-of-range-port",
        "insecure-http", "local-path", "file-url", "ssh-url", "userinfo",
        "query", "fragment", "parent-path", "encoded-parent-path",
    ],
)
def test_implicit_authority_and_ambiguous_paths_are_not_anonymous_https(source):
    assert anonymous_https_repository_source(source) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,has_binding,valid",
    [
        ("https://gitlab.example.test/group/Project.git", False, True),
        ("https://gitlab.example.test/group/project.git", False, False),
        ("https://other.example.test/group/Project.git", False, False),
        ("https://gitlab.example.test/group/Project.git", True, False),
    ],
)
async def test_host_uses_exact_anonymous_snapshot_without_credential_lookup(
    requested, has_binding, valid
):
    import hashlib
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.auth.bound_acquisition import AccessMode, select_repository_authority
    from moonmind.omnigent.host_services.github_credentials import (
        OmnigentGithubCredentialService,
    )
    from moonmind.workflows.executions.repository_contract import RepositoryIdentity

    remote = "https://gitlab.example.test/group/Project.git"
    identity = RepositoryIdentity(
        endpoint="https://gitlab.example.test",
        canonicalRemote=remote,
        displayName=remote,
    )
    selection = select_repository_authority(
        access_mode=AccessMode.ANONYMOUS,
        principal_ref="workflow:test",
        principal_scope=("system", None),
        identity=identity,
        role="source_read",
        requested_operations=("read",),
        policy_revision=1,
    )
    body = json.dumps(
        {
            "selection": selection.model_dump(by_alias=True, mode="json"),
            "repositoryIdentity": identity.model_dump(by_alias=True, mode="json"),
        }
    ).encode()
    artifacts = SimpleNamespace(read_bytes=AsyncMock(return_value=body))
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={"source": SimpleNamespace(consumer="agent")}
            if has_binding
            else {},
            resolvedTools={
                "repositoryAccess": {
                    "source": {
                        "artifactRef": "artifact:test",
                        "snapshotRef": "repository-access-snapshot:sha256:"
                        + hashlib.sha256(body).hexdigest(),
                    }
                }
            },
        )
    )
    service = OmnigentGithubCredentialService(None, artifact_gateway=artifacts)
    options = {
        "plan": plan,
        "request": None,
        "role": "source_read",
        "operation": "read",
        "repository": requested,
        "execution_owner": "workflow:test",
        "consumer": "server",
    }
    if valid:
        assert await service.acquire_repository_use(**options) is None
    else:
        with pytest.raises(ValueError):
            await service.acquire_repository_use(**options)


def test_restore_https_identity_cannot_change_source_or_case():
    from pydantic import ValidationError

    from moonmind.schemas.checkpoint_restore_models import SandboxRestoreDestination

    remote = "https://gitlab.example.test/group/Project.git"
    values = {
        "kind": "sandbox",
        "workspaceId": "attempt",
        "relativePath": "repo",
        "stepExecutionId": "step",
        "repository": remote,
        "repositoryIdentity": {
            "endpoint": "https://gitlab.example.test",
            "canonicalRemote": remote,
            "displayName": remote,
        },
    }
    assert SandboxRestoreDestination.model_validate(values).repository == remote
    for bad in (
        remote.lower(),
        "https://other.example.test/group/Project.git",
        "",
        "/controller/local",
    ):
        with pytest.raises(ValidationError):
            SandboxRestoreDestination.model_validate({**values, "repository": bad})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters,requires_github,allowed",
    [
        ({}, False, True),
        ({"publishMode": "branch"}, False, False),
        ({"publishMode": "pr"}, False, False),
        ({"publishMode": "auto", "githubOperations": ["write"]}, False, False),
        ({"githubOperations": ["merge_request"]}, False, False),
        ({}, True, False),
    ],
)
async def test_admission_freezes_only_anonymous_source_read(
    monkeypatch, parameters, requires_github, allowed
):
    from api_service.services import omnigent_execution_plan_service as service

    persisted = []

    async def persist(**values):
        persisted.append(values["payload"])
        return "source", "sha256:" + "a" * 64

    monkeypatch.setattr(service, "persist_json_artifact", persist)
    remote = "https://gitlab.example.test/group/Project.git"
    options = {
        "session_factory": None,
        "db_session": None,
        "artifact_service": None,
        "principal": "workflow:test",
        "workflow_id": "test",
        "requires_github": requires_github,
        "parent_plan": None,
        "access": None,
        "initial_parameters": {
            "publishMode": "none",
            "workspace": {
                "workspaceSource": {
                    "kind": "repository",
                    "repository": remote,
                    "accessMode": "anonymous",
                }
            },
            **parameters,
        },
    }
    if not allowed:
        with pytest.raises(ValueError):
            await service._admit_repository_plan_inputs(**options)
        assert not persisted
        return
    result = await service._admit_repository_plan_inputs(**options)
    assert result["sourceKind"] == "anonymous"
    assert result["bindings"] == {}
    assert set(result["access"]) == {"source"}
    assert persisted[0]["selection"]["operations"] == ["read"]
    assert persisted[0]["selection"]["connectionId"] == "anonymous"
    assert persisted[0]["repositoryIdentity"]["canonicalRemote"] == remote


@pytest.mark.parametrize("bundle_only", [False, True])
def test_anonymous_restore_isolates_checkout_and_clean_filters(
    tmp_path, monkeypatch, bundle_only
):
    import subprocess
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.runtime.checkpoint_restore import (
        ManagedCheckpointRestoreService,
    )

    source = tmp_path / "source"
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.test"],
        check=True,
    )
    (source / ".gitattributes").write_text("tracked.txt filter=ambient\n")
    (source / "tracked.txt").write_text("retained content\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "source"], check=True)
    bundle = tmp_path / "history.bundle"
    subprocess.run(
        ["git", "-C", str(source), "bundle", "create", str(bundle), "HEAD"], check=True
    )
    leak = tmp_path / "ambient-leak"
    monkeypatch.setenv("GH_TOKEN", "must-not-reach-filters")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    for index, phase in enumerate(("smudge", "clean")):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", f"filter.ambient.{phase}")
        monkeypatch.setenv(
            f"GIT_CONFIG_VALUE_{index}", f'echo "$GH_TOKEN" >> {leak}; cat'
        )
    service = ManagedCheckpointRestoreService(
        authority_root=tmp_path / "authority",
        anonymous_repository_read_resolver=AsyncMock(),
    )
    destination = tmp_path / "restored"
    if bundle_only:
        service._git(["init", str(destination)])
        service._git(["fetch", str(bundle), "HEAD"], destination)
        target = "FETCH_HEAD"
    else:
        service._git(["clone", "--no-checkout", str(source), str(destination)])
        target = "HEAD"
    service._git(["checkout", "--detach", target], destination)
    service._git_bytes(["status", "--porcelain"], destination)
    assert (destination / "tracked.txt").read_text() == "retained content\n"
    assert not leak.exists()
