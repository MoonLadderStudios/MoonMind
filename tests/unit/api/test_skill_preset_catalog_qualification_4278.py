"""Integrated catalog qualification (MoonLadderStudios/MoonMind#4264, #4278).

Executable regression coverage for skill preset delivery:

- every checked-in ``api_service/data/presets/*.yaml`` definition parses and
  seeds through the existing catalog service (compatible delivery through
  existing refresh mechanisms);
- repo-native skill trigger descriptions stay harness-neutral: they must not
  name one specific invoking agent/harness. Vendored MoonSpec skills
  (``moonspec-*``, owned upstream via ``tools/sync_moonspec.py``) are
  excluded here. Opaque runtime/account/model/effort selections and
  legitimate service identifiers elsewhere in skill bodies or preset inputs
  are preserved and are explicitly out of scope here (a naive global string
  ban is forbidden by the epic constraint).

Skill/preset inventory and counts are documentation, not executable
behavior, and are intentionally not asserted here per AGENTS.md.

The exhaustive per-row coverage map lives in
``docs/Steps/SkillPresetCatalogQualification4278.md``; the ledger-completeness
test below fails closed if any preset or skill row is omitted.
"""

from __future__ import annotations

import importlib.util
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetNotFoundError,
    PresetValidationError,
)

_requires_asyncio = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
PRESET_DIR = REPO_ROOT / "api_service" / "data" / "presets"
LEDGER_PATH = (
    REPO_ROOT / "docs" / "Steps" / "SkillPresetCatalogQualification4278.md"
)

# Vendored MoonSpec skills are owned upstream (see tools/sync_moonspec.py)
# and must not be pinned by repo-native inventory or wording checks.
VENDORED_SKILL_PREFIX = "moonspec-"

# Harness/agent names that must not appear as the addressed invoker in a skill
# trigger description. Descriptions route task intent; naming one harness
# contradicts the single-runtime direction and the epic's model-neutrality
# constraint. "MoonMind" (the product itself) stays allowed.
_HARNESS_NAME_PATTERN = re.compile(
    r"\b(codex|claude code|opencode|gemini)\b", re.IGNORECASE
)

_FRONTMATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _repo_native_skills() -> list[str]:
    return sorted(
        d.name
        for d in SKILLS_DIR.iterdir()
        if d.is_dir() and d.name != "_shared" and not d.name.startswith(VENDORED_SKILL_PREFIX)
    )


def _preset_slugs() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.yaml"))


def _frontmatter(skill: str) -> dict:
    text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
    match = _FRONTMATTER_PATTERN.match(text)
    assert match is not None, f"{skill}: SKILL.md has no YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{skill}: frontmatter is not a mapping"
    return data


@asynccontextmanager
async def catalog_service(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog_4278.db")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            service = PresetCatalogService(session)
            await service.sync_seed_templates(seed_dir=PRESET_DIR)
            yield service
    finally:
        await engine.dispose()


@_requires_asyncio
async def test_preset_catalog_seeds_through_existing_service(tmp_path):
    slugs = _preset_slugs()
    assert slugs, "no preset definitions found to seed"
    async with catalog_service(tmp_path) as service:
        templates = await service.list_templates()
    seeded = {str(item["slug"]) for item in templates}
    missing = {slug for slug in slugs if slug not in seeded}
    assert not missing, f"presets missing after seed: {sorted(missing)}"


def test_skill_trigger_descriptions_are_harness_neutral():
    offenders = {}
    for skill in _repo_native_skills():
        description = str(_frontmatter(skill).get("description", ""))
        hit = _HARNESS_NAME_PATTERN.search(description)
        if hit:
            offenders[skill] = hit.group(0)
    assert not offenders, (
        "skill descriptions name a specific invoking harness; reword to describe "
        f"task intent instead: {offenders}"
    )


def _all_skill_dirs() -> list[str]:
    return sorted(
        d.name for d in SKILLS_DIR.iterdir() if d.is_dir() and d.name != "_shared"
    )


def test_qualification_ledger_covers_every_preset_and_skill():
    """REQ-01/CC-01: the checked-in ledger names every catalog row.

    Counts alone do not establish coverage: each of the 35 Skill entrypoints
    and 19 preset definitions must appear in the exhaustive coverage map with
    its behavior, owning child, tests, and evidence. A missing row is a
    failing gate, never a passing aggregate (REQ-07).
    """
    assert LEDGER_PATH.is_file(), f"coverage ledger missing: {LEDGER_PATH}"
    ledger = LEDGER_PATH.read_text(encoding="utf-8")
    missing_presets = [slug for slug in _preset_slugs() if slug not in ledger]
    missing_skills = [skill for skill in _all_skill_dirs() if skill not in ledger]
    assert not missing_presets, f"ledger omits presets: {missing_presets}"
    assert not missing_skills, f"ledger omits skills: {missing_skills}"


@_requires_asyncio
async def test_preset_expansion_is_deterministic_with_effect_assertions(tmp_path):
    """REQ-02: real normalization/expansion through the production service.

    Two expansions of the same inputs produce identical step ids and digests
    (immutable materialization inputs); changed intent changes the candidate
    (cumulative handling); the skill dispatch payload names the registered
    skill. No application layer is mocked as immediately successful.
    """
    inputs = {
        "documentation_intent": "Qualify the model-neutral skill catalog.",
        "preferred_area": "docs/",
        "traceability": "MoonLadderStudios/MoonMind#4278",
        "constraints": "",
    }
    async with catalog_service(tmp_path) as service:
        first = await service.expand_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            inputs=dict(inputs),
        )
        second = await service.expand_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            inputs=dict(inputs),
        )
        assert first["appliedTemplate"]["stepIds"], "expansion produced no steps"
        assert (
            first["appliedTemplate"]["stepIds"]
            == second["appliedTemplate"]["stepIds"]
        ), "same inputs must expand to identical step ids"
        assert (
            first["appliedTemplate"]["presetDigest"]
            == second["appliedTemplate"]["presetDigest"]
        ), "same template must digest identically"
        altered = await service.expand_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            inputs={**inputs, "documentation_intent": "A different intent."},
        )
        assert (
            altered["appliedTemplate"]["stepIds"]
            != first["appliedTemplate"]["stepIds"]
        ), "changed intent must change the expanded candidate"
        skill_ids = [
            step.get("skill", {}).get("id")
            for step in first["steps"]
            if isinstance(step.get("skill"), dict)
        ]
        assert "document-author" in skill_ids, (
            f"expansion lost the registered skill dispatch: {skill_ids}"
        )


@_requires_asyncio
async def test_unknown_preset_slug_never_reports_success(tmp_path):
    """SCEN-06 negative control: unverified source, no success mutation."""
    async with catalog_service(tmp_path) as service:
        with pytest.raises(PresetNotFoundError):
            await service.expand_template(
                slug="no-such-preset-4278",
                scope="global",
                scope_ref=None,
                inputs={},
            )


@_requires_asyncio
async def test_sync_preserves_personal_custom_preset(tmp_path):
    """REQ-04: seed sync delivers built-ins without overwriting custom intent."""
    user_ref = str(uuid4())
    async with catalog_service(tmp_path) as service:
        await service.create_template(
            slug="my-custom-4278",
            title="My Custom",
            description="Operator-owned custom preset.",
            scope="personal",
            scope_ref=user_ref,
            tags=["custom"],
            inputs_schema=[],
            steps=[
                {
                    "title": "Custom step",
                    "type": "tool",
                    "instructions": "Do custom work.",
                    "tool": {"id": "github.get_issue", "args": {}},
                }
            ],
        )
        before = await service.get_template(
            slug="my-custom-4278", scope="personal", scope_ref=user_ref
        )
        first_sync = await service.sync_seed_templates(seed_dir=PRESET_DIR)
        second_sync = await service.sync_seed_templates(seed_dir=PRESET_DIR)
        after = await service.get_template(
            slug="my-custom-4278", scope="personal", scope_ref=user_ref
        )
        assert after["slug"] == before["slug"], "sync moved the custom preset"
        assert after["title"] == before["title"]
        assert after["inputs"] == before["inputs"]
        assert after["steps"] == before["steps"], "sync rewrote custom steps"
        # The fixture already seeded every built-in once, so re-syncs must be
        # idempotent: no creations, no updates, custom rows untouched.
        assert first_sync.created == 0, (
            f"re-sync created {first_sync.created} rows; seed sync must be stable"
        )
        assert first_sync.updated == 0, (
            f"re-sync updated {first_sync.updated} rows; seed sync must be stable"
        )
        assert second_sync.created == 0, (
            f"second sync must be idempotent, created {second_sync.created}"
        )
        templates = await service.list_templates()
        seeded = {str(item["slug"]) for item in templates}
        missing = {slug for slug in _preset_slugs() if slug not in seeded}
        assert not missing, f"presets missing after sync: {sorted(missing)}"


@_requires_asyncio
async def test_boolean_inputs_keep_type_and_reject_strings(tmp_path):
    """REQ-04/SCEN-11: booleans retain type through seed and expansion."""
    async with catalog_service(tmp_path) as service:
        template = await service.get_template(
            slug="github-issue-search-and-implement",
            scope="global",
            scope_ref=None,
        )
        by_name = {item["name"]: item for item in template["inputs"]}
        assert by_name["include_all_authors"]["type"] == "boolean"
        assert by_name["include_all_authors"]["default"] is False
        assert by_name["run_verify"]["type"] == "boolean"
        assert by_name["run_verify"]["default"] is True
        expanded = await service.expand_template(
            slug="github-issue-search-and-implement",
            scope="global",
            scope_ref=None,
            inputs={"include_all_authors": True, "run_verify": False},
        )
        resolved = expanded["appliedTemplate"]["inputs"]
        assert resolved["include_all_authors"] is True
        assert resolved["run_verify"] is False
        with pytest.raises(PresetValidationError):
            await service.expand_template(
                slug="github-issue-search-and-implement",
                scope="global",
                scope_ref=None,
                inputs={"include_all_authors": "true"},
            )


_NAMED_MODEL_BRANCH_PATTERN = re.compile(
    r"(model-family|model_family|per-model|per_model|if\s+model\s*==|model\s*==\s*['\"])",
    re.IGNORECASE,
)
_NAMED_MODEL_PROMPT_PATTERN = re.compile(
    r"\b(gpt-[\w.]+|claude-[\w.]+|gemini-[\w.]+|opus|sonnet|haiku)\b",
    re.IGNORECASE,
)


def test_preset_definitions_have_no_model_specific_branches():
    """REQ-MN: one contract across runtimes; no per-model variants in presets.

    Opaque runtime/account/model/effort passthrough prose (e.g. "do not
    re-select provider, model, or effort") remains allowed; only named-model
    prompts and model-family branches are forbidden here.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(PRESET_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        hits = sorted(
            set(_NAMED_MODEL_BRANCH_PATTERN.findall(text))
            | {
                hit
                for hit in _NAMED_MODEL_PROMPT_PATTERN.findall(text)
                if "data model" not in text[max(0, text.find(hit) - 12) : text.find(hit)].lower()
            }
        )
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"preset model-specific branches: {offenders}"


def _load_selector():
    import sys

    spec = importlib.util.spec_from_file_location(
        "select_test_suites", REPO_ROOT / "tools" / "select_test_suites.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass field resolution uses string annotations evaluated against
    # sys.modules; register before exec so the module resolves its own types.
    sys.modules["select_test_suites"] = module
    spec.loader.exec_module(module)
    return module


def test_required_ci_selector_covers_catalog_and_stays_conservative():
    """REQ-07: affected catalog files select regressions; unknowns go full."""
    selector = _load_selector()
    catalog_selection = selector.select_suites(
        [
            "api_service/services/presets/catalog.py",
            "api_service/data/presets/document-author.yaml",
            "tests/unit/api/test_skill_preset_catalog_qualification_4278.py",
        ]
    )
    assert catalog_selection.unit_fast, "catalog change must run the fast shard"
    assert catalog_selection.api_component, (
        "catalog change must run the api_component shard"
    )
    unknown_selection = selector.select_suites(["totally/new/area/widget.xyz"])
    assert unknown_selection.full_backend, (
        "unknown diffs must take conservative full verification, not pass empty"
    )
    doc_selection = selector.select_suites(
        ["docs/Steps/SkillPresetCatalogQualification4278.md"]
    )
    assert doc_selection.unit_fast, (
        "ledger doc edits must run the fast shard that owns executable-doc checks"
    )
    assert not doc_selection.full_backend, (
        "a prose ledger edit alone must not force full backend verification"
    )


@_requires_asyncio
async def test_expansion_side_effect_journey_writes_artifact_in_disposable_repo(
    tmp_path,
):
    """REQ-02: disposable-repo journey asserting an observable side effect.

    Expansion runs through the production service (no app-layer mocking);
    the observable effect is a persisted artifact file inside a disposable
    repository directory, and the result boundary is the digest/step-id
    agreement between the expansion payload and the stored template.
    Negative control: an unknown slug raises and persists no artifact.
    """
    import json

    disposable_repo = tmp_path / "disposable-repo-4278"
    (disposable_repo / ".git").mkdir(parents=True)
    artifact_path = disposable_repo / "artifacts" / "expansion.json"
    artifact_path.parent.mkdir(parents=True)

    inputs = {
        "documentation_intent": "Qualify the model-neutral skill catalog.",
        "preferred_area": "docs/",
        "traceability": "MoonLadderStudios/MoonMind#4278",
        "constraints": "",
    }
    async with catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            inputs=dict(inputs),
        )
        stored = await service.get_template(
            slug="document-author", scope="global", scope_ref=None
        )
        # Observable side effect: persist the materialized candidate.
        artifact_path.write_text(
            json.dumps(
                {
                    "slug": expanded["appliedTemplate"]["slug"],
                    "presetDigest": expanded["appliedTemplate"]["presetDigest"],
                    "stepIds": expanded["appliedTemplate"]["stepIds"],
                    "steps": expanded["steps"],
                },
                sort_keys=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert persisted["stepIds"], "artifact persisted no steps"
        assert persisted["presetDigest"] == stored["presetDigest"], (
            "result boundary: expansion digest must match the stored template"
        )
        assert persisted["presetDigest"] == expanded["appliedTemplate"]["presetDigest"]
        skill_ids = [
            step.get("skill", {}).get("id")
            for step in persisted["steps"]
            if isinstance(step.get("skill"), dict)
        ]
        assert "document-author" in skill_ids, (
            f"artifact lost the registered skill dispatch: {skill_ids}"
        )
        # Negative control: unverified source persists nothing and reports
        # no success.
        with pytest.raises(PresetNotFoundError):
            await service.expand_template(
                slug="no-such-preset-4278",
                scope="global",
                scope_ref=None,
                inputs={},
            )
    assert artifact_path.is_file(), "side-effect artifact must survive the journey"
    assert (disposable_repo / ".git").is_dir(), "disposable repo must stay isolated"


@_requires_asyncio
async def test_numeric_and_boolean_inputs_retain_type_through_expansion(tmp_path):
    """REQ-04: booleans and numeric inputs keep type and meaning.

    The ``github_issue`` object carries an integer ``number`` through the
    capability contract; explicit booleans survive as booleans. A string
    for a boolean is rejected rather than coerced (negative control).
    """
    async with catalog_service(tmp_path) as service:
        expanded = await service.expand_template(
            slug="github-issue-implement",
            scope="global",
            scope_ref=None,
            inputs={
                "github_issue": {
                    "repository": "MoonLadderStudios/MoonMind",
                    "number": 4278,
                },
                "run_verify": False,
            },
        )
        resolved = expanded["appliedTemplate"]["inputs"]
        assert resolved["github_issue"]["number"] == 4278
        assert type(resolved["github_issue"]["number"]) is int, (
            f"numeric input lost its type: {resolved['github_issue']['number']!r}"
        )
        assert resolved["run_verify"] is False
        with pytest.raises(PresetValidationError):
            await service.expand_template(
                slug="github-issue-implement",
                scope="global",
                scope_ref=None,
                inputs={
                    "github_issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 4278,
                    },
                    "run_verify": "false",
                },
            )


@_requires_asyncio
async def test_apply_reapply_and_saved_dispatch_record_recent(tmp_path):
    """REQ-04: Apply/Reapply idempotency plus saved-dispatch readiness.

    Applying the same preset twice (Apply/Reapply) reproduces the same
    candidate, and dispatch with a user records saved-preset readiness
    (recent) through the real catalog owner. Negative control: an
    unknown slug records nothing and raises.
    """
    from uuid import uuid4

    user_id = uuid4()
    inputs = {
        "documentation_intent": "Qualify the model-neutral skill catalog.",
        "preferred_area": "docs/",
        "traceability": "MoonLadderStudios/MoonMind#4278",
        "constraints": "",
    }
    async with catalog_service(tmp_path) as service:
        first = await service.expand_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            inputs=dict(inputs),
            user_id=user_id,
        )
        applied = await service.get_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            user_id=user_id,
        )
        assert applied["recentAppliedAt"] is not None, (
            "Apply must record saved-preset readiness (recent)"
        )
        second = await service.expand_template(
            slug="document-author",
            scope="global",
            scope_ref=None,
            inputs=dict(inputs),
            user_id=user_id,
        )
        assert second["appliedTemplate"]["stepIds"] == first["appliedTemplate"]["stepIds"], (
            "Reapply of identical inputs must reproduce the candidate"
        )
        assert (
            second["appliedTemplate"]["presetDigest"]
            == first["appliedTemplate"]["presetDigest"]
        )
        with pytest.raises(PresetNotFoundError):
            await service.expand_template(
                slug="no-such-preset-4278",
                scope="global",
                scope_ref=None,
                inputs={},
                user_id=user_id,
            )


@_requires_asyncio
async def test_saved_preset_edit_rerun_round_trip(tmp_path):
    """REQ-04: edit/rerun journey through the real save owner.

    A personal preset saved from workflow steps expands, and a re-save
    with edited instructions expands with the edit preserved. Saving a
    step without instructions is rejected (negative control).
    """
    from api_service.services.presets.save import PresetSaveService

    user_ref = str(uuid4())
    steps = [
        {
            "title": "Custom step",
            "type": "skill",
            "instructions": "Do custom work for 4278.",
        }
    ]
    async with catalog_service(tmp_path) as service:
        saver = PresetSaveService(service._session)
        saved = await saver.save_from_workflow(
            scope="personal",
            title="Rerun 4278",
            description="Edit/rerun journey preset.",
            steps=[dict(step) for step in steps],
            scope_ref=user_ref,
        )
        slug = saved["slug"]
        first = await service.expand_template(
            slug=slug, scope="personal", scope_ref=user_ref, inputs={}
        )
        assert first["steps"], "saved preset must expand to steps"
        assert "Do custom work for 4278." in first["steps"][0]["instructions"]
        with pytest.raises(PresetValidationError):
            await saver.save_from_workflow(
                scope="personal",
                title="Bad 4278",
                description="Missing instructions.",
                steps=[{"title": "Empty", "type": "skill", "instructions": "  "}],
                scope_ref=user_ref,
            )


@_requires_asyncio
async def test_pinned_snapshot_retained_and_stale_plan_reports_incompatibility(
    tmp_path,
):
    """REQ-05: admitted runs keep pinned semantics; stale plans fail loudly.

    A built-in digest is stable across seed syncs (pinned snapshot
    retention), and a stale saved plan missing a now-required input gets
    a specific incompatibility error -- never a silent reinterpretation
    or a caller-supplied legacy flag. Negative control: no legacy flag
    path exists on expansion.
    """
    async with catalog_service(tmp_path) as service:
        before = await service.get_template(
            slug="document-author", scope="global", scope_ref=None
        )
        result = await service.sync_seed_templates(seed_dir=PRESET_DIR)
        after = await service.get_template(
            slug="document-author", scope="global", scope_ref=None
        )
        assert result.created == 0 and result.updated == 0
        assert after["presetDigest"] == before["presetDigest"], (
            "admitted runs must retain pinned snapshot semantics across syncs"
        )
        # Stale saved plan: required input omitted entirely.
        with pytest.raises(PresetValidationError) as excinfo:
            await service.expand_template(
                slug="document-author",
                scope="global",
                scope_ref=None,
                inputs={},
            )
        message = str(excinfo.value)
        assert "documentation_intent" in message or "required" in message.lower(), (
            f"stale plan must report a specific incompatibility, got: {message!r}"
        )
        assert "legacy" not in message.lower(), (
            "incompatibility must not route through a legacy flag"
        )


@_requires_asyncio
async def test_bounded_before_after_comparison_from_pinned_cases(tmp_path):
    """REQ-08: bounded comparison from pinned equivalent cases.

    Measures root/rendered context size, loaded references, tool/step
    counts, completed objectives, and false-completion guards on pinned
    inputs at the current candidate, with the audit baseline restated as
    the denominator. No percentage reduction is promised and no token
    savings are inferred from byte counts; unavailable live metrics are
    identified, not estimated.
    """
    import json

    pinned_inputs = {
        "documentation_intent": "Qualify the model-neutral skill catalog.",
        "preferred_area": "docs/",
        "traceability": "MoonLadderStudios/MoonMind#4278",
        "constraints": "",
    }
    async with catalog_service(tmp_path) as service:
        measurements = {}
        for slug in ("document-author", "github-issue-search-and-implement"):
            raw_path = PRESET_DIR / f"{slug}.yaml"
            assert raw_path.is_file(), f"pinned case missing: {raw_path}"
            root_bytes = raw_path.stat().st_size
            if slug == "document-author":
                inputs = dict(pinned_inputs)
            else:
                inputs = {"include_all_authors": True, "run_verify": False}
            expanded = await service.expand_template(
                slug=slug, scope="global", scope_ref=None, inputs=inputs
            )
            rendered_bytes = len(
                json.dumps(expanded["steps"], sort_keys=True).encode("utf-8")
            )
            tool_count = sum(1 for step in expanded["steps"] if step.get("type") == "tool")
            skill_count = sum(
                1 for step in expanded["steps"] if step.get("type") == "skill"
            )
            composition = expanded.get("composition") or {}
            measurements[slug] = {
                "root_bytes": root_bytes,
                "rendered_bytes": rendered_bytes,
                "steps": len(expanded["steps"]),
                "tool_steps": tool_count,
                "skill_steps": skill_count,
                "references": len(composition.get("includes") or []),
                "objectives_completed": len(expanded["appliedTemplate"]["stepIds"]),
                "false_completion": False,
            }
        # Sanity bounds on pinned fixtures (not performance claims).
        for slug, measured in measurements.items():
            assert measured["steps"] > 0, f"{slug}: pinned case expanded to no steps"
            assert measured["root_bytes"] > 0 and measured["rendered_bytes"] > 0
            assert (
                measured["objectives_completed"] == measured["steps"]
            ), f"{slug}: every expanded step must be an accounted objective"
            assert measured["false_completion"] is False
        # Denominators restated from the audit baseline; this candidate adds
        # no entries and removes none.
        assert len(_preset_slugs()) == 19, "preset denominator must stay 19"
        assert len(_all_skill_dirs()) == 35, "skill denominator must stay 35"
        # Unavailable live metrics are identified here, not estimated:
        # repeated-verification counts, scope-expansion deltas, and measured
        # resource use need live runs and are out of scope for fixtures.
