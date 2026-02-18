"""Unit tests for task template CLI helper client."""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.agents.cli.task_templates import TaskTemplateClient, merge_expanded_steps


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_list_templates_and_expand(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get(url, headers, params, timeout):
        calls.append(("GET", url, params))
        return _FakeResponse({"items": [{"slug": "demo", "latestVersion": "1.0.0"}]})

    def fake_post(url, headers, params, json, timeout):
        calls.append(("POST", url, params, json))
        return _FakeResponse(
            {
                "steps": [{"id": "tpl:demo:1.0.0:01:abcd1234", "instructions": "run"}],
                "appliedTemplate": {
                    "slug": "demo",
                    "version": "1.0.0",
                    "inputs": {},
                    "stepIds": ["tpl:demo:1.0.0:01:abcd1234"],
                },
                "capabilities": ["codex"],
                "warnings": [],
            }
        )

    monkeypatch.setattr("moonmind.agents.cli.task_templates.requests.get", fake_get)
    monkeypatch.setattr("moonmind.agents.cli.task_templates.requests.post", fake_post)

    client = TaskTemplateClient(base_url="http://localhost:8000", token="token")
    items = client.list_templates(scope="global", search="demo")
    expanded = client.expand_template(
        slug="demo",
        scope="global",
        version="1.0.0",
        inputs={},
    )

    assert items[0]["slug"] == "demo"
    assert expanded["appliedTemplate"]["slug"] == "demo"
    assert calls[0][0] == "GET"
    assert calls[1][0] == "POST"


def test_merge_expanded_steps_modes() -> None:
    existing = [{"id": "s1", "instructions": "one"}]
    incoming = [{"id": "s2", "instructions": "two"}]

    assert merge_expanded_steps(
        existing_steps=existing, expanded_steps=incoming, mode="append"
    ) == [
        {"id": "s1", "instructions": "one"},
        {"id": "s2", "instructions": "two"},
    ]
    assert merge_expanded_steps(
        existing_steps=existing, expanded_steps=incoming, mode="replace"
    ) == [{"id": "s2", "instructions": "two"}]

    with pytest.raises(ValueError):
        merge_expanded_steps(
            existing_steps=existing, expanded_steps=incoming, mode="bad"
        )


def test_cli_raises_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url, headers, params, timeout):
        return _FakeResponse({"detail": "boom"}, status_code=422)

    monkeypatch.setattr("moonmind.agents.cli.task_templates.requests.get", fake_get)

    client = TaskTemplateClient(base_url="http://localhost:8000", token="token")
    with pytest.raises(RuntimeError, match="HTTP 422"):
        client.list_templates(scope="global")
