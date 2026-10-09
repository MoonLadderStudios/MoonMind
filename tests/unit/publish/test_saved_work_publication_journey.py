"""Publish Saved Work journey (MoonLadderStudios/MoonMind#4018).

A real managed capture writes saved work through the production checkpoint
artifact path into a sqlite-backed artifact service. The real publication
workflow code then drives the real saved-work Activities against local bare
Git remotes with a recording provider fixture (no live GitHub). The dispatcher
emulates Temporal's Activity retries and failure wrapping.

The original workspace is deleted before publication; the checkpoint-restore
clone path and every agent launcher are wired to fail if touched.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.exceptions import CancelledError as TemporalCancelledError
from temporalio.exceptions import TimeoutError as TemporalTimeoutError
from temporalio.exceptions import (
    RetryState,
    TimeoutType,
    is_cancelled_exception,
)

from api_service.db.models import Base
from moonmind.auth import github_credentials
from moonmind.auth.github_credentials import (
    GitHubCredentialSource,
    ResolvedGitHubCredential,
)
from moonmind.config.settings import settings
from moonmind.publish import saved_candidate as saved_candidate_module
from moonmind.workflows.adapters.github_service import (
    CreatePRResult,
    GitHubService,
    PullRequestReconciliation,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    TemporalIntegrationActivities,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.publication_recovery import (
    SavedWorkPublicationDestination,
    saved_work_publication_operation_key,
)
from moonmind.workflows.temporal.runtime import (
    checkpoint_restore,
    managed_api_key_resolve,
)
from moonmind.workflows.temporal.workflows import (
    publication_recovery as workflow_module,
)
from moonmind.workflows.temporal.workflows.publication_recovery import (
    MoonMindPublicationRecoveryWorkflow,
)
from tests.support.saved_work_capture import CapturedSavedWork, capture_saved_work, git

OPERATOR = str(uuid.UUID(int=4018))
DESTINATION_TOKEN = "admitted-destination-token"
REPOSITORY = "dest-owner/dest-repo"


@pytest.fixture(autouse=True)
def _enforced_access(monkeypatch):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    monkeypatch.setattr(settings.security, "high_security_mode", False)


@asynccontextmanager
async def _artifact_service(tmp_path: Path):
    # In-memory schema creation keeps the journey inside the fast unit budget.
    engine = create_async_engine(
        "sqlite+aiosqlite://", future=True, poolclass=StaticPool
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_maker() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
    finally:
        await engine.dispose()


def _mutate(repo: Path) -> None:
    (repo / "src" / "app.py").write_text("print('saved')\n")
    (repo / "gone.txt").unlink()
    (repo / "notes").mkdir()
    (repo / "notes" / "new.md").write_text("added by the run\n")


BASE_FILES = {"src/app.py": "print('base')\n", "gone.txt": "removed by the run\n"}


def _bare_destination(tmp_path: Path) -> Path:
    remote = tmp_path / "destination.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(remote))
    return remote


def _write_destination(tmp_path: Path, remote: Path, files: dict[str, str]) -> str:
    writer = tmp_path / f"writer-{uuid.uuid4().hex[:6]}"
    git(tmp_path, "clone", "-q", str(remote), str(writer))
    if not git(writer, "for-each-ref", "refs/remotes/origin/main"):
        git(writer, "checkout", "-q", "-b", "main")
    for name, text in files.items():
        (writer / name).parent.mkdir(parents=True, exist_ok=True)
        (writer / name).write_text(text)
    git(writer, "add", "-A")
    git(
        writer,
        "-c",
        "user.name=other",
        "-c",
        "user.email=other@example.invalid",
        "commit",
        "-qm",
        "destination writer",
    )
    git(writer, "push", "-q", "origin", "HEAD:main")
    return git(writer, "rev-parse", "HEAD")


def _tree(remote: Path, rev: str) -> dict[str, str]:
    names = git(remote, "ls-tree", "-r", "--name-only", rev).splitlines()
    return {name: git(remote, "show", f"{rev}:{name}") for name in names}


class Provider:
    """Recording GitHub PR provider backed by the bare remote's branch tips."""

    def __init__(self, remote: Path) -> None:
        self.remote = remote
        self.pull_requests: list[dict[str, Any]] = []
        self.creates: list[dict[str, Any]] = []
        self.tokens: list[str] = []
        self.unavailable_reads = 0
        self.rate_limited_creates = 0

    async def reconcile(self, **kwargs: Any) -> PullRequestReconciliation:
        self.tokens.append(kwargs["github_token"])
        if self.unavailable_reads:
            self.unavailable_reads -= 1
            return PullRequestReconciliation(state="unavailable", retryable=True)
        for pr in self.pull_requests:
            if (pr["head"], pr["base"]) != (kwargs["head"], kwargs["base"]):
                continue
            if pr["state"] != "open":
                return PullRequestReconciliation(
                    state=pr["state"], url=pr["url"], headSha=pr["sha"]
                )
            matched = pr["sha"] == kwargs["expected_head_sha"]
            return PullRequestReconciliation(
                state="matched" if matched else "mismatched",
                url=pr["url"],
                headSha=pr["sha"],
            )
        # Like the adapter, absence is reported only while the head holds
        # the candidate, so a create never targets another actor's commit.
        tip = git(
            self.remote,
            "for-each-ref",
            "--format=%(objectname)",
            f"refs/heads/{kwargs['head']}",
        )
        if tip != kwargs["expected_head_sha"]:
            return PullRequestReconciliation(state="mismatched", headSha=tip or None)
        return PullRequestReconciliation(state="absent")

    async def create(self, **kwargs: Any) -> CreatePRResult:
        self.tokens.append(kwargs["github_token"])
        self.creates.append(kwargs)
        if self.rate_limited_creates:
            self.rate_limited_creates -= 1
            return CreatePRResult(
                created=False, retryable=True, retryAfterSeconds=61, summary="HTTP 429"
            )
        sha = git(self.remote, "rev-parse", f"refs/heads/{kwargs['head']}")
        url = f"https://github.com/{REPOSITORY}/pull/{len(self.pull_requests) + 1}"
        self.pull_requests.append(
            {
                "head": kwargs["head"],
                "base": kwargs["base"],
                "state": "open",
                "sha": sha,
                "url": url,
            }
        )
        return CreatePRResult(created=True, url=url, headSha=sha, summary="created")


class _FailIfUsed:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"publication must not use {name}")


@dataclass
class Journey:
    saved: CapturedSavedWork
    service: TemporalArtifactService
    remote: Path
    provider: Provider
    runtime: TemporalAgentRuntimeActivities
    artifacts_root: Path
    runs: int = 0
    calls: list[str] = field(default_factory=list)
    git_commands: list[list[str]] = field(default_factory=list)
    authority_source: str = "GITHUB_TOKEN_SECRET_REF"
    authority_outages: int = 0
    sleeps: list[Any] = field(default_factory=list)
    hooks: dict[str, Callable[[int], Awaitable[None]]] = field(default_factory=dict)
    after: dict[str, Callable[[int], Awaitable[None]]] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)

    def attempt(self, name: str) -> int:
        """Number this attempt of one Activity across every retry and run."""
        self.attempts[name] = self.attempts.get(name, 0) + 1
        return self.attempts[name]

    def pushes(self) -> list[list[str]]:
        return [args for args in self.git_commands if "push" in args]

    def contract(
        self,
        *,
        principal: str = OPERATOR,
        title: str | None = "Publish saved work",
        **destination: Any,
    ) -> dict[str, Any]:
        parsed = SavedWorkPublicationDestination.model_validate(
            {"repository": REPOSITORY, "headBranch": "saved/work", **destination}
        )
        return {
            "schemaVersion": "saved-work-publication-v1",
            "sourceWorkflowId": self.saved.workflow_id,
            "sourceRunId": self.saved.run_id,
            "savedWorkRef": self.saved.saved_work_ref,
            "savedWorkDigest": self.saved.saved_work_digest,
            "admittedPrincipal": principal,
            "destination": parsed.model_dump(by_alias=True, mode="json"),
            "githubAuthorityRef": "github:repository-default",
            "commit": {
                "message": "Publish saved work from mm:source",
                "authorName": "MoonMind Worker",
                "authorEmail": "moonmind-worker@users.noreply.github.com",
                "timestamp": "2026-09-30T09:25:00+00:00",
            },
            "pullRequestTitle": title if parsed.objective != "branch" else None,
            "pullRequestBody": "Saved work from mm:source",
            "publicationIdempotencyKey": saved_work_publication_operation_key(
                saved_work_digest=self.saved.saved_work_digest,
                destination=parsed,
                github_authority_ref="github:repository-default",
            ),
        }

    @property
    def run_id(self) -> str:
        return f"publication-run-{self.runs}"

    async def run(self, contract: dict[str, Any]) -> dict[str, Any]:
        # Each workflow run gets its own Temporal run id.
        self.runs += 1
        return await MoonMindPublicationRecoveryWorkflow().run(contract)

    async def use_claims(self) -> list[Any]:
        from sqlalchemy import select

        from api_service.db import models as db_models

        session = self.service._repository._session
        rows = await session.execute(select(db_models.TemporalArtifactUseClaim))
        return list(rows.scalars().all())

    def saved_objects_unchanged(self, before: dict[str, bytes]) -> bool:
        """Every pre-existing saved object keeps its exact bytes."""
        after = self.saved_bytes()
        return {name: after.get(name) for name in before} == before

    def saved_bytes(self) -> dict[str, bytes]:
        return {
            path.name: path.read_bytes()
            for path in sorted(self.artifacts_root.rglob("*"))
            if path.is_file()
        }


@asynccontextmanager
async def journey(
    tmp_path: Path,
    monkeypatch,
    *,
    destination_files=None,
    seed_baseline=False,
    emulate_temporal=True,
    saved_work_factory=None,
):
    """Saved work, destination, and provider fixtures for one journey.

    ``emulate_temporal=False`` leaves ``workflow.execute_activity`` alone so a
    real Temporal worker can run the same Activities.
    """
    async with _artifact_service(tmp_path) as service:
        saved = (
            await saved_work_factory(service)
            if saved_work_factory is not None
            else await capture_saved_work(
                tmp_path, BASE_FILES, _mutate, artifact_service=service
            )
        )
        remote = _bare_destination(tmp_path)
        if seed_baseline:
            git(
                saved.source_repo,
                "push",
                "-q",
                str(remote),
                f"{saved.baseline_commit}:refs/heads/main",
            )
        if destination_files:
            _write_destination(tmp_path, remote, destination_files)
        saved.remove_source()
        provider = Provider(remote)
        runtime = TemporalAgentRuntimeActivities(
            artifact_service=service,
            client_adapter=object(),
            run_launcher=_FailIfUsed(),
            run_supervisor=_FailIfUsed(),
            session_controller=_FailIfUsed(),
            workspace_root=tmp_path / "worker",
        )
        state = Journey(
            saved=saved,
            service=service,
            remote=remote,
            provider=provider,
            runtime=runtime,
            artifacts_root=tmp_path / "artifacts",
        )

        async def resolve(explicit_token=None, *, repo=None):
            assert repo == REPOSITORY and explicit_token is None
            if state.authority_outages:
                state.authority_outages -= 1
                return ResolvedGitHubCredential(
                    source=GitHubCredentialSource.UNRESOLVABLE,
                    sourceName=state.authority_source,
                    repo=repo,
                    diagnostic="GitHub credential reference could not be resolved.",
                    retryable=True,
                )
            return ResolvedGitHubCredential(
                token=DESTINATION_TOKEN,
                source=GitHubCredentialSource.SECRET_REF_ENV,
                sourceName=state.authority_source,
                repo=repo,
            )

        async def selected_access(
            connection_ref, *, repository, required_operations=()
        ):
            assert connection_ref == "repository-connection:git-default"
            return managed_api_key_resolve.SelectedGitHubAccess(
                connection=None, credential=await resolve(repo=repository)
            )

        async def no_source_clone(*_args, **_kwargs):
            raise AssertionError(
                "publication must not resolve an original-source token"
            )

        real_exec = asyncio.create_subprocess_exec

        async def recording(*args: Any, **kwargs: Any):
            state.git_commands.append([str(arg) for arg in args])
            return await real_exec(*args, **kwargs)

        async def reconcile(self, **kwargs):
            return await provider.reconcile(**kwargs)

        async def create(self, **kwargs):
            return await provider.create(**kwargs)

        async def no_ambient_credential(*_args, **_kwargs):
            raise AssertionError(
                "publication must use the default repository connection"
            )

        monkeypatch.setattr(
            managed_api_key_resolve, "select_github_access_for_launch", selected_access
        )
        monkeypatch.setattr(
            github_credentials, "resolve_github_credential", no_ambient_credential
        )
        monkeypatch.setattr(
            checkpoint_restore, "resolve_github_token_for_launch", no_source_clone
        )
        monkeypatch.setattr(
            saved_candidate_module, "github_remote_url", lambda _repo: str(remote)
        )
        monkeypatch.setattr(
            saved_candidate_module.asyncio, "create_subprocess_exec", recording
        )
        monkeypatch.setattr(GitHubService, "reconcile_pull_request", reconcile)
        monkeypatch.setattr(GitHubService, "create_pull_request", create)

        handlers = {
            "publication_recovery.saved_work_prepare": runtime.publication_recovery_saved_work_prepare,
            "publication_recovery.saved_work_push": runtime.publication_recovery_saved_work_push,
            "publication_recovery.saved_work_pull_request": (
                TemporalIntegrationActivities().publication_recovery_saved_work_pull_request
            ),
            "publication_recovery.persist_result": (
                TemporalArtifactActivities(service).publication_recovery_persist_result
            ),
            "publication_recovery.cleanup": runtime.publication_recovery_cleanup,
        }

        def failed(name: str, attempt: int) -> ActivityError:
            return ActivityError(
                "activity failed",
                scheduled_event_id=1,
                started_event_id=2,
                identity="journey",
                activity_type=name,
                activity_id=str(attempt),
                retry_state=RetryState.NON_RETRYABLE_FAILURE,
            )

        async def execute_activity(name: str, payload: dict[str, Any], **kwargs: Any):
            state.calls.append(name)
            policy = kwargs.get("retry_policy")
            attempts = (policy.maximum_attempts if policy else 0) or 5
            for attempt in range(1, attempts + 1):
                number = state.attempt(name)
                try:
                    if name in state.hooks:
                        await state.hooks[name](number)
                    result = await handlers[name](payload)
                    if name in state.after:
                        await state.after[name](number)
                    return result
                except ApplicationError as exc:
                    if exc.non_retryable or attempt == attempts:
                        raise failed(name, attempt) from exc
                except _WorkerLost:
                    if attempt == attempts:
                        # Temporal observes a lost worker as a timeout.
                        raise failed(name, attempt) from TemporalTimeoutError(
                            "activity timed out",
                            type=TimeoutType.START_TO_CLOSE,
                            last_heartbeat_details=[],
                        )
            raise AssertionError(f"{name} exhausted retries")

        async def no_wait(duration: Any, **_kwargs: Any) -> None:
            state.sleeps.append(duration)
            await asyncio.sleep(0)

        if emulate_temporal:
            monkeypatch.setattr(
                workflow_module.workflow, "execute_activity", execute_activity
            )
            monkeypatch.setattr(workflow_module.workflow, "sleep", no_wait)
            monkeypatch.setattr(
                workflow_module.workflow,
                "info",
                lambda: SimpleNamespace(
                    task_queue="mm.workflow.user.v2",
                    workflow_id="mm:source:saved-work-publication:x",
                    run_id=state.run_id,
                ),
            )
        yield state


class _WorkerLost(Exception):
    """The worker died after the Activity's effect but before it reported."""


def _only_publication_activities(state: Journey) -> None:
    assert state.calls and all(
        name.startswith("publication_recovery.") for name in state.calls
    )


def _only_destination_remote(state: Journey) -> None:
    for args in state.git_commands:
        if {"push", "fetch", "ls-remote"} & set(args):
            assert str(state.remote) in args, args


@pytest.mark.asyncio
async def test_additive_pr_publishes_saved_content_with_fresh_authority_only(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "destination only\n"}
    ) as state:
        before = state.saved_bytes()
        claims_during_push: list[Any] = []

        async def observe_claims(attempt: int) -> None:
            claims_during_push.extend(await state.use_claims())

        state.hooks["publication_recovery.saved_work_push"] = observe_claims
        result = await state.run(
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            )
        )

        assert result["outcome"] == "published"
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        assert (
            result["push"]["status"] == "pushed"
            and result["push"]["remoteVerified"] is True
        )
        assert result["push"]["remoteHeadSha"] == head == result["candidate"]["headSha"]
        assert result["pullRequest"]["status"] == "created"
        assert _tree(state.remote, head) == {
            "README.md": "destination only",
            "src/app.py": "print('saved')",
            "notes/new.md": "added by the run",
        }
        assert [c["github_token"] for c in state.provider.creates] == [
            DESTINATION_TOKEN
        ]
        assert result["admission"]["authorityRef"] == (
            "github:repository-default#secret_ref_env:GITHUB_TOKEN_SECRET_REF"
        )
        assert result["implementationRerun"] is False
        assert state.calls == [
            "publication_recovery.saved_work_prepare",
            "publication_recovery.saved_work_push",
            "publication_recovery.saved_work_pull_request",
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        _only_publication_activities(state)
        _only_destination_remote(state)
        assert state.saved_objects_unchanged(before)
        # Every saved object publication reads is claimed for this operation.
        assert {
            (c.artifact_id, c.operation_kind, c.owner_principal)
            for c in claims_during_push
        } == {
            (artifact_id, "publication", OPERATOR)
            for artifact_id in await _publication_closure(state)
        }
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_recorded_default_connection_publishes_instead_of_an_ambient_token(
    tmp_path, monkeypatch
):
    """The ``github:repository-default`` authority is the recorded default
    connection, never an ambient deployment token (MoonLadderStudios/MoonMind#4003).
    """

    from moonmind.workflows.executions.repository_contract import (
        DEFAULT_GIT_CONNECTION_REF,
    )
    from tests.helpers.repository_connections import (
        github_pat_connection,
        github_repository_assignment,
        record_repository_connections,
    )

    real_access = managed_api_key_resolve.select_github_access_for_launch
    real_ambient = github_credentials.resolve_github_credential
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "destination only\n"}
    ) as state:
        monkeypatch.setattr(
            managed_api_key_resolve,
            "select_github_access_for_launch",
            real_access,
        )
        monkeypatch.setattr(
            github_credentials, "resolve_github_credential", real_ambient
        )
        monkeypatch.setenv("GITHUB_TOKEN", "ambient-token")
        monkeypatch.setenv("DEFAULT_CONNECTION_PAT", "default-connection-token")
        engine = await record_repository_connections(
            monkeypatch,
            tmp_path,
            github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "DEFAULT_CONNECTION_PAT"),
            assignments=(
                github_repository_assignment(DEFAULT_GIT_CONNECTION_REF, REPOSITORY),
            ),
        )
        try:
            result = await state.run(
                state.contract(
                    objective="pr", baseBranch="main", strategy="additive_import"
                )
            )
        finally:
            await engine.dispose()

        assert result["outcome"] == "published"
        assert [c["github_token"] for c in state.provider.creates] == [
            "default-connection-token"
        ]
        authority = result["admission"]["authorityRef"]
        assert f":{DEFAULT_GIT_CONNECTION_REF}" in authority
        assert authority.endswith(":policy:1:credential:1")
        assert "default-connection-token" not in authority


@pytest.mark.asyncio
async def test_same_baseline_applies_recorded_delta_and_keeps_destination_changes(
    tmp_path, monkeypatch
):
    async with journey(tmp_path, monkeypatch, seed_baseline=True) as state:
        _write_destination(tmp_path, state.remote, {"other.txt": "another writer\n"})
        result = await state.run(
            state.contract(
                objective="branch", baseBranch="main", strategy="baseline_delta"
            )
        )

        assert result["outcome"] == "published"
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        assert _tree(state.remote, head) == {
            "src/app.py": "print('saved')",
            "notes/new.md": "added by the run",
            "other.txt": "another writer",
        }
        assert result["candidate"]["deletedPaths"] == ["gone.txt"]
        assert state.provider.creates == [] and state.provider.tokens == []


@pytest.mark.asyncio
async def test_empty_destination_is_initialized_only_after_confirmed_emptiness(
    tmp_path, monkeypatch
):
    async with journey(tmp_path, monkeypatch) as state:
        contract = state.contract(
            objective="branch", headBranch="main", strategy="empty_initialization"
        )
        result = await state.run(contract)

        assert result["outcome"] == "published"
        head = git(state.remote, "rev-parse", "refs/heads/main")
        assert result["candidate"]["baseSha"] is None
        assert _tree(state.remote, head) == {
            "src/app.py": "print('saved')",
            "notes/new.md": "added by the run",
        }
        # Resubmitting the same decision reconciles its own published candidate.
        again = await state.run(contract)
        assert (again["outcome"], again["push"]["status"]) == (
            "published",
            "reconciled",
        )
        # Now non-empty: a different decision is refused rather than overwriting.
        changed = {
            **contract,
            "commit": {**contract["commit"], "message": "A different commit"},
        }
        with pytest.raises(ApplicationError) as exc:
            await state.run(changed)
        assert (await _persisted_result(state))["reasonCode"] == (
            "PUBLICATION_DESTINATION_NOT_EMPTY"
        )
        assert exc.value.type == "PUBLICATION_RECONCILIATION_BLOCKED"
        assert git(state.remote, "rev-parse", "refs/heads/main") == head
        assert len(state.pushes()) == 1


@pytest.mark.asyncio
async def test_no_change_publishes_nothing(tmp_path, monkeypatch):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        first = await state.run(
            state.contract(
                objective="branch", baseBranch="main", strategy="additive_import"
            )
        )
        # Publishing the same content onto the published branch changes nothing.
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        pushes = len(state.pushes())
        second = await state.run(
            state.contract(
                objective="branch",
                baseBranch="saved/work",
                headBranch="saved/again",
                strategy="additive_import",
            )
        )

        assert first["outcome"] == "published"
        assert second["outcome"] == "no_change"
        assert second["candidate"]["headSha"] == head
        assert len(state.pushes()) == pushes
        assert "refs/heads/saved/again" not in git(state.remote, "for-each-ref")


@pytest.mark.asyncio
async def test_lost_push_acknowledgment_reconciles_without_a_second_push(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:

        async def lose_first_ack(attempt: int) -> None:
            if attempt == 1:
                raise _WorkerLost()

        state.after["publication_recovery.saved_work_push"] = lose_first_ack
        result = await state.run(
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            )
        )

        assert result["outcome"] == "published"
        assert result["push"]["status"] == "reconciled"
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1


@pytest.mark.asyncio
async def test_push_success_then_pr_failure_retries_only_the_pr(tmp_path, monkeypatch):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        state.provider.unavailable_reads = 2
        result = await state.run(
            state.contract(
                objective="draft_pr", baseBranch="main", strategy="additive_import"
            )
        )

        assert result["outcome"] == "published"
        assert state.calls.count("publication_recovery.saved_work_push") == 1
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        assert state.provider.creates[0]["draft"] is True


@pytest.mark.asyncio
async def test_rate_limited_pr_create_waits_for_the_provider_delay(
    tmp_path, monkeypatch
):
    from datetime import timedelta

    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        state.provider.rate_limited_creates = 1
        result = await state.run(
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            )
        )

        assert result["outcome"] == "published"
        assert result["pullRequest"]["status"] == "created"
        # The retry waits for GitHub's cooldown, not the shorter 2 s backoff.
        assert state.sleeps == [timedelta(seconds=61)]
        assert len(state.provider.creates) == 2
        assert len(state.provider.pull_requests) == 1
        assert len(state.pushes()) == 1


@pytest.mark.asyncio
async def test_a_transient_authority_outage_is_retried_before_publication(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        # The destination's secret reference cannot be read once.
        state.authority_outages = 1
        result = await state.run(
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            )
        )

        assert result["outcome"] == "published"
        assert state.attempts["publication_recovery.saved_work_prepare"] == 2
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_verified_existing_pr_is_adopted_without_create_or_metadata_write(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        prepared = await state.runtime.publication_recovery_saved_work_prepare(
            {
                "contract": contract,
                "destinationWorkflowId": "mm:source:saved-work-publication:x",
                "destinationRunId": state.run_id,
            }
        )
        # Another attempt already pushed the candidate and opened its PR.
        await state.runtime.publication_recovery_saved_work_push(
            {"contract": contract, "prepared": prepared}
        )
        state.provider.pull_requests.append(
            {
                "head": "saved/work",
                "base": "main",
                "state": "open",
                "sha": prepared["candidate"]["headSha"],
                "url": "https://github.com/dest-owner/dest-repo/pull/7",
            }
        )
        result = await state.run(contract)

        assert result["outcome"] == "published"
        assert result["push"]["status"] == "reconciled"
        assert result["pullRequest"] == {
            "status": "adopted",
            "url": "https://github.com/dest-owner/dest-repo/pull/7",
            "headSha": prepared["candidate"]["headSha"],
            "summary": "",
        }
        assert state.provider.creates == []
        assert len(state.pushes()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["closed", "merged"])
async def test_closed_or_merged_pr_is_not_recreated(tmp_path, monkeypatch, state_name):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        state.provider.pull_requests.append(
            {
                "head": "saved/work",
                "base": "main",
                "state": state_name,
                "sha": "f" * 40,
                "url": "https://github.com/dest-owner/dest-repo/pull/3",
            }
        )

        with pytest.raises(ApplicationError) as exc:
            await state.run(contract)

        assert exc.value.type == "PUBLICATION_RECONCILIATION_BLOCKED"
        assert state.provider.creates == []
        persisted = await _persisted_result(state)
        assert persisted["outcome"] == "conflict"
        assert persisted["push"]["status"] == "pushed"
        assert persisted["pullRequest"]["status"] == state_name


@pytest.mark.asyncio
async def test_competing_head_branch_is_a_conflict_not_an_overwrite(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        competing = _write_destination(
            tmp_path, state.remote, {"theirs.txt": "another actor\n"}
        )
        git(state.remote, "update-ref", "refs/heads/saved/work", competing)
        before = state.saved_bytes()

        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    objective="pr", baseBranch="main", strategy="additive_import"
                )
            )

        assert exc.value.type == "PUBLICATION_RECONCILIATION_BLOCKED"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == competing
        assert state.pushes() == [] and state.provider.creates == []
        persisted = await _persisted_result(state)
        assert (persisted["outcome"], persisted["push"]["reasonCode"]) == (
            "conflict",
            "remote_head_changed",
        )
        assert state.saved_objects_unchanged(before)
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_stale_admitted_base_is_rejected_before_any_effect(tmp_path, monkeypatch):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        stale = git(state.remote, "rev-parse", "refs/heads/main")
        _write_destination(tmp_path, state.remote, {"newer.txt": "moved\n"})

        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    objective="pr",
                    baseBranch="main",
                    strategy="additive_import",
                    expectedBaseSha=stale,
                )
            )

        assert exc.value.type == "PUBLICATION_RECONCILIATION_BLOCKED"
        assert (await _persisted_result(state))[
            "reasonCode"
        ] == "PUBLICATION_STALE_EXPECTATION"
        assert state.pushes() == []


@pytest.mark.asyncio
async def test_unauthorized_owner_cannot_publish_saved_work(tmp_path, monkeypatch):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    principal="workflow:mm:unrelated",
                    objective="pr",
                    baseBranch="main",
                    strategy="additive_import",
                )
            )

        assert exc.value.type == "PUBLICATION_SAVED_WORK_UNAUTHORIZED"
        assert state.git_commands == []


@pytest.mark.asyncio
async def test_changed_destination_authority_invalidates_the_persisted_decision(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:

        async def switch_connection(attempt: int) -> None:
            state.authority_source = "OTHER_CONNECTION_SECRET_REF"

        state.hooks["publication_recovery.saved_work_push"] = switch_connection

        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    objective="pr", baseBranch="main", strategy="additive_import"
                )
            )

        assert exc.value.type == "PUBLICATION_AUTHORITY_CHANGED"
        assert state.pushes() == []


def _cancelled_activity() -> BaseException:
    """Temporal's delivery of a cancelled in-flight Activity."""
    try:
        raise TemporalCancelledError("cancelled")
    except TemporalCancelledError as cause:
        error = ActivityError(
            "activity cancelled",
            scheduled_event_id=1,
            started_event_id=2,
            identity="journey",
            activity_type="publication_recovery.saved_work_pull_request",
            activity_id="1",
            retry_state=RetryState.CANCEL_REQUESTED,
        )
        error.__cause__ = cause
        return error


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", [asyncio.CancelledError, _cancelled_activity])
async def test_cancellation_keeps_the_confirmed_push_and_releases_only_owned_claims(
    tmp_path, monkeypatch, delivery
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:

        async def cancel(attempt: int) -> None:
            raise delivery()

        state.hooks["publication_recovery.saved_work_pull_request"] = cancel

        with pytest.raises(BaseException) as raised:
            await state.run(
                state.contract(
                    objective="pr", baseBranch="main", strategy="additive_import"
                )
            )

        assert is_cancelled_exception(raised.value)

        persisted = await _persisted_result(state)
        assert persisted["outcome"] == "cancelled"
        assert persisted["push"]["status"] == "pushed"
        assert (
            git(state.remote, "rev-parse", "refs/heads/saved/work")
            == persisted["push"]["remoteHeadSha"]
        )
        # The cancelled PR effect is recorded as unconfirmed, never omitted.
        assert persisted["pullRequest"] == {
            "status": "unconfirmed",
            "reasonCode": "publication_cancelled",
        }
        assert state.calls[-1] == "publication_recovery.cleanup"
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_resubmitted_request_after_pr_exhaustion_reuses_the_pushed_candidate(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        # The PR outage outlasts every retry of the first run.
        state.provider.unavailable_reads = 5
        with pytest.raises(ApplicationError):
            await state.run(contract)
        first = await _persisted_result(state)
        assert (first["outcome"], first["push"]["status"]) == ("unavailable", "pushed")
        assert first["pullRequest"] == {
            "status": "unconfirmed",
            "reasonCode": "PUBLICATION_PULL_REQUEST_UNAVAILABLE",
        }
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        _write_destination(tmp_path, state.remote, {"later.txt": "base advanced\n"})

        # A plain resubmission of the same request completes only the PR.
        result = await state.run(contract)

        assert result["outcome"] == "published"
        assert result["admission"] == first["admission"]
        assert result["candidate"] == first["candidate"]
        assert result["push"]["status"] == "reconciled"
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_a_changed_decision_is_not_reused_and_never_overwrites_the_pushed_candidate(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        state.provider.unavailable_reads = 5
        with pytest.raises(ApplicationError):
            await state.run(contract)
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        _write_destination(tmp_path, state.remote, {"later.txt": "base advanced\n"})
        # The same operation with a different commit is a new decision.
        changed = {
            **contract,
            "commit": {**contract["commit"], "message": "A different commit"},
        }

        with pytest.raises(ApplicationError) as exc:
            await state.run(changed)

        assert exc.value.type == "PUBLICATION_RECONCILIATION_BLOCKED"
        second = await _persisted_result(state)
        assert (second["outcome"], second["push"]["reasonCode"]) == (
            "conflict",
            "remote_head_changed",
        )
        assert second["candidate"]["headSha"] != head
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert state.provider.creates == []


@pytest.mark.asyncio
async def test_resubmission_after_a_lost_terminal_record_reuses_the_pushed_candidate(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        before = state.saved_bytes()
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        # The push lands, the PR outage outlasts every retry, and the first
        # run cannot write its terminal record either.
        state.provider.unavailable_reads = 5

        async def artifact_store_down(attempt: int) -> None:
            if state.runs == 1:
                raise ApplicationError("artifact store unavailable")

        state.hooks["publication_recovery.persist_result"] = artifact_store_down
        with pytest.raises(ActivityError):
            await state.run(contract)
        assert await _persisted_results(state) == []
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        _write_destination(tmp_path, state.remote, {"later.txt": "base advanced\n"})

        # A plain resubmission of the same request completes only the PR.
        result = await state.run(contract)

        assert result["outcome"] == "published"
        assert result["push"]["status"] == "reconciled"
        assert result["candidate"]["headSha"] == head
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1
        assert state.saved_objects_unchanged(before)
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_an_intervening_changed_request_does_not_hide_the_original_decision(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        state.provider.unavailable_reads = 5
        with pytest.raises(ApplicationError):
            await state.run(contract)
        first = await _persisted_result(state)
        head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        _write_destination(tmp_path, state.remote, {"later.txt": "base advanced\n"})
        # The same operation with a different commit is a new decision that
        # conflicts with the pushed candidate.
        changed = {
            **contract,
            "commit": {**contract["commit"], "message": "A different commit"},
        }
        with pytest.raises(ApplicationError):
            await state.run(changed)

        # Resubmitting the original request still completes its own decision.
        result = await state.run(contract)

        assert result["outcome"] == "published"
        assert result["admission"] == first["admission"]
        assert result["candidate"] == first["candidate"]
        assert result["push"]["status"] == "reconciled"
        assert result["pullRequest"]["status"] == "created"
        assert git(state.remote, "rev-parse", "refs/heads/saved/work") == head
        assert len(state.pushes()) == 1
        assert len(state.provider.creates) == 1


@pytest.mark.asyncio
async def test_a_timed_out_pull_request_is_recorded_with_a_readable_reason(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:

        async def never_reports(attempt: int) -> None:
            raise _WorkerLost()

        state.hooks["publication_recovery.saved_work_pull_request"] = never_reports

        with pytest.raises(ApplicationError) as exc:
            await state.run(
                state.contract(
                    objective="pr", baseBranch="main", strategy="additive_import"
                )
            )

        reason = "publication_activity_timeout:start_to_close"
        assert exc.value.type == reason
        persisted = await _persisted_result(state)
        assert (persisted["outcome"], persisted["reasonCode"]) == (
            "unavailable",
            reason,
        )
        assert persisted["push"]["status"] == "pushed"
        assert persisted["pullRequest"] == {"status": "unconfirmed", "reasonCode": reason}
        assert state.provider.creates == []


async def _publication_closure(state: Journey) -> set[str]:
    """Artifact ids of every saved object publication reads."""
    import json

    from moonmind.publish.saved_work_source import saved_work_artifact_id

    manifest_id = saved_work_artifact_id(state.saved.saved_work_ref)
    _meta, payload = await state.service.read(
        artifact_id=manifest_id, principal=OPERATOR
    )
    manifest = json.loads(payload)
    (snapshot,) = [
        output["ref"]
        for output in manifest["outputs"]
        if output["format"] == "full_snapshot"
    ]
    return {
        manifest_id,
        saved_work_artifact_id(snapshot),
        saved_work_artifact_id(manifest["git"]["deltaRef"]),
    }


@pytest.mark.asyncio
async def test_retention_sweep_between_prepare_and_push_keeps_the_claimed_closure(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from api_service.db import models as db_models

    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        closure = await _publication_closure(state)
        claimed: list[set[str]] = []
        sweeps: list[Any] = []

        async def expire_everything_and_sweep(attempt: int) -> None:
            claimed.append({claim.artifact_id for claim in await state.use_claims()})
            session = state.service._repository._session
            rows = await session.execute(select(db_models.TemporalArtifact))
            for row in rows.scalars().all():
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
            sweeps.append(
                await state.service.sweep_lifecycle(principal="service:lifecycle")
            )

        state.hooks["publication_recovery.saved_work_push"] = (
            expire_everything_and_sweep
        )
        result = await state.run(
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            )
        )

        assert result["outcome"] == "published"
        assert claimed == [closure]
        assert sweeps[0].skipped_in_use_count == len(closure)
        for artifact_id in closure:
            row = await state.service._repository.get_artifact(artifact_id)
            assert row.status is db_models.TemporalArtifactStatus.COMPLETE
        assert await state.use_claims() == []


def _pause(
    state: Journey, name: str, *, after: bool
) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold one Activity in flight so the workflow can be cancelled meanwhile."""
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(attempt: int) -> None:
        started.set()
        await release.wait()

    (state.after if after else state.hooks)[name] = hold
    return started, release


async def _cancel_while_held(
    state: Journey,
    contract: dict[str, Any],
    started: asyncio.Event,
    release: asyncio.Event,
) -> BaseException:
    running = asyncio.ensure_future(state.run(contract))
    await asyncio.wait_for(started.wait(), timeout=60)
    running.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(BaseException) as raised:
        await running
    return raised.value


@pytest.mark.asyncio
async def test_cancellation_during_push_waits_for_and_records_the_landed_push(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        # The push lands; the cancellation arrives before it is acknowledged.
        started, release = _pause(
            state, "publication_recovery.saved_work_push", after=True
        )
        raised = await _cancel_while_held(
            state,
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            ),
            started,
            release,
        )

        assert is_cancelled_exception(raised)
        persisted = await _persisted_result(state)
        assert persisted["outcome"] == "cancelled"
        assert persisted["push"]["status"] == "pushed"
        assert persisted["push"]["remoteHeadSha"] == git(
            state.remote, "rev-parse", "refs/heads/saved/work"
        )
        assert "publication_recovery.saved_work_pull_request" not in state.calls
        assert state.calls[-2:] == [
            "publication_recovery.persist_result",
            "publication_recovery.cleanup",
        ]
        assert await state.use_claims() == []


@pytest.mark.asyncio
async def test_cancellation_during_result_persistence_keeps_the_record(
    tmp_path, monkeypatch
):
    async with journey(
        tmp_path, monkeypatch, destination_files={"README.md": "x\n"}
    ) as state:
        started, release = _pause(
            state, "publication_recovery.persist_result", after=False
        )
        raised = await _cancel_while_held(
            state,
            state.contract(
                objective="pr", baseBranch="main", strategy="additive_import"
            ),
            started,
            release,
        )

        assert is_cancelled_exception(raised)
        persisted = await _persisted_result(state)
        # Cancellation cannot undo or hide the confirmed push and PR.
        assert persisted["outcome"] == "published"
        assert persisted["pullRequest"]["status"] == "created"
        assert state.calls[-1] == "publication_recovery.cleanup"
        assert await state.use_claims() == []


async def _persisted_results(state: Journey) -> list[dict[str, Any]]:
    """Terminal records of the latest run."""
    import json

    records = []
    for artifact in await state.service.list_for_execution(
        namespace=state.service._default_namespace,
        workflow_id="mm:source:saved-work-publication:x",
        run_id=state.run_id,
        principal="workflow:mm:source:saved-work-publication:x",
        link_type="result",
    ):
        if (artifact.metadata_json or {}).get(
            "name"
        ) == "saved-work-publication-result.json":
            _meta, payload = await state.service.read(
                artifact_id=artifact.artifact_id,
                principal="workflow:mm:source:saved-work-publication:x",
            )
            records.append(json.loads(payload))
    return records


async def _persisted_result(state: Journey) -> dict[str, Any]:
    (record,) = await _persisted_results(state)
    return record
