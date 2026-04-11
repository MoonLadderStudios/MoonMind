from __future__ import annotations

import asyncio
import runpy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-pr-resolver"
            / "bin"
            / "batch_pr_resolver.py"
        )
    )


def test_is_local_head_uses_cross_repository_flag():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": True,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is False


def test_is_local_head_accepts_same_repo_without_name_with_owner():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": False,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is True


def test_is_local_head_rejects_fork_owner_mismatch():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": False,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "another-org"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is False


def test_build_queue_request_sets_none_publish_with_matching_branches():
    module = _load_module()
    build_queue_request = module["_build_queue_request"]
    runtime_selection = module["RuntimeSelection"]

    request = build_queue_request(
        "MoonLadderStudios/MoonMind",
        pr_number=42,
        branch="feature/example",
        runtime=runtime_selection(mode="codex", model="gpt-5-codex", effort="high", provider_profile="test-profile"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )

    payload = request["payload"]
    task = payload["task"]
    git = task["git"]

    assert payload["targetRuntime"] == "codex"
    assert task["runtime"]["mode"] == "codex"
    assert task["runtime"]["model"] == "gpt-5-codex"
    assert task["runtime"]["effort"] == "high"
    assert task["runtime"]["providerProfile"] == "test-profile"
    assert task["title"] == "feature/example"
    assert task["publish"]["mode"] == "none"
    assert git["startingBranch"] == "feature/example"
    assert git["targetBranch"] == "feature/example"


def test_build_queue_request_adds_batch_scoped_idempotency_key() -> None:
    module = _load_module()
    build_queue_request = module["_build_queue_request"]
    runtime_selection = module["RuntimeSelection"]

    request = build_queue_request(
        "MoonLadderStudios/MoonMind",
        pr_number=42,
        branch="feature/example",
        runtime=runtime_selection(mode="codex", model=None, effort=None),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
        batch_scope="mm:parent-run",
    )

    assert request["payload"]["idempotencyKey"] == (
        "batch-pr-resolver:mm:parent-run:MoonLadderStudios/MoonMind:"
        "pr:42:branch:feature/example"
    )


def test_build_queue_request_omits_idempotency_without_batch_scope() -> None:
    module = _load_module()
    build_queue_request = module["_build_queue_request"]
    runtime_selection = module["RuntimeSelection"]

    request = build_queue_request(
        "MoonLadderStudios/MoonMind",
        pr_number=42,
        branch="feature/example",
        runtime=runtime_selection(mode="codex", model=None, effort=None),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )

    assert "idempotencyKey" not in request["payload"]


def test_build_queue_request_enqueues_without_manual_publish_patch() -> None:
    module = _load_module()
    build_queue_request = module["_build_queue_request"]
    runtime_selection = module["RuntimeSelection"]

    request = build_queue_request(
        "MoonLadderStudios/MoonMind",
        pr_number=77,
        branch="feature/enqueue-check",
        runtime=runtime_selection(mode="codex", model=None, effort=None),
        merge_method="squash",
        max_iterations=3,
        priority=1,
        max_attempts=4,
    )

    # Assert directly on the raw Temporal-contract payload. normalize_queue_job_payload
    # uses the legacy queue-worker contract (skill.id) and will fail on the new
    # skill.name shape.  Publish and skill identity assertions are covered by the
    # dedicated contract tests below.
    assert request["payload"]["task"]["publish"]["mode"] == "none"


def test_resolve_artifacts_dir_prefers_managed_session_spool(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    module = _load_module()
    resolve_artifacts_dir = module["_resolve_artifacts_dir"]

    spool = tmp_path / "mm-parent" / "artifacts"
    monkeypatch.setenv("MOONMIND_SESSION_ARTIFACT_SPOOL_PATH", str(spool))

    assert resolve_artifacts_dir("artifacts") == spool


def test_resolve_artifacts_dir_respects_explicit_path(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    module = _load_module()
    resolve_artifacts_dir = module["_resolve_artifacts_dir"]

    explicit = tmp_path / "custom-artifacts"
    monkeypatch.setenv(
        "MOONMIND_SESSION_ARTIFACT_SPOOL_PATH",
        str(tmp_path / "mm-parent" / "artifacts"),
    )

    assert resolve_artifacts_dir(str(explicit)) == explicit


def test_parent_run_scope_uses_managed_session_spool(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    module = _load_module()
    parent_run_scope = module["_parent_run_scope"]

    monkeypatch.setenv(
        "MOONMIND_SESSION_ARTIFACT_SPOOL_PATH",
        str(tmp_path / "mm:parent-run" / "artifacts"),
    )

    assert parent_run_scope() == "mm:parent-run"


def test_load_parent_repository_reads_task_context(tmp_path: Path):
    module = _load_module()
    load_parent_repository = module["_load_parent_repository"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        '{"repository":"MoonLadderStudios/Tactics"}',
        encoding="utf-8",
    )

    assert load_parent_repository(str(task_context)) == "MoonLadderStudios/Tactics"


def test_resolve_repo_prefers_task_context_over_env(monkeypatch, tmp_path: Path):
    module = _load_module()
    resolve_repo = module["_resolve_repo"]

    monkeypatch.setenv("WORKFLOW_GITHUB_REPOSITORY", "MoonLadderStudios/MoonMind")
    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        '{"repository":"MoonLadderStudios/Tactics"}',
        encoding="utf-8",
    )

    assert (
        resolve_repo(raw_repo=None, task_context_path=str(task_context))
        == "MoonLadderStudios/Tactics"
    )


def test_resolve_repo_prefers_remote_over_env(monkeypatch):
    module = _load_module()
    resolve_repo = module["_resolve_repo"]

    monkeypatch.setenv("WORKFLOW_GITHUB_REPOSITORY", "MoonLadderStudios/MoonMind")
    monkeypatch.setitem(
        resolve_repo.__globals__, "_load_parent_repository", lambda _path=None: None
    )
    monkeypatch.setitem(
        resolve_repo.__globals__,
        "_infer_repo_from_remote",
        lambda: "MoonLadderStudios/Tactics",
    )

    assert resolve_repo(raw_repo=None, task_context_path=None) == (
        "MoonLadderStudios/Tactics"
    )


def test_load_parent_runtime_selection_prefers_runtime_config(tmp_path: Path):
    module = _load_module()
    load_parent_runtime_selection = module["_load_parent_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        (
            "{"
            '"runtime":"codex",'
            '"runtimeConfig":{"mode":"gemini","model":"gemini-2.5-pro","effort":"medium","providerProfile":"inherited-profile"}'
            "}"
        ),
        encoding="utf-8",
    )

    runtime = load_parent_runtime_selection(str(task_context))
    assert runtime is not None
    assert runtime.mode == "gemini"
    assert runtime.model == "gemini-2.5-pro"
    assert runtime.effort == "medium"
    assert runtime.provider_profile == "inherited-profile"


def test_load_parent_runtime_selection_accepts_profile_id_fallback(tmp_path: Path):
    module = _load_module()
    load_parent_runtime_selection = module["_load_parent_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        (
            "{"
            '"runtime":"codex",'
            '"runtimeConfig":{"mode":"codex","model":"gpt-5-codex","effort":"high","profileId":"profile-from-id"}'
            "}"
        ),
        encoding="utf-8",
    )

    runtime = load_parent_runtime_selection(str(task_context))
    assert runtime is not None
    assert runtime.mode == "codex"
    assert runtime.model == "gpt-5-codex"
    assert runtime.effort == "high"
    assert runtime.provider_profile == "profile-from-id"


def test_resolve_runtime_selection_uses_inherited_values(tmp_path: Path):
    module = _load_module()
    resolve_runtime_selection = module["_resolve_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        (
            "{"
            '"runtimeConfig":{"mode":"claude","model":"claude-3.7-sonnet","effort":"low","providerProfile":"inherited-profile"}'
            "}"
        ),
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "task_context_path": str(task_context),
            "runtime_mode": None,
            "runtime_model": None,
            "runtime_effort": None,
            "runtime_provider_profile": None,
        },
    )()

    runtime = resolve_runtime_selection(args)
    assert runtime.mode == "claude"
    assert runtime.model == "claude-3.7-sonnet"
    assert runtime.effort == "low"
    assert runtime.provider_profile == "inherited-profile"


def test_resolve_runtime_selection_prefers_explicit_over_inherited(tmp_path: Path):
    module = _load_module()
    resolve_runtime_selection = module["_resolve_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        ('{"runtimeConfig":{"mode":"codex","model":"gpt-5-codex","effort":"low","providerProfile":"inherited-profile"}}'),
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "task_context_path": str(task_context),
            "runtime_mode": "gemini",
            "runtime_model": "gemini-2.5-pro",
            "runtime_effort": "high",
            "runtime_provider_profile": "explicit-profile",
        },
    )()

    runtime = resolve_runtime_selection(args)
    assert runtime.mode == "gemini"
    assert runtime.model == "gemini-2.5-pro"
    assert runtime.effort == "high"
    assert runtime.provider_profile == "explicit-profile"


def test_resolve_runtime_selection_defaults_to_none_without_inheritance(monkeypatch: Any):
    module = _load_module()
    resolve_runtime_selection = module["_resolve_runtime_selection"]
    
    monkeypatch.delenv("MOONMIND_DEFAULT_TASK_RUNTIME", raising=False)

    args = type(
        "Args",
        (),
        {
            "task_context_path": None,
            "runtime_mode": None,
            "runtime_model": None,
            "runtime_effort": None,
            "runtime_provider_profile": None,
        },
    )()

    runtime = resolve_runtime_selection(args)
    assert runtime.mode is None
    assert runtime.model is None
    assert runtime.effort is None
    assert runtime.provider_profile is None


def test_resolve_runtime_selection_uses_default_runtime_env(monkeypatch: Any):
    module = _load_module()
    resolve_runtime_selection = module["_resolve_runtime_selection"]

    monkeypatch.setenv("MOONMIND_DEFAULT_TASK_RUNTIME", "claude")

    args = type(
        "Args",
        (),
        {
            "task_context_path": None,
            "runtime_mode": None,
            "runtime_model": None,
            "runtime_effort": None,
            "runtime_provider_profile": None,
        },
    )()

    runtime = resolve_runtime_selection(args)
    assert runtime.mode == "claude"
    assert runtime.model is None
    assert runtime.effort is None
    assert runtime.provider_profile is None


# ---------------------------------------------------------------------------
# HTTP submission path tests
# ---------------------------------------------------------------------------


def _make_submission(module: dict[str, Any]) -> Any:
    """Build a minimal JobSubmission for testing."""
    _JobSubmission = module["JobSubmission"]
    _RuntimeSelection = module["RuntimeSelection"]
    build = module["_build_queue_request"]
    req = build(
        "MoonLadderStudios/MoonMind",
        pr_number=42,
        branch="feature/test",
        runtime=_RuntimeSelection(mode="codex", model=None, effort=None),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )
    return _JobSubmission(queue_request=req, pr_number=42, branch="feature/test")


def test_submit_jobs_posts_to_api(monkeypatch: Any) -> None:
    """When MOONMIND_URL is set, _submit_jobs should POST to /api/executions."""
    module = _load_module()
    submit_jobs_via_http = module["_submit_jobs_via_http"]
    _read_worker_token = module["_read_worker_token"]

    monkeypatch.setenv("MOONMIND_WORKER_TOKEN", "test-token-abc")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(return_value={"taskId": "mm:uuid-1234", "status": "queued"})

    mock_post = AsyncMock(return_value=fake_response)

    import httpx

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, path: str, **kwargs: Any) -> Any:
            return await mock_post(path, **kwargs)

    with patch.object(httpx, "AsyncClient", FakeAsyncClient):
        submission = _make_submission(module)
        created, errors = asyncio.run(
            submit_jobs_via_http(
                [submission],
                moonmind_url="http://api:5000",
                worker_token="test-token-abc",
            )
        )

    assert errors == []
    assert len(created) == 1
    assert created[0]["jobId"] == "mm:uuid-1234"
    assert created[0]["pr"] == 42
    mock_post.assert_awaited_once()
    call_path = mock_post.await_args[0][0]
    assert call_path == "/api/executions"


def test_submit_jobs_records_temporal_workflow_id(monkeypatch: Any) -> None:
    """The Temporal executions API returns workflowId rather than legacy taskId."""
    module = _load_module()
    submit_jobs_via_http = module["_submit_jobs_via_http"]

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json = MagicMock(
        return_value={"workflowId": "mm:wf-123", "status": "queued"}
    )

    mock_post = AsyncMock(return_value=fake_response)

    import httpx

    class FakeAsyncClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def post(self, path: str, **kwargs: Any) -> Any:
            return await mock_post(path, **kwargs)

    with patch.object(httpx, "AsyncClient", FakeAsyncClient):
        submission = _make_submission(module)
        created, errors = asyncio.run(
            submit_jobs_via_http(
                [submission],
                moonmind_url="http://api:5000",
                worker_token=None,
            )
        )

    assert errors == []
    assert created[0]["jobId"] == "mm:wf-123"


def test_submit_jobs_uses_http_when_moonmind_url_set(monkeypatch: Any) -> None:
    """_submit_jobs dispatches to HTTP when MOONMIND_URL is configured."""
    module = _load_module()
    submit_jobs = module["_submit_jobs"]

    monkeypatch.setenv("MOONMIND_URL", "http://api:5000")
    monkeypatch.delenv("MOONMIND_WORKER_TOKEN", raising=False)
    monkeypatch.delenv("MOONMIND_WORKER_TOKEN_FILE", raising=False)

    http_called = []

    async def fake_http(
        requests: list, *, moonmind_url: str, worker_token: Any
    ) -> tuple:
        http_called.append(moonmind_url)
        return [{"pr": 1, "branch": "b", "jobId": "x"}], []

    submission = _make_submission(module)

    # patch.dict doesn't work for runpy'd modules because the function's __globals__
    # dict IS the module dict; we must mutate it in place via setitem.
    monkeypatch.setitem(submit_jobs.__globals__, "_submit_jobs_via_http", fake_http)
    created, errors = asyncio.run(submit_jobs([submission]))

    assert http_called == ["http://api:5000"]
    assert len(created) == 1
    assert errors == []


def test_submit_jobs_errors_when_no_url(monkeypatch: Any) -> None:
    """The removed legacy DB queue fallback must not be used."""
    module = _load_module()
    submit_jobs = module["_submit_jobs"]

    monkeypatch.delenv("MOONMIND_URL", raising=False)

    submission = _make_submission(module)

    created, errors = asyncio.run(submit_jobs([submission]))

    assert created == []
    assert len(errors) == 1
    assert errors[0]["pr"] == 42
    assert "MOONMIND_URL is not set" in errors[0]["error"]
    assert "removed legacy DB queue" in errors[0]["error"]


def test_read_worker_token_from_file(monkeypatch: Any, tmp_path: Path) -> None:
    """_read_worker_token reads from MOONMIND_WORKER_TOKEN_FILE when TOKEN env is absent."""
    module = _load_module()
    read_worker_token = module["_read_worker_token"]

    token_file = tmp_path / "worker_token"
    token_file.write_text("  my-file-token  ", encoding="utf-8")

    monkeypatch.delenv("MOONMIND_WORKER_TOKEN", raising=False)
    monkeypatch.setenv("MOONMIND_WORKER_TOKEN_FILE", str(token_file))

    assert read_worker_token() == "my-file-token"


def test_read_worker_token_prefers_env_over_file(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """_read_worker_token prefers MOONMIND_WORKER_TOKEN over MOONMIND_WORKER_TOKEN_FILE."""
    module = _load_module()
    read_worker_token = module["_read_worker_token"]

    token_file = tmp_path / "worker_token"
    token_file.write_text("file-token", encoding="utf-8")

    monkeypatch.setenv("MOONMIND_WORKER_TOKEN", "env-token")
    monkeypatch.setenv("MOONMIND_WORKER_TOKEN_FILE", str(token_file))

    assert read_worker_token() == "env-token"


# ---------------------------------------------------------------------------
# SkillInvocation payload contract tests
# ---------------------------------------------------------------------------


def _build_request(module: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """Call _build_queue_request with sensible defaults, returning the raw dict."""
    _RuntimeSelection = module["RuntimeSelection"]
    build = module["_build_queue_request"]
    kwargs: dict[str, Any] = dict(
        runtime=_RuntimeSelection(mode="codex", model=None, effort=None),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )
    kwargs.update(overrides)
    return build("MoonLadderStudios/MoonMind", 42, "feature/test", **kwargs)


def test_build_queue_request_skill_contract() -> None:
    """skill.name + skill.version are present; legacy skill.id and skill.args are absent."""
    module = _load_module()
    req = _build_request(module)
    task = req["payload"]["task"]
    skill = task["skill"]

    # Correct fields per SkillInvocation contract
    assert skill.get("name") == "pr-resolver", "skill.name must be 'pr-resolver'"
    assert skill.get("version") == "1.0", "skill.version must default to '1.0'"

    # Legacy / wrong fields must NOT be present
    assert (
        "id" not in skill
    ), "skill.id is the legacy field; must not be sent to Temporal"
    assert (
        "args" not in skill
    ), "skill.args is legacy; inputs now live at task-node level"

    # inputs live at the task-node level, not inside skill
    inputs = task.get("inputs")
    assert isinstance(inputs, dict), "inputs must be a top-level key on task node"
    assert inputs.get("repo") == "MoonLadderStudios/MoonMind"
    assert inputs.get("pr") == "42"
    assert inputs.get("branch") == "feature/test"
    assert inputs.get("mergeMethod") == "squash"
    assert inputs.get("maxIterations") == 3


def test_build_queue_request_required_capabilities_toplevel() -> None:
    """requiredCapabilities must live at payload level, not inside skill."""
    module = _load_module()
    req = _build_request(module)
    payload = req["payload"]
    task = payload["task"]

    # Correct: top-level on payload so _create_execution_from_task_request can read it
    assert payload.get("requiredCapabilities") == ["gh"]

    # Wrong nesting must NOT be present
    skill = task["skill"]
    assert (
        "requiredCapabilities" not in skill
    ), "requiredCapabilities must not be nested inside skill"


def test_build_queue_request_skill_version_passthrough() -> None:
    """--skill-version value is forwarded to the payload."""
    module = _load_module()
    req = _build_request(module, skill_version="2.3")
    skill = req["payload"]["task"]["skill"]
    assert skill.get("version") == "2.3"
