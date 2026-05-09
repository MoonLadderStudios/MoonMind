"""Tests for moonmind.workflows.skills.run_projection.

Covers the activity/launcher-boundary helpers that materialize a resolved
skill snapshot for a managed run, verify the projection, and prepend the
canonical activation summary defined in ``docs/Steps/SkillSystem.md`` §14.5.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
)
from moonmind.workflows.skills.run_projection import (
    SkillProjectionError,
    build_skill_activation_summary,
    load_resolved_skillset,
    materialize_run_skill_snapshot,
    prepend_skill_activation_summary,
    verify_skill_projection,
)


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _skill_entry(name: str, content_ref: str, payload: bytes) -> ResolvedSkillEntry:
    return ResolvedSkillEntry(
        skill_name=name,
        version="1.0.0",
        content_ref=content_ref,
        content_digest=_digest(payload),
        provenance=AgentSkillProvenance(source_kind=AgentSkillSourceKind.DEPLOYMENT),
    )


class _StaticArtifactService:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool,
    ) -> tuple[object, bytes]:
        del principal, allow_restricted_raw
        return object(), self._payloads[artifact_id]


def _skill_payload(name: str) -> bytes:
    return f"---\nname: {name}\ndescription: test\n---\n".encode("utf-8")


def _resolved_skillset(
    snapshot_id: str,
    skills: list[tuple[str, str]],
    payloads: dict[str, bytes],
) -> ResolvedSkillSet:
    entries: list[ResolvedSkillEntry] = []
    for name, ref in skills:
        entries.append(_skill_entry(name, ref, payloads[ref]))
    return ResolvedSkillSet(
        snapshot_id=snapshot_id,
        resolved_at=datetime.now(tz=UTC),
        skills=entries,
    )


# ---------------------------------------------------------------------------
# materialize_run_skill_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_writes_canonical_layout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace" / "repo"
    workspace.mkdir(parents=True)
    run_root = tmp_path / "workspace"

    payloads = {"art-pr-resolver": _skill_payload("pr-resolver")}
    skillset = _resolved_skillset(
        "snap-aaa", [("pr-resolver", "art-pr-resolver")], payloads
    )

    metadata = await materialize_run_skill_snapshot(
        workspace_path=workspace,
        run_root=run_root,
        runtime_id="claude_code",
        resolved_skillset=skillset,
        artifact_service=_StaticArtifactService(payloads),
    )

    backing_dir = run_root / "runtime" / "skills_active" / "snap-aaa"
    assert backing_dir.is_dir()
    assert (backing_dir / "_manifest.json").is_file()
    assert (backing_dir / "pr-resolver" / "SKILL.md").is_file()
    visible = Path(metadata["visiblePath"])
    assert (visible / "pr-resolver" / "SKILL.md").is_file()
    assert metadata["canonicalAliasAvailable"] is True
    manifest = json.loads((backing_dir / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == "snap-aaa"
    assert {entry["name"] for entry in manifest["skills"]} == {"pr-resolver"}


@pytest.mark.asyncio
async def test_materialize_only_includes_selected_skills(tmp_path: Path) -> None:
    workspace = tmp_path / "ws" / "repo"
    workspace.mkdir(parents=True)
    payloads = {
        "art-alpha": _skill_payload("alpha"),
        "art-beta": _skill_payload("beta"),
    }
    skillset = _resolved_skillset(
        "snap-multi",
        [("alpha", "art-alpha"), ("beta", "art-beta")],
        payloads,
    )

    metadata = await materialize_run_skill_snapshot(
        workspace_path=workspace,
        run_root=workspace.parent,
        runtime_id="claude_code",
        resolved_skillset=skillset,
        artifact_service=_StaticArtifactService(payloads),
    )

    visible = Path(metadata["visiblePath"])
    assert sorted(p.name for p in visible.iterdir()) == [
        "_manifest.json",
        "alpha",
        "beta",
    ]


@pytest.mark.asyncio
async def test_materialize_does_not_rewrite_repo_authored_skills_dir(
    tmp_path: Path,
) -> None:
    """Spec 314 (MM-608) noninterference: repo-authored ``.agents/skills`` is preserved."""

    workspace = tmp_path / "ws" / "repo"
    workspace.mkdir(parents=True)
    repo_authored = workspace / ".agents" / "skills" / "repo-only"
    repo_authored.mkdir(parents=True)
    (repo_authored / "SKILL.md").write_text(
        "---\nname: repo-only\n---\n", encoding="utf-8"
    )

    payloads = {"art-pr": _skill_payload("pr-resolver")}
    skillset = _resolved_skillset(
        "snap-noninterference", [("pr-resolver", "art-pr")], payloads
    )

    metadata = await materialize_run_skill_snapshot(
        workspace_path=workspace,
        run_root=workspace.parent,
        runtime_id="claude_code",
        resolved_skillset=skillset,
        artifact_service=_StaticArtifactService(payloads),
    )

    repo_skills_dir = workspace / ".agents" / "skills"
    assert repo_skills_dir.is_dir()
    assert not repo_skills_dir.is_symlink()
    assert (repo_skills_dir / "repo-only" / "SKILL.md").is_file()
    visible = Path(metadata["visiblePath"])
    assert visible != repo_skills_dir
    assert (visible / "pr-resolver" / "SKILL.md").is_file()
    assert metadata["canonicalAliasAvailable"] is False
    assert metadata["repoSkillSourcePreserved"] is True


@pytest.mark.asyncio
async def test_materialize_raises_skill_projection_error_on_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws" / "repo"
    workspace.mkdir(parents=True)
    payloads = {"art-pr": _skill_payload("pr-resolver")}
    skillset = _resolved_skillset(
        "snap-bad", [("pr-resolver", "art-pr")], payloads
    )

    # An artifact service that always raises simulates an unreachable backing
    # store. The helper must convert it into a typed SkillProjectionError so
    # the activity can surface a skill_projection_failed classification.
    class _BrokenArtifactService:
        async def read(
            self,
            *,
            artifact_id: str,
            principal: str,
            allow_restricted_raw: bool,
        ) -> tuple[object, bytes]:
            del artifact_id, principal, allow_restricted_raw
            raise OSError("artifact backing store unavailable")

    with pytest.raises(SkillProjectionError):
        await materialize_run_skill_snapshot(
            workspace_path=workspace,
            run_root=workspace.parent,
            runtime_id="claude_code",
            resolved_skillset=skillset,
            artifact_service=_BrokenArtifactService(),
        )


# ---------------------------------------------------------------------------
# verify_skill_projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_accepts_valid_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "ws" / "repo"
    workspace.mkdir(parents=True)
    payloads = {"art-pr": _skill_payload("pr-resolver")}
    skillset = _resolved_skillset("snap-ok", [("pr-resolver", "art-pr")], payloads)

    metadata = await materialize_run_skill_snapshot(
        workspace_path=workspace,
        run_root=workspace.parent,
        runtime_id="claude_code",
        resolved_skillset=skillset,
        artifact_service=_StaticArtifactService(payloads),
    )

    verify_skill_projection(
        materialization_metadata=metadata,
        resolved_skillset=skillset,
        selected_skill="pr-resolver",
    )


def test_verify_raises_when_visible_path_missing(tmp_path: Path) -> None:
    skillset = _resolved_skillset(
        "snap-x", [("pr-resolver", "art-pr")], {"art-pr": _skill_payload("pr-resolver")}
    )
    with pytest.raises(SkillProjectionError, match="visiblePath is missing"):
        verify_skill_projection(
            materialization_metadata={"visiblePath": str(tmp_path / "nope")},
            resolved_skillset=skillset,
        )


def test_verify_raises_on_snapshot_id_mismatch(tmp_path: Path) -> None:
    visible = tmp_path / ".agents" / "skills"
    (visible / "pr-resolver").mkdir(parents=True)
    (visible / "pr-resolver" / "SKILL.md").write_text("x", encoding="utf-8")
    (visible / "_manifest.json").write_text(
        json.dumps(
            {"snapshot_id": "wrong-id", "skills": [{"name": "pr-resolver"}]}
        ),
        encoding="utf-8",
    )
    skillset = _resolved_skillset(
        "expected-id",
        [("pr-resolver", "art-pr")],
        {"art-pr": _skill_payload("pr-resolver")},
    )
    with pytest.raises(SkillProjectionError, match="snapshot_id does not match"):
        verify_skill_projection(
            materialization_metadata={"visiblePath": str(visible)},
            resolved_skillset=skillset,
        )


def test_verify_raises_when_selected_skill_doc_missing(tmp_path: Path) -> None:
    visible = tmp_path / ".agents" / "skills"
    visible.mkdir(parents=True)
    (visible / "_manifest.json").write_text(
        json.dumps(
            {"snapshot_id": "snap-x", "skills": [{"name": "pr-resolver"}]}
        ),
        encoding="utf-8",
    )
    skillset = _resolved_skillset(
        "snap-x",
        [("pr-resolver", "art-pr")],
        {"art-pr": _skill_payload("pr-resolver")},
    )
    with pytest.raises(SkillProjectionError, match="missing"):
        verify_skill_projection(
            materialization_metadata={"visiblePath": str(visible)},
            resolved_skillset=skillset,
            selected_skill="pr-resolver",
        )


def test_verify_raises_when_manifest_missing_expected_skill(tmp_path: Path) -> None:
    visible = tmp_path / ".agents" / "skills"
    (visible / "alpha").mkdir(parents=True)
    (visible / "alpha" / "SKILL.md").write_text("x", encoding="utf-8")
    (visible / "_manifest.json").write_text(
        json.dumps({"snapshot_id": "snap-y", "skills": [{"name": "alpha"}]}),
        encoding="utf-8",
    )
    skillset = _resolved_skillset(
        "snap-y",
        [("alpha", "art-a"), ("beta", "art-b")],
        {"art-a": _skill_payload("alpha"), "art-b": _skill_payload("beta")},
    )
    with pytest.raises(SkillProjectionError, match="missing expected skills"):
        verify_skill_projection(
            materialization_metadata={"visiblePath": str(visible)},
            resolved_skillset=skillset,
        )


# ---------------------------------------------------------------------------
# build_skill_activation_summary / prepend_skill_activation_summary
# ---------------------------------------------------------------------------


def test_activation_summary_empty_without_selected_skill() -> None:
    summary = build_skill_activation_summary(
        parameters={"selectedSkill": ""},
        materialization_metadata={"visiblePath": "/work/skills"},
        skills_on_demand_enabled=False,
    )
    assert summary == ""


def test_activation_summary_empty_without_metadata() -> None:
    summary = build_skill_activation_summary(
        parameters={"selectedSkill": "pr-resolver"},
        materialization_metadata=None,
        skills_on_demand_enabled=False,
    )
    assert summary == ""


def test_activation_summary_includes_required_fields() -> None:
    summary = build_skill_activation_summary(
        parameters={"selectedSkill": "pr-resolver"},
        materialization_metadata={
            "visiblePath": "/work/agent_jobs/workspaces/r1/runtime/skills_active/snap-1",
            "canonicalAliasAvailable": True,
        },
        skills_on_demand_enabled=False,
    )
    assert "Active MoonMind skill snapshot:" in summary
    assert "Selected skill: pr-resolver" in summary
    assert "Full active MoonMind skill content is available at:" in summary
    assert "/work/agent_jobs/workspaces/r1/runtime/skills_active/snap-1/pr-resolver/SKILL.md" in summary


def test_activation_summary_warns_when_alias_unavailable() -> None:
    summary = build_skill_activation_summary(
        parameters={"selectedSkill": "pr-resolver"},
        materialization_metadata={
            "visiblePath": "/work/skills/snap",
            "canonicalAliasAvailable": False,
        },
        skills_on_demand_enabled=False,
    )
    assert "repo-authored source and must not be modified" in summary


def test_prepend_is_idempotent() -> None:
    metadata = {"visiblePath": "/work/skills/snap", "canonicalAliasAvailable": True}
    once = prepend_skill_activation_summary(
        "do the thing",
        parameters={"selectedSkill": "pr-resolver"},
        materialization_metadata=metadata,
        skills_on_demand_enabled=False,
    )
    twice = prepend_skill_activation_summary(
        once,
        parameters={"selectedSkill": "pr-resolver"},
        materialization_metadata=metadata,
        skills_on_demand_enabled=False,
    )
    assert once == twice


def test_prepend_returns_unchanged_when_no_skill() -> None:
    out = prepend_skill_activation_summary(
        "task",
        parameters={"selectedSkill": "auto"},
        materialization_metadata={"visiblePath": "/x"},
        skills_on_demand_enabled=False,
    )
    assert out == "task"


# ---------------------------------------------------------------------------
# load_resolved_skillset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_resolved_skillset_round_trips() -> None:
    skillset = _resolved_skillset(
        "snap-load",
        [("pr-resolver", "art-pr")],
        {"art-pr": _skill_payload("pr-resolver")},
    )
    payloads = {"snap-ref": skillset.model_dump_json().encode("utf-8")}
    loaded = await load_resolved_skillset(_StaticArtifactService(payloads), "snap-ref")
    assert loaded.snapshot_id == "snap-load"
    assert [entry.skill_name for entry in loaded.skills] == ["pr-resolver"]


@pytest.mark.asyncio
async def test_load_resolved_skillset_raises_on_missing_service() -> None:
    with pytest.raises(SkillProjectionError, match="artifact service is required"):
        await load_resolved_skillset(None, "any-ref")


@pytest.mark.asyncio
async def test_load_resolved_skillset_raises_on_invalid_payload() -> None:
    with pytest.raises(SkillProjectionError, match="failed to read"):
        await load_resolved_skillset(
            _StaticArtifactService({"bad": b"not-json"}), "bad"
        )
