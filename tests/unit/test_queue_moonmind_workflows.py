from __future__ import annotations

import asyncio
import json
import runpy
from pathlib import Path
from typing import Any

import httpx
import pytest


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "queue-moonmind-workflows"
            / "scripts"
            / "queue_moonmind_workflows.py"
        )
    )


def _task_request() -> dict[str, Any]:
    return {
        "type": "task",
        "priority": 0,
        "maxAttempts": 3,
        "payload": {
            "repository": "MoonLadderStudios/MoonMind",
            "runtimeInheritance": "caller",
            "task": {
                "title": "Implement issue",
                "instructions": "Implement GitHub issue MoonLadderStudios/MoonMind#722.",
                "skill": {"name": "github-issue-implement"},
                "inputs": {"github_issue_ref": "MoonLadderStudios/MoonMind#722"},
                "publish": {"mode": "pr"},
            },
        },
    }


def test_load_manifest_injects_stable_idempotency_for_task_shape(tmp_path: Path) -> None:
    module = _load_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "batchScope": "mm:parent",
                "workflows": [
                    {
                        "ref": "github:MoonLadderStudios/MoonMind#722",
                        "request": _task_request(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    children, skipped = module["_load_manifest"](manifest)

    assert skipped == []
    child = children[0]
    key = child.request["payload"]["idempotencyKey"]
    assert key == child.idempotency_key
    assert key.startswith("queue-moonmind-workflows:")
    assert len(key) <= module["IDEMPOTENCY_KEY_MAX_LENGTH"]


def test_load_manifest_preserves_existing_direct_create_idempotency(
    tmp_path: Path,
) -> None:
    module = _load_module()
    manifest = tmp_path / "manifest.json"
    request = {
        "workflowType": "MoonMind.UserWorkflow",
        "title": "Child",
        "initialParameters": {"workflow": {"instructions": "Do work."}},
        "idempotencyKey": "explicit-key",
    }
    manifest.write_text(
        json.dumps({"workflows": [{"ref": "child-1", "request": request}]}),
        encoding="utf-8",
    )

    children, _skipped = module["_load_manifest"](manifest)

    assert children[0].idempotency_key == "explicit-key"
    assert children[0].request["idempotencyKey"] == "explicit-key"


def test_load_manifest_rejects_missing_idempotency_scope(tmp_path: Path) -> None:
    module = _load_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"workflows": [{"ref": "child-1", "request": _task_request()}]}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="no idempotencyKey and no batchScope"):
        module["_load_manifest"](manifest)


def test_load_manifest_caps_workflows_and_reports_skips(tmp_path: Path) -> None:
    module = _load_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "batchScope": "mm:parent",
                "workflows": [
                    {"ref": "child-1", "request": _task_request()},
                    {"ref": "child-2", "request": _task_request()},
                ],
            }
        ),
        encoding="utf-8",
    )

    children, skipped = module["_load_manifest"](manifest, max_workflows=1)

    assert [child.ref for child in children] == ["child-1"]
    assert skipped == [{"ref": "child-2", "reason": "max_workflows_exceeded"}]


def test_load_manifest_accepts_explicit_empty_workflows(tmp_path: Path) -> None:
    module = _load_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"workflows": []}), encoding="utf-8")

    children, skipped = module["_load_manifest"](manifest)

    assert children == []
    assert skipped == []


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.reason_phrase = "OK" if status_code < 400 else "Error"
        self.request = httpx.Request("GET", "http://moonmind.test")

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    request=self.request,
                    text=self.text,
                ),
            )


class _FakeClient:
    def __init__(self, get_responses: list[_FakeResponse]) -> None:
        self.get_responses = list(get_responses)
        self.posts: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, _path: str, *, json: dict[str, Any]) -> _FakeResponse:
        self.posts.append(json)
        return _FakeResponse(
            201,
            {
                "workflowId": "mm:child",
                "runId": "run-1",
                "state": "initializing",
            },
        )

    async def get(self, _path: str) -> _FakeResponse:
        if not self.get_responses:
            raise AssertionError("unexpected GET")
        return self.get_responses.pop(0)


def test_submit_and_verify_counts_only_described_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    fake = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "workflowId": "mm:child",
                    "runId": "run-1",
                    "state": "initializing",
                    "temporalStatus": "running",
                },
            )
        ]
    )
    monkeypatch.setattr(module["httpx"], "AsyncClient", lambda **_kwargs: fake)
    child = module["ChildExecution"](
        ref="child-1",
        request=_task_request(),
        idempotency_key="key-1",
    )

    queued, errors = asyncio.run(
        module["_submit_and_verify"](
            [child],
            moonmind_url="http://moonmind.test",
            verify_attempts=1,
            verify_delay_seconds=0,
        )
    )

    assert errors == []
    assert queued == [
        {
            "ref": "child-1",
            "workflowId": "mm:child",
            "runId": "run-1",
            "state": "initializing",
            "temporalStatus": "running",
            "idempotencyKey": "key-1",
        }
    ]


def test_submit_and_verify_reports_unverified_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    fake = _FakeClient([_FakeResponse(404, {"detail": "not found"})])
    monkeypatch.setattr(module["httpx"], "AsyncClient", lambda **_kwargs: fake)
    child = module["ChildExecution"](
        ref="child-1",
        request=_task_request(),
        idempotency_key="key-1",
    )

    queued, errors = asyncio.run(
        module["_submit_and_verify"](
            [child],
            moonmind_url="http://moonmind.test",
            verify_attempts=1,
            verify_delay_seconds=0,
        )
    )

    assert queued == []
    assert errors[0]["ref"] == "child-1"
    assert "was not verified" in errors[0]["error"]
