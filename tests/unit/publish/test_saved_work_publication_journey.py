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
from temporalio.exceptions import RetryState, is_cancelled_exception

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
from moonmind.workflows.temporal.runtime import checkpoint_restore
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
        return PullRequestReconciliation(state="absent")

    async def create(self, **kwargs: Any) -> CreatePRResult:
        self.tokens.append(kwargs["github_token"])
        self.creates.append(kwargs)
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
    hooks: dict[str, Callable[[int], Awaitable[None]]] = field(default_factory=dict)
    after: dict[str, Callable[[int], Awaitable[None]]] = field(default_factory=dict)

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
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "source-run",
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
    tmp_path: Path, monkeypatch, *, destination_files=None, seed_baseline=False
):
    async with _artifact_service(tmp_path) as service:
        saved = await capture_saved_work(
            tmp_path, BASE_FILES, _mutate, artifact_service=service
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
            return ResolvedGitHubCredential(
                token=DESTINATION_TOKEN,
                source=GitHubCredentialSource.SECRET_REF_ENV,
                sourceName=state.authority_source,
                repo=repo,
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

        monkeypatch.setattr(github_credentials, "resolve_github_credential", resolve)
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

        async def execute_activity(name: str, payload: dict[str, Any], **_kwargs: Any):
            state.calls.append(name)
            for attempt in range(1, 6):
                try:
                    if name in state.hooks:
                        await state.hooks[name](attempt)
                    result = await handlers[name](payload)
                    if name in state.after:
                        await state.after[name](attempt)
                    return result
                except ApplicationError as exc:
                    if exc.non_retryable or attempt == 5:
                        raise ActivityError(
                            "activity failed",
                            scheduled_event_id=1,
                            started_event_id=2,
                            identity="journey",
                            activity_type=name,
                            activity_id=str(attempt),
                            retry_state=RetryState.NON_RETRYABLE_FAILURE,
                        ) from exc
                except _WorkerLost:
                    continue
            raise AssertionError(f"{name} exhausted retries")

        monkeypatch.setattr(
            workflow_module.workflow, "execute_activity", execute_activity
        )
        monkeypatch.setattr(
            workflow_module.workflow,
            "info",
            lambda: SimpleNamespace(
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
        assert [(c.operation_kind, c.owner_principal) for c in claims_during_push] == [
            ("publication", OPERATOR)
        ]
        assert await state.use_claims() == []


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
        # Now non-empty: the same request is refused rather than overwriting.
        with pytest.raises(ApplicationError) as exc:
            await state.run(contract)
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
            {"contract": contract}
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
        assert "pullRequest" not in persisted
        assert state.calls[-1] == "publication_recovery.cleanup"
        assert await state.use_claims() == []


async def _persisted_result(state: Journey) -> dict[str, Any]:
    import json

    artifacts = await state.service.list_for_execution(
        namespace=state.service._default_namespace,
        workflow_id="mm:source:saved-work-publication:x",
        run_id=state.run_id,
        principal="workflow:mm:source:saved-work-publication:x",
        link_type="result",
    )
    (artifact,) = [
        a
        for a in artifacts
        if (a.metadata_json or {}).get("name") == "saved-work-publication-result.json"
    ]
    _meta, payload = await state.service.read(
        artifact_id=artifact.artifact_id,
        principal="workflow:mm:source:saved-work-publication:x",
    )
    return json.loads(payload)
