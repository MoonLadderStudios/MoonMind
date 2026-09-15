"""MoonLadderStudios/MoonMind#4271: documentation semantics + explicit health skills.

Health-update expansion must select the two dedicated Skills with correctly
typed inputs and a readable report handoff, not `auto` plus duplicated
procedure. Review stays read-only; remediation fixes only authorized,
still-valid findings with a verified no-op on empty findings.
"""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import ExpandOptions, PresetCatalogService

pytestmark = [pytest.mark.asyncio]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PRESET_DIR = _REPO_ROOT / "api_service" / "data" / "presets"
_SKILLS_DIR = _REPO_ROOT / ".agents" / "skills"


@asynccontextmanager
async def _catalog_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/doc_health_4271.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield sessions
    finally:
        await engine.dispose()


def _seed_dir(tmp_path) -> Path:
    seed_dir = tmp_path / "presets"
    shutil.copytree(_PRESET_DIR, seed_dir)
    return seed_dir


def _seed_yaml(slug: str) -> dict:
    return yaml.safe_load((_PRESET_DIR / f"{slug}.yaml").read_text(encoding="utf-8"))


def _skill_text(name: str) -> str:
    return (_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


async def _expand(slug: str, tmp_path, inputs: dict):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            return await service.expand_template(
                slug=slug,
                scope="global",
                scope_ref=None,
                inputs=inputs,
                context={"repository": "o/r", "branch": "main", "targetRuntime": "codex_cli"},
                options=ExpandOptions(should_enforce_step_limit=True),
            )


def test_health_update_seed_selects_explicit_skills_with_typed_inputs():
    seed = _seed_yaml("document-health-update")
    assert [s["title"] for s in seed["steps"]] == [
        "Document health review",
        "Document health remediate",
    ]
    review_skill = seed["steps"][0]["skill"]
    remediate_skill = seed["steps"][1]["skill"]
    # Not `auto` plus duplicated procedure.
    assert review_skill["id"] == "document-health-review"
    assert remediate_skill["id"] == "document-health-remediate"
    assert "target_scope" in str(review_skill["args"])
    assert "report_path" in str(review_skill["args"])
    assert "output_mode" in str(review_skill["args"])
    assert "report_path" in str(remediate_skill["args"])
    assert "allow_destructive" in str(remediate_skill["args"])
    # No copied review/remediation procedure in preset prose.
    assert "drift or factual inaccuracy" not in seed["steps"][0]["instructions"]
    assert "redo review work" not in seed["steps"][1]["instructions"]


def test_health_update_seed_declares_tested_input_contract():
    seed = _seed_yaml("document-health-update")
    names = {i["name"] for i in seed["inputs"]}
    assert {
        "documentation_scope",
        "report_path",
        "review_output_mode",
        "allowed_actions",
        "allow_destructive",
        "constraints",
    } <= names


async def test_health_update_expansion_carries_report_handoff(tmp_path):
    expanded = await _expand(
        "document-health-update",
        tmp_path,
        {
            "documentation_scope": "docs/",
            "report_path": "artifacts/document-health-review.json",
            "review_output_mode": "full_report",
            "allowed_actions": "",
            "allow_destructive": False,
            "constraints": "",
        },
    )
    assert len(expanded["steps"]) == 2
    review, remediate = expanded["steps"]
    assert review["skill"]["id"] == "document-health-review"
    assert remediate["skill"]["id"] == "document-health-remediate"
    assert "artifacts/document-health-review.json" in review["instructions"]
    assert "artifacts/document-health-review.json" in remediate["instructions"]
    assert "verified no-op" in remediate["instructions"]
    assert "still valid" in remediate["instructions"]


def test_review_skill_is_read_only_with_typed_contract():
    text = _skill_text("document-health-review")
    assert "Review-only, always" in text
    assert "remediation owns authorized edits" in text
    assert "target_scope" in text
    assert "report_path" in text
    assert "output_mode" in text


def test_review_skill_resolves_role_first_and_size_as_signal():
    text = _skill_text("document-health-review")
    assert "Document Role First" in text
    assert "Factual implementation reference" in text
    assert "Authorized desired-state design" in text
    assert "Temporary execution artifact" in text
    assert "investigation signal" in text
    assert "large file alone does not force" in text.lower()


def test_review_skill_reconciles_ownership_ban_with_authority_checks():
    text = _skill_text("document-health-review")
    assert "bounded exception" in text
    assert "unclear_authority" in text


def test_remediate_skill_revalidates_and_orders_safely():
    text = _skill_text("document-health-remediate")
    assert "stale recommendation is not write authority" in text.lower()
    assert "focused stale-evidence check is required" in text
    assert "safe content/dependency ordering" in text
    assert "update inbound and relative links before removal" in text.lower()
    assert "allow_destructive" in text


def test_remediate_skill_has_explicit_destructive_permission():
    text = _skill_text("document-health-remediate")
    assert "external report alone never authorizes deletion" in text.lower()
    assert "denied mutation is never retried" in text.lower()


def test_document_skills_use_provider_neutral_escalation():
    for name in ("document-health-review", "document-health-remediate", "document-update"):
        text = _skill_text(name)
        assert "provider-neutral" in text.lower(), name
    update_text = _skill_text("document-update")
    assert "structured handoff" in update_text
    assert "missing tracker integration must not force" in update_text.lower()


def test_document_update_preserves_desired_state_and_role():
    text = _skill_text("document-update")
    assert "Document Role First" in text
    assert "Buggy or incomplete code never downgrades" in text
    assert "never be falsely labeled implemented" in text


def test_document_author_discovers_conventions_without_mandatory_detour():
    text = _skill_text("document-author")
    assert "actual conventions" in text
    assert "only the relevant document-class references" in text
    assert "must not itself block" in text
    assert "not replaced with a plan solely because it is substantial" in text


def test_reconcile_gate_stays_truthful():
    text = _skill_text("moonspec-doc-reconcile")
    assert "already carry explicit authority" in text or "explicit authority" in text
    assert "ordinary no-op is `no_update_required`" in text
    assert "verification, escalation, and publication outcomes stay separate" in text


def test_orchestrate_preset_preserves_discovery_and_child_policy():
    seed = _seed_yaml("document-update-orchestrate")
    assert seed["steps"][0]["tool"]["id"] == "document.discover"
    assert seed["steps"][1]["tool"]["id"] == "story.create_document_update_tasks"
    instructions = seed["steps"][1]["instructions"]
    assert "exact discovered document path" in instructions
    assert "inherits" in instructions
    assert "cannot silently write conflicting" in instructions
    assert "not document completion" in instructions
    assert "another scheduler" in instructions
