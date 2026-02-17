"""Unit tests for run-scoped skills selection resolution."""

from __future__ import annotations

import pytest

from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    list_available_skill_names,
    resolve_run_skill_selection,
)


@pytest.fixture
def skills_mirror(tmp_path, monkeypatch):
    mirror = tmp_path / "skills"
    legacy = tmp_path / "legacy"
    mirror.mkdir(parents=True)
    legacy.mkdir(parents=True)

    for root, name in ((mirror, "speckit"), (mirror, "docs-lint"), (legacy, "legacy")):
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skills_local_mirror_root",
        str(mirror),
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skills_legacy_mirror_root",
        str(legacy),
        raising=False,
    )
    return mirror, legacy


def test_resolve_run_skill_selection_uses_global_defaults(skills_mirror, monkeypatch):
    mirror, _ = skills_mirror
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skill_policy_mode",
        "allowlist",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.allowed_skills",
        ("speckit",),
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.default_skill",
        "speckit",
        raising=False,
    )

    resolved = resolve_run_skill_selection(run_id="run-1", context={})

    assert resolved.selection_source == "global_default"
    assert [skill.skill_name for skill in resolved.skills] == ["speckit"]
    assert resolved.skills[0].source_uri == (mirror / "speckit").resolve().as_uri()


def test_resolve_run_skill_selection_permissive_mode_discovers_local_skills(
    skills_mirror, monkeypatch
):
    mirror, legacy = skills_mirror
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skill_policy_mode",
        "permissive",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.allowed_skills",
        ("speckit",),
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.default_skill",
        "speckit",
        raising=False,
    )

    resolved = resolve_run_skill_selection(run_id="run-1b", context={})

    assert resolved.selection_source == "global_default"
    assert [skill.skill_name for skill in resolved.skills] == [
        "speckit",
        "docs-lint",
        "legacy",
    ]
    assert resolved.skills[0].source_uri == (mirror / "speckit").resolve().as_uri()
    assert resolved.skills[1].source_uri == (mirror / "docs-lint").resolve().as_uri()
    assert resolved.skills[2].source_uri == (legacy / "legacy").resolve().as_uri()


def test_resolve_run_skill_selection_prefers_job_override(skills_mirror):
    resolved = resolve_run_skill_selection(
        run_id="run-2",
        context={
            "skill_selection": ["docs-lint:1.2.0"],
            "queue_skill_selection": ["speckit:1.0.0"],
            "skill_sources": {"docs-lint:1.2.0": "file:///tmp/custom/docs-lint"},
        },
    )

    assert resolved.selection_source == "job_override"
    assert [skill.skill_name for skill in resolved.skills] == ["docs-lint"]
    assert resolved.skills[0].version == "1.2.0"
    assert resolved.skills[0].source_uri == "file:///tmp/custom/docs-lint"


def test_resolve_run_skill_selection_rejects_duplicates(skills_mirror):
    with pytest.raises(SkillResolutionError, match="Duplicate skill name"):
        resolve_run_skill_selection(
            run_id="run-3",
            context={"skill_selection": ["speckit:1", "speckit:2"]},
        )


@pytest.mark.parametrize("skill_name", ["../evil", "a/b", "a\\b", "..", "bad name"])
def test_resolve_run_skill_selection_rejects_unsafe_names(skills_mirror, skill_name):
    with pytest.raises(SkillResolutionError, match="Invalid skill name"):
        resolve_run_skill_selection(
            run_id="run-invalid",
            context={"skill_selection": [f"{skill_name}:1.0.0"]},
        )


def test_resolve_run_skill_selection_falls_back_to_legacy_root(skills_mirror):
    _, legacy = skills_mirror
    resolved = resolve_run_skill_selection(
        run_id="run-4",
        context={"skill_selection": ["legacy:0.1.0"]},
    )

    assert resolved.skills[0].source_uri == (legacy / "legacy").resolve().as_uri()


def test_resolve_run_skill_selection_requires_source(monkeypatch, tmp_path):
    empty_root = tmp_path / "empty"
    empty_root.mkdir(parents=True)

    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skills_local_mirror_root",
        str(empty_root),
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skills_legacy_mirror_root",
        str(empty_root),
        raising=False,
    )

    with pytest.raises(SkillResolutionError, match="No source URI resolved"):
        resolve_run_skill_selection(
            run_id="run-5",
            context={"skill_selection": ["missing:1.0.0"]},
        )


def test_list_available_skill_names_permissive_mode_discovers_local_roots(
    skills_mirror, monkeypatch
):
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skill_policy_mode",
        "permissive",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.default_skill",
        "speckit",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.allowed_skills",
        ("speckit",),
        raising=False,
    )

    assert list_available_skill_names() == ("speckit", "docs-lint", "legacy")


def test_list_available_skill_names_allowlist_filters_unlisted_local_skills(
    skills_mirror, monkeypatch
):
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skill_policy_mode",
        "allowlist",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.default_skill",
        "speckit",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.allowed_skills",
        ("speckit", "legacy"),
        raising=False,
    )

    assert list_available_skill_names() == ("speckit", "legacy")


def test_list_available_skill_names_includes_builtin_speckit_without_local_mirror(
    monkeypatch, tmp_path
):
    empty_root = tmp_path / "empty"
    empty_root.mkdir(parents=True)

    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skills_local_mirror_root",
        str(empty_root),
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skills_legacy_mirror_root",
        str(empty_root),
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.skill_policy_mode",
        "permissive",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.default_skill",
        "speckit",
        raising=False,
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.spec_workflow.allowed_skills",
        (),
        raising=False,
    )

    assert list_available_skill_names() == ("speckit",)
