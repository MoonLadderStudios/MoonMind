"""Unit tests for isolated multi-repository fan-out (MoonLadderStudios/MoonMind#1657).

Covers the portable authoring boundary in
``.agents/skills/_shared/repository_batch_targets.py`` and the engine
dispatch path in ``.agents/skills/_shared/batch_workflows.py``:

* R1: one operation drives at least two admitted repositories through
  distinct child/workspace/evidence owners;
* R2: duplicates, same-name different-host repos, wrong-owner connections,
  and post-approval injection cannot expand the batch;
* R3: restart reconciliation, lost-ack retry, capacity gating, budgets;
* R4: partial outcomes and selected retry without republishing;
* R5: credential/workspace isolation;
* R6: dependency edges consume verified evidence only;
* R7: negative scope holds by construction.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_ROOT = REPO_ROOT / ".agents" / "skills" / "_shared"


def _load_targets_module() -> Any:
    path = SHARED_ROOT / "repository_batch_targets.py"
    spec = importlib.util.spec_from_file_location(
        "repository_batch_targets_under_test", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _allow_all_branches(_branch: str) -> bool:
    return True


def _targets_module_entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "repository": "MoonLadderStudios/MoonMind",
        "connectionRef": "repository-connection:git-default",
        "branch": "main",
        "operation": "pr",
    }
    entry.update(overrides)
    return entry


def _normalize(module: Any, entries: list[dict[str, Any]], **kwargs: Any):
    return module.normalize_repository_batch(
        entries, branch_validator=_allow_all_branches, **kwargs
    )


# ---------------------------------------------------------------------------
# R1: one operation, two admitted repositories, distinct owners.
# ---------------------------------------------------------------------------


def test_two_repositories_build_distinct_isolated_children() -> None:
    module = _load_targets_module()
    normalized = _normalize(
        module,
        [
            _targets_module_entry(
                repository="MoonLadderStudios/MoonMind",
                connectionRef="conn-moonmind",
            ),
            _targets_module_entry(
                repository="octo/Hello-World",
                endpoint="https://ghe.example.com",
                connectionRef="conn-hello",
                branch="develop",
            ),
        ],
    )
    assert len(normalized.targets) == 2
    manifest = module.freeze_repository_batch_manifest(
        normalized,
        task={"runRef": "preset:github-issue-implement", "publishMode": "pr"},
        budget=module.parse_batch_budget({}),
    )
    children = [
        module.build_multi_repo_child_request(
            target,
            manifest_digest=str(manifest["digest"]),
            run_ref="preset:github-issue-implement",
            goal="Apply the security patch.",
            constraints="",
            publish_mode="pr",
            runtime_mode=None,
            runtime_model=None,
            runtime_effort=None,
            runtime_provider_profile=None,
            max_attempts=3,
        )
        for target in normalized.targets
    ]
    keys = [child["payload"]["idempotencyKey"] for child in children]
    assert len(set(keys)) == 2
    connections = [
        child["payload"]["repository"]["connectionRef"] for child in children
    ]
    assert connections == ["conn-moonmind", "conn-hello"]
    namespaces = [
        child["payload"]["task"]["inputs"]["artifact_namespace"] for child in children
    ]
    assert len(set(namespaces)) == 2
    for child in children:
        assert child["payload"]["batchDigest"] == manifest["digest"]
        assert child["payload"]["batchTarget"]["connectionRef"]
        assert child["payload"]["runtimeInheritance"] == "caller"


def test_child_payload_carries_no_credential_material() -> None:
    module = _load_targets_module()
    normalized = _normalize(module, [_targets_module_entry()])
    manifest = module.freeze_repository_batch_manifest(
        normalized,
        task={"runRef": "preset:github-issue-implement", "publishMode": "pr"},
        budget=module.parse_batch_budget({}),
    )
    child = module.build_multi_repo_child_request(
        normalized.targets[0],
        manifest_digest=str(manifest["digest"]),
        run_ref="preset:github-issue-implement",
        goal="Apply the patch.",
        constraints="",
        publish_mode="pr",
        runtime_mode=None,
        runtime_model=None,
        runtime_effort=None,
        runtime_provider_profile=None,
        max_attempts=3,
    )
    def _credential_keys(node: Any) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                normalized_key = str(key).lower().replace("-", "_")
                if normalized_key in {
                    "token",
                    "tokens",
                    "token_bundle",
                    "secret",
                    "secrets",
                    "credential",
                    "credentials",
                    "pat",
                    "password",
                }:
                    found.append(str(key))
                found.extend(_credential_keys(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(_credential_keys(item))
        return found

    assert _credential_keys(child) == []


# ---------------------------------------------------------------------------
# R2: the approved set cannot be expanded.
# ---------------------------------------------------------------------------


def test_duplicate_targets_collapse_with_evidence() -> None:
    module = _load_targets_module()
    normalized = _normalize(
        module, [_targets_module_entry(), _targets_module_entry()]
    )
    assert len(normalized.targets) == 1
    assert len(normalized.duplicates) == 1
    assert normalized.duplicates[0]["repository"] == "MoonLadderStudios/MoonMind"


def test_same_name_on_different_hosts_stays_distinct() -> None:
    module = _load_targets_module()
    normalized = _normalize(
        module,
        [
            _targets_module_entry(repository="acme/widgets"),
            _targets_module_entry(
                repository="acme/widgets", endpoint="https://ghe.example.com"
            ),
        ],
    )
    assert len(normalized.targets) == 2
    assert normalized.duplicates == []


def test_conflicting_connections_for_one_repository_fail_closed() -> None:
    module = _load_targets_module()
    with pytest.raises(module.RepositoryBatchError) as exc:
        _normalize(
            module,
            [
                _targets_module_entry(connectionRef="conn-a"),
                _targets_module_entry(connectionRef="conn-b"),
            ],
        )
    assert exc.value.code == "REPOSITORY_BATCH_CONNECTION_CONFLICT"


def test_missing_connection_and_wildcards_and_bundles_rejected() -> None:
    module = _load_targets_module()
    with pytest.raises(module.RepositoryBatchError) as exc:
        _normalize(
            module, [{"repository": "acme/widgets", "branch": "main", "operation": "pr"}]
        )
    assert exc.value.code == "REPOSITORY_BATCH_CONNECTION_REQUIRED"
    with pytest.raises(module.RepositoryBatchError) as exc:
        _normalize(module, [_targets_module_entry(repository="acme/*")])
    assert exc.value.code == "REPOSITORY_BATCH_WILDCARD_REJECTED"
    with pytest.raises(module.RepositoryBatchError) as exc:
        _normalize(
            module,
            [
                {
                    "repository": "acme/widgets",
                    "connectionRef": "conn-a",
                    "branch": "main",
                    "operation": "pr",
                    "tokenBundle": {"acme/widgets": "ghp_example"},
                }
            ],
        )
    assert exc.value.code == "REPOSITORY_BATCH_TOKEN_BUNDLE_REJECTED"


def test_post_approval_injection_changes_digest_and_is_rejected() -> None:
    module = _load_targets_module()
    normalized = _normalize(module, [_targets_module_entry()])
    manifest = module.freeze_repository_batch_manifest(
        normalized,
        task={"runRef": "preset:github-issue-implement", "publishMode": "pr"},
        budget=module.parse_batch_budget({}),
    )
    approved = str(manifest["digest"])
    module.verify_manifest_unchanged(manifest, approved)
    tampered = dict(manifest)
    tampered_targets = [dict(item) for item in manifest["targets"]]
    tampered_targets.append(
        {
            "targetRef": "https://github.com#evil/added",
            "endpoint": "https://github.com",
            "repository": "evil/added",
            "connectionRef": "conn-evil",
            "branch": "main",
            "operation": "pr",
            "revision": None,
            "dependsOn": [],
            "evidenceKind": None,
        }
    )
    tampered["targets"] = tampered_targets
    with pytest.raises(module.RepositoryBatchError) as exc:
        module.verify_manifest_unchanged(tampered, approved)
    assert exc.value.code == "REPOSITORY_BATCH_MANIFEST_MISMATCH"
    with pytest.raises(module.RepositoryBatchError) as exc:
        module.verify_manifest_unchanged(manifest, "")
    assert exc.value.code == "REPOSITORY_BATCH_APPROVAL_REQUIRED"


def test_preflight_blocks_by_default_and_supports_explicit_partial() -> None:
    module = _load_targets_module()
    normalized = _normalize(
        module,
        [
            _targets_module_entry(repository="acme/good"),
            _targets_module_entry(repository="acme/bad"),
        ],
    )

    def _probe(target: Any) -> tuple[bool, str]:
        if target.repository == "acme/bad":
            return False, "unsupported_target"
        return True, "accessible"

    blocked = module.preflight_repository_batch(normalized, probe=_probe)
    assert blocked.blocked is True
    assert blocked.failure
    partial = module.preflight_repository_batch(
        normalized, probe=_probe, allow_partial=True
    )
    assert partial.blocked is False
    assert [item.target_ref for item in partial.targets if not item.accessible]


def test_read_only_target_requires_none_publish_mode() -> None:
    module = _load_targets_module()
    normalized = _normalize(module, [_targets_module_entry(operation="read")])
    report = module.preflight_repository_batch(normalized, publish_mode="pr")
    assert report.blocked is True
    assert report.targets[0].reason == "read_only_publish_mismatch"
    allowed = module.preflight_repository_batch(normalized, publish_mode="none")
    assert allowed.blocked is False


# ---------------------------------------------------------------------------
# R3: reconciliation, idempotency, capacity, budgets.
# ---------------------------------------------------------------------------


def _manifest_with_two_targets(module: Any) -> tuple[Any, Any]:
    normalized = _normalize(
        module,
        [
            _targets_module_entry(repository="acme/one"),
            _targets_module_entry(
                repository="acme/two", connectionRef="conn-two"
            ),
        ],
    )
    manifest = module.freeze_repository_batch_manifest(
        normalized,
        task={"runRef": "preset:github-issue-implement", "publishMode": "pr"},
        budget=module.parse_batch_budget({}),
    )
    return normalized, manifest


def test_restart_discovers_accepted_children_without_duplicates() -> None:
    module = _load_targets_module()
    normalized, manifest = _manifest_with_two_targets(module)
    digest = str(manifest["digest"])
    prior = module.build_batch_aggregate_result(
        manifest=manifest,
        per_target=[
            {
                "targetRef": normalized.targets[0].target_ref,
                "status": "queued",
                "workflowId": "mm:child-one",
                "attempt": 0,
            },
            {
                "targetRef": normalized.targets[1].target_ref,
                "status": "failed",
                "error": "boom",
                "attempt": 0,
            },
        ],
        batch_status="partial",
    )
    to_submit, reused = module.reconcile_with_prior_result(
        manifest_digest=digest, targets=normalized.targets, prior_result=prior
    )
    assert [item["reason"] for item in reused] == [
        "already_queued",
        "terminal_preserved",
    ]
    # Failed without an explicit retry stays preserved; nothing resubmits.
    assert to_submit == []
    assert reused[0]["workflowId"] == "mm:child-one"


def test_ambiguous_attempt_keeps_key_while_retry_advances_attempt() -> None:
    module = _load_targets_module()
    normalized, manifest = _manifest_with_two_targets(module)
    digest = str(manifest["digest"])
    target = normalized.targets[0]
    first_key = module.multi_repo_child_idempotency_key(
        manifest_digest=digest, target=target, run_ref="preset:github-issue-implement"
    )
    # Lost acknowledgment: no workflowId, so the retry keeps the same key and
    # the execution API dedupes instead of duplicating work.
    prior = module.build_batch_aggregate_result(
        manifest=manifest,
        per_target=[
            {"targetRef": target.target_ref, "status": "unknown", "attempt": 0}
        ],
        batch_status="partial",
    )
    to_submit, _ = module.reconcile_with_prior_result(
        manifest_digest=digest,
        targets=[target],
        prior_result=prior,
        retry_failed_only=True,
    )
    assert len(to_submit) == 1 and to_submit[0][1] == 0
    assert (
        module.multi_repo_child_idempotency_key(
            manifest_digest=digest,
            target=target,
            run_ref="preset:github-issue-implement",
            attempt=to_submit[0][1],
        )
        == first_key
    )
    # Selected retry of an admitted-but-failed child advances the attempt so
    # it admits a fresh child instead of resolving to the terminal one.
    admitted = module.build_batch_aggregate_result(
        manifest=manifest,
        per_target=[
            {
                "targetRef": target.target_ref,
                "status": "failed",
                "workflowId": "mm:child-old",
                "attempt": 0,
            }
        ],
        batch_status="partial",
    )
    to_retry, _ = module.reconcile_with_prior_result(
        manifest_digest=digest,
        targets=[target],
        prior_result=admitted,
        retry_failed_only=True,
    )
    assert len(to_retry) == 1 and to_retry[0][1] == 1
    assert (
        module.multi_repo_child_idempotency_key(
            manifest_digest=digest,
            target=target,
            run_ref="preset:github-issue-implement",
            attempt=1,
        )
        != first_key
    )


def test_idempotency_key_stable_and_manifest_bound() -> None:
    module = _load_targets_module()
    normalized, manifest = _manifest_with_two_targets(module)
    digest = str(manifest["digest"])
    target = normalized.targets[0]
    first = module.multi_repo_child_idempotency_key(
        manifest_digest=digest, target=target, run_ref="preset:github-issue-implement"
    )
    second = module.multi_repo_child_idempotency_key(
        manifest_digest=digest, target=target, run_ref="preset:github-issue-implement"
    )
    assert first == second
    other = module.multi_repo_child_idempotency_key(
        manifest_digest="sha256:other",
        target=target,
        run_ref="preset:github-issue-implement",
    )
    assert other != first


def test_capacity_gate_and_budget_bounds() -> None:
    module = _load_targets_module()
    assert (
        module.gate_on_capacity(running_owned=1, max_concurrency=3, target_ref="t")
        is None
    )
    hold = module.gate_on_capacity(running_owned=3, max_concurrency=3, target_ref="t")
    assert hold is not None and hold["reason"] == "capacity_waiting"
    with pytest.raises(module.RepositoryBatchError):
        module.normalize_repository_batch(
            [_targets_module_entry() for _ in range(11)],
            branch_validator=_allow_all_branches,
        )
    with pytest.raises(module.RepositoryBatchError):
        module.parse_batch_budget({"maxTargets": 99})
    budget = module.parse_batch_budget(
        {"maxTargets": 5, "maxConcurrency": 2, "maxChildSpendUsd": 1.5}
    )
    assert budget.max_targets == 5 and budget.max_child_spend_usd == 1.5


# ---------------------------------------------------------------------------
# R4: partial outcomes and selected retry.
# ---------------------------------------------------------------------------


def test_partial_aggregate_and_selected_retry_skip_completed() -> None:
    module = _load_targets_module()
    _, manifest = _manifest_with_two_targets(module)
    aggregate = module.build_batch_aggregate_result(
        manifest=manifest,
        per_target=[
            {"targetRef": "a", "status": "succeeded", "workflowId": "mm:a"},
            {"targetRef": "b", "status": "failed", "workflowId": "mm:b"},
            {"targetRef": "c", "status": "canceled"},
            {"targetRef": "d", "status": "queued", "workflowId": "mm:d"},
        ],
        batch_status="partial",
    )
    assert aggregate["counts"]["succeeded"] == 1
    assert aggregate["status"] == "partial"
    assert module.selected_retry_targets(aggregate) == ["b", "c"]
    assert module.selected_retry_targets(aggregate, selected_refs=["a", "b"]) == ["b"]


# ---------------------------------------------------------------------------
# R6: dependency edges consume verified evidence only.
# ---------------------------------------------------------------------------


def test_dependency_phases_require_verified_evidence() -> None:
    module = _load_targets_module()
    normalized = _normalize(
        module,
        [
            _targets_module_entry(repository="acme/base"),
            _targets_module_entry(
                repository="acme/app",
                connectionRef="conn-app",
                dependsOn=["https://github.com#acme/base"],
                evidenceKind="revision",
            ),
        ],
    )
    phases = module.resolve_target_phases(normalized.targets)
    assert len(phases) == 2
    releasable, blocked = module.gate_dependent_targets(phases[1])
    assert releasable == [] and len(blocked) == 1
    # A bare PR reference never satisfies a merged-code dependency.
    releasable, blocked = module.gate_dependent_targets(
        phases[1],
        verify_upstream_evidence=lambda ref: {"pr": 42, "url": "https://example/x"},
    )
    assert releasable == [] and len(blocked) == 1
    releasable, blocked = module.gate_dependent_targets(
        phases[1],
        verify_upstream_evidence=lambda ref: {
            "verified": True,
            "kind": "revision",
            "revision": "abc1234",
        },
    )
    assert len(releasable) == 1 and blocked == []


def test_dependency_cycles_and_unknown_refs_rejected() -> None:
    module = _load_targets_module()
    with pytest.raises(module.RepositoryBatchError) as exc:
        _normalize(
            module,
            [
                _targets_module_entry(
                    repository="acme/a", dependsOn=["https://github.com#acme/b"]
                ),
                _targets_module_entry(
                    repository="acme/b",
                    connectionRef="conn-b",
                    dependsOn=["https://github.com#acme/a"],
                ),
            ],
        )
    assert exc.value.code == "REPOSITORY_BATCH_DEPENDENCY_CYCLE"
    with pytest.raises(module.RepositoryBatchError):
        _normalize(
            module,
            [_targets_module_entry(repository="acme/a", dependsOn=["missing-ref"])],
        )


# ---------------------------------------------------------------------------
# Engine dispatch path (hermetic subprocess + local HTTP double).
# ---------------------------------------------------------------------------


def _snapshot_skills(tmp_path: Path) -> Path:
    snapshot = tmp_path / "skills_active"
    shutil.copytree(
        REPO_ROOT / ".agents" / "skills" / "batch-github-workflows",
        snapshot / "batch-github-workflows",
    )
    shutil.copytree(REPO_ROOT / ".agents" / "skills" / "_shared", snapshot / "_shared")
    return snapshot


def _write_targets(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


class _BatchDoubleHandler(BaseHTTPRequestHandler):
    posts: list[dict[str, Any]] = []
    cancels: list[str] = []
    describe_status: dict[str, str] = {}

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path.endswith("/cancel"):
            workflow_id = self.path.split("/")[-2]
            type(self).cancels.append(workflow_id)
            self._send_json({"workflowId": workflow_id, "status": "canceled"})
            return
        payload = json.loads(raw.decode("utf-8"))
        type(self).posts.append(payload)
        workflow_id = f"mm:child-{len(type(self).posts)}"
        self._send_json({"workflowId": workflow_id})

    def do_GET(self) -> None:  # noqa: N802
        workflow_id = self.path.rstrip("/").split("/")[-1]
        status = type(self).describe_status.get(workflow_id, "running")
        self._send_json({"workflowId": workflow_id, "status": status})

    def log_message(self, *_args: Any) -> None:
        return None


def _run_helper(
    snapshot: Path,
    workspace: Path,
    targets_path: Path,
    extra_args: list[str],
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "MOONMIND_STEP_EXECUTION_ID": "step-repo-batch-1",
        "MOONMIND_ACTIVE_SKILLS_DIR": str(snapshot),
        **(env_extra or {}),
    }
    # The hermetic double serves loopback HTTP: never route it through an
    # ambient egress proxy inherited from the test worker environment.
    for proxy_var in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
    ):
        env.pop(proxy_var, None)
    env["no_proxy"] = "127.0.0.1,localhost"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    return subprocess.run(
        [
            sys.executable,
            str(snapshot / "batch-github-workflows" / "bin" / "batch_workflows.py"),
            "--run-ref",
            "preset:github-issue-implement",
            "--repository-targets-file",
            str(targets_path),
            "--artifacts-dir",
            str(workspace / "artifacts"),
            "--capacity-poll-interval",
            "0",
            *extra_args,
        ],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def _manifest_of(workspace: Path) -> dict[str, Any]:
    return json.loads(
        (workspace / "artifacts" / "batch-repository-manifest.json").read_text()
    )


def _result_of(workspace: Path) -> dict[str, Any]:
    return json.loads(
        (workspace / "artifacts" / "batch-repositories-result.json").read_text()
    )


def _artifacts(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return _manifest_of(workspace), _result_of(workspace)


def test_engine_preflight_freezes_manifest_without_dispatch(tmp_path: Path) -> None:
    snapshot = _snapshot_skills(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    targets_path = _write_targets(
        workspace / "targets.json",
        [
            {
                "repository": "acme/one",
                "connectionRef": "conn-one",
                "branch": "main",
                "operation": "pr",
            },
            {
                "repository": "acme/one",
                "connectionRef": "conn-one",
                "branch": "main",
                "operation": "pr",
            },
            {
                "repository": "acme/two",
                "connectionRef": "conn-two",
                "branch": "main",
                "operation": "pr",
            },
        ],
    )
    completed = _run_helper(snapshot, workspace, targets_path, ["--preflight-only"])
    assert completed.returncode == 0, completed.stderr
    manifest = _manifest_of(workspace)
    assert len(manifest["targets"]) == 2
    assert len(manifest["duplicatesNormalized"]) == 1
    assert manifest["digest"].startswith("sha256:")


def test_engine_refuses_dispatch_without_approved_digest(tmp_path: Path) -> None:
    _BatchDoubleHandler.posts = []
    snapshot = _snapshot_skills(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    targets_path = _write_targets(
        workspace / "targets.json",
        [
            {
                "repository": "acme/one",
                "connectionRef": "conn-one",
                "branch": "main",
                "operation": "pr",
            }
        ],
    )
    completed = _run_helper(snapshot, workspace, targets_path, [])
    assert completed.returncode == 2
    assert _BatchDoubleHandler.posts == []
    _, result = _artifacts(workspace)
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "REPOSITORY_BATCH_APPROVAL_REQUIRED"


def test_engine_dispatch_verifies_restart_reuses_and_retry_advances(
    tmp_path: Path,
) -> None:
    _BatchDoubleHandler.posts = []
    _BatchDoubleHandler.cancels = []
    _BatchDoubleHandler.describe_status = {}
    snapshot = _snapshot_skills(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    targets_path = _write_targets(
        workspace / "targets.json",
        [
            {
                "repository": "acme/one",
                "connectionRef": "conn-one",
                "branch": "main",
                "operation": "pr",
            },
            {
                "repository": "acme/two",
                "connectionRef": "conn-two",
                "branch": "main",
                "operation": "pr",
            },
        ],
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BatchDoubleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        preflight = _run_helper(
            snapshot, workspace, targets_path, ["--preflight-only"]
        )
        assert preflight.returncode == 0, preflight.stderr
        manifest = _manifest_of(workspace)
        digest = manifest["digest"]
        first = _run_helper(
            snapshot,
            workspace,
            targets_path,
            ["--approved-batch-digest", digest],
            env_extra={"MOONMIND_URL": url},
        )
        assert first.returncode == 0, first.stderr + first.stdout
        assert len(_BatchDoubleHandler.posts) == 2
        _, result = _artifacts(workspace)
        assert result["status"] == "queued"
        assert result["manifestDigest"] == digest
        posted_keys = [
            post["payload"]["idempotencyKey"] for post in _BatchDoubleHandler.posts
        ]
        assert len(set(posted_keys)) == 2
        for post in _BatchDoubleHandler.posts:
            assert post["payload"]["batchDigest"] == digest
            assert post["payload"]["batchTarget"]["connectionRef"]
            assert post["payload"]["runtimeInheritance"] == "caller"
        # A parent restart discovers the accepted children instead of
        # submitting the same work again.
        restart = _run_helper(
            snapshot,
            workspace,
            targets_path,
            ["--approved-batch-digest", digest],
            env_extra={"MOONMIND_URL": url},
        )
        assert restart.returncode == 0, restart.stderr + restart.stdout
        assert len(_BatchDoubleHandler.posts) == 2
        # Selected retry of one failed child admits a fresh child for that
        # target only and never republishes the completed sibling.
        retry_result_path = workspace / "artifacts" / "batch-repositories-result.json"
        prior = json.loads(retry_result_path.read_text())
        assert prior["status"] == "queued"
        for item in prior["targets"]:
            if item["repository"] == "acme/one":
                item["status"] = "succeeded"
            else:
                item["status"] = "failed"
        retry_result_path.write_text(json.dumps(prior), encoding="utf-8")
        _BatchDoubleHandler.describe_status = {}
        retried = _run_helper(
            snapshot,
            workspace,
            targets_path,
            ["--approved-batch-digest", digest, "--retry-failed-only"],
            env_extra={"MOONMIND_URL": url},
        )
        assert retried.returncode == 0, retried.stderr + retried.stdout
        assert len(_BatchDoubleHandler.posts) == 3
        _, retry_result = _artifacts(workspace)
        retried_targets = {
            item["targetRef"]: item for item in retry_result["targets"]
        }
        attempts = {item["attempt"] for item in retry_result["targets"]}
        assert retry_result["status"] == "queued"
        assert attempts == {0, 1}
        assert any(
            item.get("reason") == "retry_skipped_completed"
            for item in retry_result["targets"]
            if item.get("status") == "skipped"
        )
        resubmitted = [
            item
            for item in retry_result["targets"]
            if item.get("status") == "queued"
        ]
        assert len(resubmitted) == 1 and resubmitted[0]["attempt"] == 1
        assert resubmitted[0]["idempotencyKey"] not in posted_keys
        assert retried_targets
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_engine_cancel_owned_requests_owned_children(tmp_path: Path) -> None:
    _BatchDoubleHandler.posts = []
    _BatchDoubleHandler.cancels = []
    _BatchDoubleHandler.describe_status = {}
    snapshot = _snapshot_skills(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    targets_path = _write_targets(
        workspace / "targets.json",
        [
            {
                "repository": "acme/one",
                "connectionRef": "conn-one",
                "branch": "main",
                "operation": "pr",
            }
        ],
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _BatchDoubleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        preflight = _run_helper(
            snapshot, workspace, targets_path, ["--preflight-only"]
        )
        assert preflight.returncode == 0, preflight.stderr
        digest = _manifest_of(workspace)["digest"]
        dispatched = _run_helper(
            snapshot,
            workspace,
            targets_path,
            ["--approved-batch-digest", digest],
            env_extra={"MOONMIND_URL": url},
        )
        assert dispatched.returncode == 0, dispatched.stderr + dispatched.stdout
        canceled = _run_helper(
            snapshot,
            workspace,
            targets_path,
            ["--cancel-owned"],
            env_extra={"MOONMIND_URL": url},
        )
        assert canceled.returncode == 0, canceled.stderr + canceled.stdout
        assert _BatchDoubleHandler.cancels == ["mm:child-1"]
        artifacts = workspace / "artifacts"
        result = json.loads(
            (artifacts / "batch-repositories-result.json").read_text()
        )
        assert result["status"] == "canceled"
        assert result["targets"][0]["reason"] == "cancel_requested"
    finally:
        server.shutdown()
        thread.join(timeout=5)
