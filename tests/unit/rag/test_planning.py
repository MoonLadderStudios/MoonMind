from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from moonmind.rag.planning import (
    BeadsPlanningAdapter,
    PlanningAdapterError,
    PlanningFollowup,
)


def test_beads_planning_adapter_prefetch_formats_issue_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".beads").mkdir()
    calls: list[list[str]] = []

    def _run(command, **kwargs):
        calls.append(command)
        if command[-2:] == ["show", "bd-123"]:
            stdout = json.dumps(
                {
                    "id": "bd-123",
                    "title": "Implement Planning Memory",
                    "status": "open",
                    "priority": 1,
                    "description": "Wire Beads into context packs.",
                    "dependencies": [{"id": "bd-100"}],
                }
            )
        elif command[-1] == "ready":
            stdout = json.dumps(
                [
                    {
                        "id": "bd-124",
                        "title": "Follow-up work",
                        "status": "open",
                    }
                ]
            )
        else:
            stdout = "{}"
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr("moonmind.rag.planning.shutil.which", lambda command: command)
    monkeypatch.setattr("moonmind.rag.planning.subprocess.run", _run)

    adapter = BeadsPlanningAdapter(repo_root=tmp_path)
    item = adapter.prefetch("bd-123")

    assert item is not None
    assert item.source == "beads:bd-123"
    assert item.trust_class == "planning"
    assert item.payload["record_kind"] == "planning"
    assert "Implement Planning Memory" in item.text
    assert "bd-124: Follow-up work" in item.text
    assert calls[0][:3] == ["bd", "--no-daemon", "--json"]


def test_beads_planning_adapter_writeback_commands(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".beads").mkdir()
    calls: list[list[str]] = []

    def _run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({"ok": True}))

    monkeypatch.setattr("moonmind.rag.planning.shutil.which", lambda command: command)
    monkeypatch.setattr("moonmind.rag.planning.subprocess.run", _run)

    adapter = BeadsPlanningAdapter(repo_root=tmp_path)
    adapter.claim("bd-123", assignee="agent")
    adapter.close("bd-123", reason="Completed by MM-765")
    adapter.create_followups(
        planning_ref="bd-123",
        followups=[
            PlanningFollowup(
                title="Add Plane B digest follow-up",
                description="Next memory plane",
                priority=2,
            )
        ],
    )

    assert calls[0] == [
        "bd",
        "--no-daemon",
        "--json",
        "update",
        "bd-123",
        "--claim",
        "--assignee",
        "agent",
    ]
    assert calls[1] == [
        "bd",
        "--no-daemon",
        "--json",
        "close",
        "bd-123",
        "--reason",
        "Completed by MM-765",
    ]
    assert calls[2] == [
        "bd",
        "--no-daemon",
        "--json",
        "create",
        "Add Plane B digest follow-up",
        "--type",
        "task",
        "--description",
        "Next memory plane",
        "--priority",
        "2",
        "--deps",
        "discovered-from:bd-123",
    ]


def test_beads_planning_adapter_returns_none_when_repo_has_no_beads(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.rag.planning.shutil.which", lambda command: command)
    adapter = BeadsPlanningAdapter(repo_root=tmp_path)

    assert adapter.prefetch("bd-123") is None


def test_beads_planning_adapter_raises_for_invalid_json(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".beads").mkdir()
    monkeypatch.setattr("moonmind.rag.planning.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "moonmind.rag.planning.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="not-json"),
    )
    adapter = BeadsPlanningAdapter(repo_root=tmp_path)

    with pytest.raises(PlanningAdapterError, match="invalid JSON"):
        adapter.prefetch("bd-123")
