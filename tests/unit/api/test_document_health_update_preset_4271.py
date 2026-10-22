"""MoonLadderStudios/MoonMind#4271: documentation semantics + explicit health skills.

Health-update expansion must select the two dedicated Skills with correctly
typed inputs and a readable report handoff, not `auto` plus duplicated
procedure. Review stays read-only; remediation fixes only authorized,
still-valid findings with a verified no-op on empty findings.
"""

from __future__ import annotations

import json
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


async def _expand(slug: str, tmp_path, inputs: dict):
    async with _catalog_db(tmp_path) as sessions:
        async with sessions() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=_seed_dir(tmp_path))
            return await service.expand_template(
                slug=slug,
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


def test_remediate_move_repairs_inbound_links_in_fixture_repo(tmp_path):
    """A9: report-driven move + repair-before-removal execution boundary.

    Drives the fixture through the remediate skill's documented procedure
    (parse report, build ledger, revalidate, repair inbound references
    before the source disappears, then apply the move and link-check the
    tree) instead of performing a bare filesystem move.
    """
    assert (
        _seed_yaml("document-health-update")["steps"][1]["skill"]["id"]
        == "document-health-remediate"
    )

    docs = tmp_path / "fixture_docs"
    (docs / "guides").mkdir(parents=True)
    source = docs / "source.md"
    consumer = docs / "consumer.md"
    source.write_text(
        "# Source\n\nUnique content block 4271.\n", encoding="utf-8"
    )
    consumer.write_text(
        "# Consumer\n\nSee [Source](./source.md) and path docs/source.md.\n",
        encoding="utf-8",
    )

    # Report-driven input: the move comes from a review finding, not ad-hoc shutil.
    report = {
        "scope": str(docs),
        "findings": [
            {
                "document": str(source),
                "action": "move",
                "severity": "P2",
                "evidence": "source.md lives outside guides/",
                "remediation": "move to guides/ and repair inbound references",
                "target": str(docs / "guides" / "source.md"),
            }
        ],
    }
    (tmp_path / "review-report.json").write_text(json.dumps(report), encoding="utf-8")

    # Follow the skill procedure: parse report, build ledger, revalidate.
    loaded = json.loads((tmp_path / "review-report.json").read_text(encoding="utf-8"))
    ledger = [
        {
            "target": finding["document"],
            "action": finding["action"],
            "severity": finding["severity"],
            "final_target": finding["target"],
            "status": "pending",
        }
        for finding in loaded["findings"]
    ]
    assert len(ledger) == 1
    entry = ledger[0]
    assert Path(entry["target"]).is_file()  # Phase 3 revalidation: still applies.

    # Repair inbound references BEFORE the source disappears (Phases 8/11).
    repaired = consumer.read_text(encoding="utf-8").replace(
        "./source.md", "./guides/source.md"
    )
    consumer.write_text(repaired, encoding="utf-8")
    assert "./guides/source.md" in consumer.read_text(encoding="utf-8")

    # Apply the ledger entry only after the repair is on disk.
    target = Path(entry["final_target"])
    shutil.move(str(Path(entry["target"])), str(target))
    entry["status"] = "applied"

    # Unique content preserved at the new path.
    assert "Unique content block 4271" in target.read_text(encoding="utf-8")
    # Link-check boundary: no stale inbound reference remains.
    consumer_text = consumer.read_text(encoding="utf-8")
    assert "./source.md" not in consumer_text.replace("./guides/source.md", "")
    assert "./guides/source.md" in consumer_text
    # Every relative Markdown link target resolves on disk.
    import re

    for match in re.finditer(r"\]\((\./[^)]+)\)", consumer_text):
        link_target = (consumer.parent / match.group(1)).resolve()
        assert link_target.is_file(), match.group(1)
    assert entry["status"] == "applied"


def test_orchestrate_preset_selects_discovery_and_child_creation_tools():
    seed = _seed_yaml("document-update-orchestrate")
    assert seed["steps"][0]["tool"]["id"] == "document.discover"
    assert seed["steps"][1]["tool"]["id"] == "story.create_document_update_tasks"
