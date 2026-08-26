"""Portable Skill tests for the pr-resolver finish mode.

`fix_only` narrows exactly one thing: the merge side effect. Every gate,
blocker, and remediation decision stays identical to `merge`.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_BIN = REPO_ROOT / ".agents" / "skills" / "pr-resolver" / "bin"
HEAD = "a" * 40


def _load_module(script_name: str) -> dict[str, Any]:
    return runpy.run_path(str(SKILL_BIN / script_name))


@pytest.fixture
def contract_module() -> dict[str, Any]:
    return _load_module("pr_resolve_contract.py")


@pytest.fixture
def finalize_module() -> dict[str, Any]:
    return _load_module("pr_resolve_finalize.py")


@pytest.fixture
def orchestrate_module() -> dict[str, Any]:
    return _load_module("pr_resolve_orchestrate.py")


def _mergeable_snapshot() -> dict[str, Any]:
    """A snapshot whose merge gate is fully open."""

    return {
        "repository": "MoonLadderStudios/MoonMind",
        "pr": {
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "state": "OPEN",
            "headRefOid": HEAD,
            "headRefName": "feature",
            "mergeStateStatus": "CLEAN",
            "mergeable": True,
        },
        "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
        "commentsFetch": {"succeeded": True},
        "commentsSummary": {
            "includeBotReviewComments": True,
            "hasActionableComments": False,
        },
        "automatedReview": {
            "enabled": True,
            "provider": "codex",
            "freshReviewForHead": True,
            "requestPending": False,
        },
        "progressSignature": f"{HEAD}||",
    }


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


def test_finish_mode_defaults_to_merge(contract_module) -> None:
    normalize = contract_module["normalize_finish_mode"]

    assert normalize(None) == "merge"
    assert normalize("") == "merge"
    assert normalize("merge") == "merge"
    assert normalize("MERGE") == "merge"


def test_fix_only_finish_mode_is_supported(contract_module) -> None:
    assert contract_module["normalize_finish_mode"]("fix_only") == "fix_only"


def test_unsupported_finish_mode_fails_fast(contract_module) -> None:
    with pytest.raises(ValueError):
        contract_module["normalize_finish_mode"]("auto")


def test_review_clean_status_maps_to_review_clean_disposition(contract_module) -> None:
    assert (
        contract_module["merge_automation_disposition_for_result"](
            status="review_clean",
            merge_outcome="skipped",
            final_reason="finish_mode_fix_only",
            next_step="done",
        )
        == "review_clean"
    )


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


def _run_finalize(
    finalize_module: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *extra_args: str,
) -> tuple[int, dict[str, Any]]:
    main = finalize_module["main"]
    snapshot = _mergeable_snapshot()

    def _write_snapshot(
        _snapshot_script: Path,
        _pr: str | None,
        snapshot_path: Path,
        **_review_kwargs: Any,
    ) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    merged: list[tuple[str, str]] = []
    monkeypatch.setitem(main.__globals__, "_run_snapshot", _write_snapshot)
    monkeypatch.setitem(
        main.__globals__,
        "_merge_pr",
        lambda selector, method: merged.append((selector, method)),
    )
    monkeypatch.setitem(main.__globals__, "_check_pr_merged", lambda _selector: True)
    monkeypatch.delenv("PR_RESOLVER_REVIEW_PROVIDER", raising=False)
    monkeypatch.delenv("PR_RESOLVER_REQUIRE_FRESH_REVIEW", raising=False)
    monkeypatch.delenv("PR_RESOLVER_FINISH_MODE", raising=False)

    result_path = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "350",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--strict-exit-codes",
            *extra_args,
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["_merged_calls"] = merged
    return int(excinfo.value.code), payload


def test_default_finalize_still_merges_an_open_gate(
    finalize_module, tmp_path, monkeypatch
) -> None:
    """Omitting --finish-mode must exercise the production merge path."""

    code, payload = _run_finalize(finalize_module, tmp_path, monkeypatch)

    assert code == finalize_module["EXIT_CODE_MERGED"]
    assert payload["status"] == "merged"
    assert payload["mergeAutomationDisposition"] == "merged"
    assert payload["_merged_calls"] == [("350", "squash")]


def test_fix_only_finalize_reports_review_clean_without_merging(
    finalize_module, tmp_path, monkeypatch
) -> None:
    code, payload = _run_finalize(
        finalize_module, tmp_path, monkeypatch, "--finish-mode", "fix_only"
    )

    assert code == finalize_module["EXIT_CODE_REVIEW_CLEAN"]
    assert payload["status"] == "review_clean"
    assert payload["merge_outcome"] == "skipped"
    assert payload["mergeAutomationDisposition"] == "review_clean"
    assert payload["final_reason"] == "finish_mode_fix_only"
    # The whole point: no merge side effect.
    assert payload["_merged_calls"] == []


def test_fix_only_finalize_still_blocks_on_a_missing_fresh_review(
    finalize_module, tmp_path, monkeypatch
) -> None:
    """fix_only must not weaken any gate; it only removes the merge."""

    main = finalize_module["main"]
    snapshot = _mergeable_snapshot()
    snapshot["automatedReview"]["freshReviewForHead"] = False

    def _write_snapshot(
        _snapshot_script: Path,
        _pr: str | None,
        snapshot_path: Path,
        **_review_kwargs: Any,
    ) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    monkeypatch.setitem(main.__globals__, "_run_snapshot", _write_snapshot)
    monkeypatch.delenv("PR_RESOLVER_FINISH_MODE", raising=False)
    result_path = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "350",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
            "--review-provider",
            "codex",
            "--require-fresh-review",
            "--finish-mode",
            "fix_only",
            "--strict-exit-codes",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == finalize_module["EXIT_CODE_BLOCKED"]
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["mergeAutomationDisposition"] == "request_review"


def test_finish_mode_can_come_from_the_environment(
    finalize_module, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("PR_RESOLVER_FINISH_MODE", "fix_only")
    main = finalize_module["main"]
    snapshot = _mergeable_snapshot()

    def _write_snapshot(
        _snapshot_script: Path,
        _pr: str | None,
        snapshot_path: Path,
        **_review_kwargs: Any,
    ) -> None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    monkeypatch.setitem(main.__globals__, "_run_snapshot", _write_snapshot)
    monkeypatch.setitem(
        main.__globals__,
        "_merge_pr",
        lambda *_args: pytest.fail("fix_only must never merge"),
    )
    result_path = tmp_path / "result.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "pr_resolve_finalize.py",
            "--pr",
            "350",
            "--snapshot-path",
            str(tmp_path / "snapshot.json"),
            "--result-path",
            str(result_path),
            "--strict-exit-codes",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == finalize_module["EXIT_CODE_REVIEW_CLEAN"]
    assert (
        json.loads(result_path.read_text(encoding="utf-8"))["status"] == "review_clean"
    )


# ---------------------------------------------------------------------------
# orchestrate
# ---------------------------------------------------------------------------


def test_orchestration_returns_review_clean_as_a_terminal_success(
    orchestrate_module,
) -> None:
    run_orchestration = orchestrate_module["run_orchestration"]

    def finalize_runner(_attempt: int) -> dict[str, Any]:
        return {
            "status": "review_clean",
            "merge_outcome": "skipped",
            "final_reason": "finish_mode_fix_only",
            "mergeAutomationDisposition": "review_clean",
        }

    def full_runner(*_args: Any) -> dict[str, Any]:
        raise AssertionError("review_clean must not escalate to remediation")

    result, exit_code = run_orchestration(
        finalize_runner=finalize_runner,
        full_runner=full_runner,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=lambda: 0.0,
        finalize_max_retries=2,
        fix_max_iterations=1,
        min_attempts_before_exhausted=1,
        base_sleep_seconds=0,
        max_sleep_seconds=0,
        max_elapsed_seconds=600,
        merge_not_ready_grace_retries=0,
    )

    assert exit_code == orchestrate_module["EXIT_CODE_REVIEW_CLEAN"]
    assert result["status"] == "review_clean"
    assert result["merge_outcome"] == "skipped"
    assert result["mergeAutomationDisposition"] == "review_clean"
    assert result["next_step"] == "done"
