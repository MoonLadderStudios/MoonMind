from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-dependabot-resolver"
            / "bin"
            / "batch_dependabot_resolver.py"
        )
    )


def _dependabot_pr(**overrides: Any) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "number": 42,
        "title": "Bump anthropic from 0.105.2 to 0.107.1",
        "author": {"login": "dependabot[bot]"},
        "headRefName": "dependabot/pip/anthropic-0.107.1",
        "headRefOid": "abc123def456",
        "headRepository": {"name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
        "isCrossRepository": False,
        "labels": [],
    }
    pr.update(overrides)
    return pr


def _args(**overrides: Any) -> Any:
    base = {
        "repo": "MoonLadderStudios/MoonMind",
        "state": "open",
        "merge_method": "squash",
        "max_iterations": 5,
        "max_attempts": 3,
        "priority": 0,
        "package_managers": [],
        "title_regex": (
            r"^(?:Bump|[Cc]hore\(deps\): bump) .+ from \S+ to \S+(?: in /.+)?$"
        ),
        "include_security_updates": True,
        "max_prs": None,
        "dry_run": False,
        "runtime_mode": None,
        "runtime_model": None,
        "runtime_effort": None,
        "runtime_provider_profile": None,
        "task_context_path": None,
        "artifacts_dir": "artifacts",
    }
    base.update(overrides)
    return type("Args", (), base)()


# ---------------------------------------------------------------------------
# Author matching (FR-002, SCN-001, SCN-003)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "author",
    [
        {"login": "dependabot[bot]"},
        {"login": "app/dependabot"},
        {"login": "DEPENDABOT[BOT]"},
        {"login": "", "name": "dependabot[bot]"},
    ],
)
def test_is_dependabot_author_accepts_bot_spellings(author: dict[str, Any]) -> None:
    module = _load_module()
    assert module["_is_dependabot_author"]({"author": author}) is True


@pytest.mark.parametrize(
    "author",
    [
        {"login": "octocat"},
        {"login": "renovate[bot]"},
        {},
        None,
    ],
)
def test_is_dependabot_author_rejects_others(author: Any) -> None:
    module = _load_module()
    assert module["_is_dependabot_author"]({"author": author}) is False


# ---------------------------------------------------------------------------
# Branch + title matching (FR-002, SCN-004)
# ---------------------------------------------------------------------------


def test_is_dependabot_branch() -> None:
    module = _load_module()
    assert module["_is_dependabot_branch"]("dependabot/pip/anthropic-0.107.1") is True
    assert module["_is_dependabot_branch"]("feature/manual") is False
    assert module["_is_dependabot_branch"]("") is False


def test_title_matches_default_regex() -> None:
    module = _load_module()
    pattern = module["DEFAULT_TITLE_REGEX"]
    assert module["_title_matches"]("Bump anthropic from 0.105.2 to 0.107.1", pattern)
    assert module["_title_matches"](
        "Bump eslint from 8.0.0 to 9.0.0 in /frontend", pattern
    )
    assert module["_title_matches"](
        "Chore(deps): bump actions/setup-node from 6 to 7", pattern
    )
    assert module["_title_matches"](
        "chore(deps): bump boto3 from 1.43.46 to 1.43.51", pattern
    )
    assert not module["_title_matches"]("Bump the pip group with 2 updates", pattern)
    assert not module["_title_matches"]("Refactor things", pattern)


def test_likely_version_bump_title_detects_filter_contract_drift() -> None:
    module = _load_module()
    assert module["_looks_like_version_bump_title"](
        "Deps: bump package from 1.0.0 to 2.0.0"
    )
    assert not module["_looks_like_version_bump_title"](
        "Bump the pip group with 2 updates"
    )


def test_infer_repo_from_remote_returns_none_when_git_remote_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()

    def fail_command(_cmd: list[str]) -> str:
        raise RuntimeError("command failed")

    monkeypatch.setitem(
        module["_infer_repo_from_remote"].__globals__, "_run_command", fail_command
    )

    assert module["_infer_repo_from_remote"]() is None


def test_title_matches_invalid_regex_raises() -> None:
    module = _load_module()
    with pytest.raises(RuntimeError):
        module["_title_matches"]("anything", "(")


# ---------------------------------------------------------------------------
# Fork / cross-repo (FR-003, SCN-002) — reuse of batch-pr-resolver behavior
# ---------------------------------------------------------------------------


def test_is_local_head_rejects_cross_repository() -> None:
    module = _load_module()
    pr = _dependabot_pr(isCrossRepository=True)
    assert module["_is_local_head"](pr, "MoonLadderStudios/MoonMind") is False


def test_is_local_head_accepts_same_owner() -> None:
    module = _load_module()
    assert module["_is_local_head"](_dependabot_pr(), "MoonLadderStudios/MoonMind") is True


# ---------------------------------------------------------------------------
# Package-manager allowlist (FR-008, SCN-007)
# ---------------------------------------------------------------------------


def test_branch_package_manager_extraction() -> None:
    module = _load_module()
    assert module["_branch_package_manager"]("dependabot/pip/anthropic-1") == "pip"
    assert (
        module["_branch_package_manager"]("dependabot/github_actions/actions/checkout-4")
        == "github_actions"
    )
    assert module["_branch_package_manager"]("feature/manual") is None


@pytest.mark.parametrize(
    "branch,allowlist,expected",
    [
        ("dependabot/pip/anthropic-1", ["pip"], True),
        ("dependabot/npm_and_yarn/eslint-9", ["npm"], True),
        ("dependabot/github_actions/checkout-4", ["github-actions"], True),
        ("dependabot/npm_and_yarn/eslint-9", ["pip"], False),
        ("dependabot/pip/anthropic-1", [], True),
    ],
)
def test_matches_package_managers(
    branch: str, allowlist: list[str], expected: bool
) -> None:
    module = _load_module()
    assert module["_matches_package_managers"](branch, allowlist) is expected


# ---------------------------------------------------------------------------
# Cross-run idempotency key (FR-006, SCN-005, SC-002, INV-2, INV-5)
# ---------------------------------------------------------------------------


def test_idempotency_key_readable_and_stable() -> None:
    module = _load_module()
    key = module["_idempotency_key"]
    k1 = key("MoonLadderStudios/MoonMind", 42, "abc123")
    k2 = key("MoonLadderStudios/MoonMind", 42, "abc123")
    assert k1 == "batch-dependabot-resolver:MoonLadderStudios/MoonMind:pr:42:head:abc123"
    assert k1 == k2  # deterministic across runs


def test_idempotency_key_changes_with_head_sha() -> None:
    module = _load_module()
    key = module["_idempotency_key"]
    assert key("o/r", 42, "sha-one") != key("o/r", 42, "sha-two")


def test_idempotency_key_missing_head_sha_returns_none() -> None:
    module = _load_module()
    assert module["_idempotency_key"]("o/r", 42, None) is None
    assert module["_idempotency_key"]("o/r", 42, "  ") is None


def test_idempotency_key_falls_back_to_hash_when_too_long() -> None:
    module = _load_module()
    key = module["_idempotency_key"]
    long_repo = "owner/" + ("r" * 200)
    result = key(long_repo, 42, "deadbeef")
    assert result is not None
    assert len(result) <= module["IDEMPOTENCY_KEY_MAX_LENGTH"]
    assert result.startswith("batch-dependabot-resolver:pr:42:sha256:")


# ---------------------------------------------------------------------------
# Child pr-resolver payload (FR-005, INV-4)
# ---------------------------------------------------------------------------


def test_build_queue_request_child_payload_shape() -> None:
    module = _load_module()
    request = module["_build_queue_request"](
        "MoonLadderStudios/MoonMind",
        42,
        "dependabot/pip/anthropic-0.107.1",
        head_sha="abc123",
        runtime=module["RuntimeSelection"](mode="codex", model="gpt-5-codex", effort="high"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )
    payload = request["payload"]
    task = payload["task"]
    assert task["skill"] == {"name": "pr-resolver"}
    assert "id" not in task["skill"] and "args" not in task["skill"]
    assert "version" not in task["skill"]
    assert payload["requiredCapabilities"] == ["gh"]
    assert task["inputs"] == {
        "repo": "MoonLadderStudios/MoonMind",
        "pr": "42",
        "branch": "dependabot/pip/anthropic-0.107.1",
        "mergeMethod": "squash",
        "maxIterations": 3,
    }
    assert task["git"]["startingBranch"] == "dependabot/pip/anthropic-0.107.1"
    assert task["git"]["branch"] == "dependabot/pip/anthropic-0.107.1"
    assert "targetBranch" not in task["git"]
    assert task["publish"]["mode"] == "auto"
    assert task["runtime"]["mode"] == "codex"
    assert payload["targetRuntime"] == "codex"
    assert (
        payload["idempotencyKey"]
        == "batch-dependabot-resolver:MoonLadderStudios/MoonMind:pr:42:head:abc123"
    )


def test_build_queue_request_without_head_sha_omits_idempotency_key() -> None:
    module = _load_module()
    request = module["_build_queue_request"](
        "o/r",
        42,
        "dependabot/pip/x",
        head_sha=None,
        runtime=module["RuntimeSelection"](mode="codex"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )
    assert "idempotencyKey" not in request["payload"]


def test_build_queue_request_runtime_inheritance_opt_in() -> None:
    module = _load_module()
    request = module["_build_queue_request"](
        "o/r",
        42,
        "dependabot/pip/x",
        head_sha="abc",
        runtime=module["RuntimeSelection"](mode="codex"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
        inherit_runtime_from_caller=True,
    )
    assert request["payload"]["runtimeInheritance"] == "caller"


def test_submit_jobs_requires_and_verifies_workflow_id() -> None:
    module = _load_module()
    request = module["_build_queue_request"](
        "MoonLadderStudios/MoonMind",
        42,
        "dependabot/pip/x",
        head_sha="abc",
        runtime=module["RuntimeSelection"](mode="codex"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )
    submission = module["JobSubmission"](
        queue_request=request,
        pr_number=42,
        branch="dependabot/pip/x",
    )
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(
        return_value={
            "workflowId": "mm:dependabot-child",
            "runId": "run-1",
            "state": "initializing",
            "temporalStatus": "running",
        }
    )
    get_paths: list[str] = []

    class FakeAsyncClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            pass

        async def post(self, _path: str, **_kwargs: Any) -> Any:
            return fake_response

        async def get(self, path: str) -> Any:
            get_paths.append(path)
            return fake_response

    with patch.object(module["httpx"], "AsyncClient", FakeAsyncClient):
        created, errors = asyncio.run(
            module["_submit_jobs_via_http"](
                [submission],
                moonmind_url="http://api:8000",
                worker_token=None,
            )
        )

    assert errors == []
    assert created[0]["workflowId"] == "mm:dependabot-child"
    assert created[0]["runId"] == "run-1"
    assert get_paths == ["/api/executions/mm%3Adependabot-child"]


def test_verify_execution_visible_rejects_terminated_workflow() -> None:
    module = _load_module()
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(
        return_value={
            "workflowId": "mm:terminated",
            "state": "terminated",
        }
    )

    class FakeClient:
        async def get(self, _path: str) -> Any:
            return fake_response

    with pytest.raises(RuntimeError, match="terminal state terminated"):
        asyncio.run(
            module["_verify_execution_visible"](
                FakeClient(),
                "mm:terminated",
                attempts=1,
                delay_seconds=0,
            )
        )


# ---------------------------------------------------------------------------
# Match / skip partition incl. maxPrs + missing head SHA (FR-004, edge cases)
# ---------------------------------------------------------------------------


def _partition(module: dict[str, Any], prs: list[dict[str, Any]], **arg_overrides: Any):
    runtime = module["RuntimeSelection"](mode="codex")
    return module["_build_request_records"](
        "MoonLadderStudios/MoonMind", prs, _args(**arg_overrides), runtime
    )


def test_partition_matches_only_genuine_dependabot_prs() -> None:
    module = _load_module()
    prs = [
        _dependabot_pr(number=1),  # match
        _dependabot_pr(number=2, isCrossRepository=True),  # fork-pr
        _dependabot_pr(number=3, author={"login": "octocat"}),  # not-dependabot-author
        _dependabot_pr(number=4, headRefName="feature/manual"),  # non-dependabot-branch
        _dependabot_pr(number=5, title="Bump the pip group with 2 updates"),  # title-mismatch
        _dependabot_pr(number=6, headRefOid=""),  # missing-head-sha
    ]
    queue_requests, skipped, matched_count = _partition(module, prs)

    assert matched_count == 1
    assert [s.pr_number for s in queue_requests] == [1]
    reasons = {entry["pr"]: entry["reason"] for entry in skipped}
    assert reasons == {
        2: "fork-pr",
        3: "not-dependabot-author",
        4: "non-dependabot-branch",
        5: "title-mismatch",
        6: "missing-head-sha",
    }


def test_partition_applies_max_prs_cap() -> None:
    module = _load_module()
    prs = [_dependabot_pr(number=n, headRefOid=f"sha{n}") for n in (1, 2, 3)]
    queue_requests, skipped, matched_count = _partition(module, prs, max_prs=2)

    assert matched_count == 3  # pre-cap
    assert [s.pr_number for s in queue_requests] == [1, 2]
    assert [(s["pr"], s["reason"]) for s in skipped] == [(3, "max-prs-cap")]


def test_partition_package_manager_filter() -> None:
    module = _load_module()
    prs = [
        _dependabot_pr(number=1, headRefName="dependabot/pip/a", headRefOid="s1"),
        _dependabot_pr(
            number=2, headRefName="dependabot/npm_and_yarn/b", headRefOid="s2"
        ),
    ]
    queue_requests, skipped, _ = _partition(module, prs, package_managers=["pip"])
    assert [s.pr_number for s in queue_requests] == [1]
    assert (skipped[0]["pr"], skipped[0]["reason"]) == (2, "package-manager-filtered")


def test_partition_respects_intentionally_narrow_title_override() -> None:
    module = _load_module()
    prs = [
        _dependabot_pr(
            number=1,
            title="Deps: bump anthropic from 1.0.0 to 2.0.0",
            headRefOid="s1",
        )
    ]
    queue_requests, skipped, _ = _partition(
        module,
        prs,
        title_regex=r"^Bump .+ from \S+ to \S+$",
    )
    assert queue_requests == []
    assert skipped == [
        {
            "pr": 1,
            "branch": "dependabot/pip/anthropic-0.107.1",
            "reason": "title-mismatch",
        }
    ]


def test_partition_security_update_excluded_when_disabled() -> None:
    module = _load_module()
    prs = [
        _dependabot_pr(number=1, labels=[{"name": "security"}], headRefOid="s1"),
    ]
    queue_requests, skipped, _ = _partition(
        module, prs, include_security_updates=False
    )
    assert queue_requests == []
    assert (skipped[0]["pr"], skipped[0]["reason"]) == (1, "security-update-excluded")
    # included by default
    qr2, sk2, _ = _partition(module, prs, include_security_updates=True)
    assert [s.pr_number for s in qr2] == [1]


# ---------------------------------------------------------------------------
# Dry-run + artifacts (FR-007, FR-010, SC-003, SC-004)
# ---------------------------------------------------------------------------


def test_would_queue_records_carry_idempotency_keys() -> None:
    module = _load_module()
    prs = [_dependabot_pr(number=7, headRefOid="sha7")]
    queue_requests, _, _ = _partition(module, prs)
    records = module["_would_queue_records"](queue_requests)
    assert records[0]["pr"] == 7
    assert records[0]["idempotencyKey"].startswith("batch-dependabot-resolver:")


def test_write_artifacts_uses_unique_temporary_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_module()
    entropy = iter([b"first1", b"second"])
    temporary_names: list[str] = []
    original_replace = module["os"].replace

    monkeypatch.setattr(module["os"], "urandom", lambda _size: next(entropy))

    def capture_replace(source: str | Path, destination: str | Path) -> None:
        temporary_names.append(Path(source).name)
        original_replace(source, destination)

    monkeypatch.setattr(module["os"], "replace", capture_replace)
    result_path = tmp_path / "result.json"

    module["_write_artifacts"](result_path, {"attempt": 1})
    module["_write_artifacts"](result_path, {"attempt": 2})

    assert temporary_names == [
        ".result.json.666972737431.tmp",
        ".result.json.7365636f6e64.tmp",
    ]
    assert json.loads(result_path.read_text(encoding="utf-8")) == {"attempt": 2}
    assert list(tmp_path.glob(".result.json.*.tmp")) == []


def test_write_run_artifacts_emits_no_op_on_zero_match(tmp_path: Path) -> None:
    module = _load_module()
    payload = {
        "dryRun": False,
        "requested": 3,
        "created": 0,
        "queued": [],
        "wouldQueue": [],
        "skipped": [{"pr": 1, "branch": "feature/x", "reason": "not-dependabot-author"}],
        "errors": [],
    }
    module["_write_run_artifacts"](tmp_path, payload)
    assert (tmp_path / "batch_dependabot_resolver_result.json").exists()
    outcome = json.loads((tmp_path / "skill_outcome.json").read_text())
    assert outcome["status"] == "no_op"
    assert outcome["reason"] == "no_dependabot_prs_matched"


def test_write_run_artifacts_skips_no_op_on_dry_run(tmp_path: Path) -> None:
    module = _load_module()
    payload = {
        "dryRun": True,
        "requested": 1,
        "created": 0,
        "queued": [],
        "wouldQueue": [{"pr": 1, "branch": "dependabot/pip/x", "idempotencyKey": "k"}],
        "skipped": [],
        "errors": [],
    }
    module["_write_run_artifacts"](tmp_path, payload)
    assert (tmp_path / "batch_dependabot_resolver_result.json").exists()
    assert not (tmp_path / "skill_outcome.json").exists()


def test_write_run_artifacts_skips_no_op_on_errors(tmp_path: Path) -> None:
    module = _load_module()
    payload = {
        "dryRun": False,
        "requested": 1,
        "created": 0,
        "queued": [],
        "wouldQueue": [],
        "skipped": [],
        "errors": [{"pr": 9, "branch": "dependabot/pip/x", "error": "boom"}],
    }
    module["_write_run_artifacts"](tmp_path, payload)
    outcome = json.loads((tmp_path / "skill_outcome.json").read_text())
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "child_workflow_queue_failed"


def test_write_run_artifacts_fails_on_default_title_contract_drift(
    tmp_path: Path,
) -> None:
    module = _load_module()
    payload = {
        "dryRun": False,
        "requested": 1,
        "created": 0,
        "queued": [],
        "wouldQueue": [],
        "skipped": [
            {
                "pr": 9,
                "branch": "dependabot/pip/x",
                "reason": "title-mismatch",
                "likelyVersionBump": True,
            }
        ],
        "errors": [],
        "diagnostics": {"titleContractDriftPrs": [9]},
    }
    module["_write_run_artifacts"](tmp_path, payload)
    outcome = json.loads((tmp_path / "skill_outcome.json").read_text())
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "dependabot_title_contract_drift"
    assert outcome["evidence"]["titleContractDriftPrs"] == [9]


# ---------------------------------------------------------------------------
# Runtime selection inheritance (FR-009)
# ---------------------------------------------------------------------------


def test_resolve_runtime_selection_uses_inherited_values(tmp_path: Path) -> None:
    module = _load_module()
    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        json.dumps(
            {
                "runtimeConfig": {
                    "mode": "claude",
                    "model": "claude-opus-4-8",
                    "effort": "high",
                    "providerProfile": "inherited-profile",
                }
            }
        ),
        encoding="utf-8",
    )
    runtime = module["_resolve_runtime_selection"](
        _args(task_context_path=str(task_context))
    )
    assert runtime.mode == "claude"
    assert runtime.model == "claude-opus-4-8"
    assert runtime.effort == "high"
    assert runtime.provider_profile == "inherited-profile"


def test_resolve_runtime_selection_prefers_explicit(tmp_path: Path) -> None:
    module = _load_module()
    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        json.dumps({"runtimeConfig": {"mode": "codex"}}), encoding="utf-8"
    )
    runtime = module["_resolve_runtime_selection"](
        _args(task_context_path=str(task_context), runtime_mode="gemini")
    )
    assert runtime.mode == "gemini"


# ---------------------------------------------------------------------------
# CLI arg flattening
# ---------------------------------------------------------------------------


def test_parse_args_flattens_package_managers() -> None:
    module = _load_module()
    args = module["_parse_args"](
        ["--repo", "o/r", "--package-managers", "pip,npm", "--package-managers", "github-actions"]
    )
    assert args.package_managers == ["pip", "npm", "github-actions"]
    assert args.max_iterations == 5
    assert args.include_security_updates is True
    assert args.dry_run is False


def test_parse_args_no_include_security_updates() -> None:
    module = _load_module()
    args = module["_parse_args"](["--repo", "o/r", "--no-include-security-updates"])
    assert args.include_security_updates is False
