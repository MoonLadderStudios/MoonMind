# Skill/Preset Catalog Qualification Ledger (MoonLadderStudios/MoonMind#4278)

Parent epic: #4264 (integration package 13, final qualification gate).
This ledger is the exhaustive coverage map required by #4278 REQ-01: one row
per catalog entry with changed-or-retained behavior, implementing child,
tests, and evidence at the current candidate.

Scope and honesty notes (read first):

- The source audit covered 35 Skill entrypoints and 19 preset YAMLs at
  `9bca7c7527cf33fd37b1400e2f60b43b1924ec03`. This ledger rebaselines by
  enumerating the catalog at the current candidate: 35 `SKILL.md` entrypoints
  under `.agents/skills/` (25 repo-native + 10 vendored `moonspec-*`, owned
  upstream via `tools/sync_moonspec.py`) and 19 YAMLs under
  `api_service/data/presets/`. Counts match the audit; counts alone do not
  establish coverage, so every row below carries behavior and evidence.
- "Retained" means the row was inspected at the candidate and needs no
  rewrite: already-correct entries are intentionally not rewritten (#4278
  required work, item 1). "Qualified" means deterministic regression tests in
  `tests/unit/api/test_skill_preset_catalog_qualification_4278.py` exercise the
  row through production boundaries (seed, expansion, sync, typed defaults,
  neutrality, CI selector).
- Feature children own their targeted behavior changes and tests. This ledger
  owns the complete mapping, cross-boundary regression integration, catalog
  delivery, and final evidence. Rows whose full end-to-end proof needs live
  deployments, paid inference, or Temporal infrastructure say so explicitly in
  the Evidence column instead of claiming it.
- Model-neutral contract (#4278 model-neutral requirement): one
  task/authority/acceptance contract across runtime and capability
  combinations. No named-model prompt, model-family/version branch, per-model
  procedural variant, or model-specific effort tuning exists in the delivered
  catalog changes. Selected runtime/account/model/effort/billing settings stay
  opaque inputs. Real hosting-service protocols and capability adapters remain
  valid. Required regression CI depends on no live model or paid inference.
- Before/after comparison (REQ-08, bounded): audit baseline 35 skills / 19
  presets; candidate 35 skills / 19 presets (no additions or removals in this
  step). No percentage reduction is promised and no token savings are inferred
  from byte counts. Deterministic contract fixtures are reported as fixtures,
  not as measured agent performance. Failed trials, estimates, and unavailable
  metrics are identified in the scenario matrix and limitations sections.

## Rebaseline commands

```bash
find .agents/skills -maxdepth 2 -name "SKILL.md" | wc -l   # expect 35
ls api_service/data/presets/*.yaml | wc -l                 # expect 19
moonmind container python-tests \
  tests/unit/api/test_skill_preset_catalog_qualification_4278.py
printf '%s\n' api_service/services/presets/catalog.py \
  api_service/data/presets/document-author.yaml \
  tests/unit/api/test_skill_preset_catalog_qualification_4278.py \
  docs/Steps/SkillPresetCatalogQualification4278.md \
  | python3 tools/select_test_suites.py
```

## Preset coverage (19 rows)

| Preset slug | Behavior at candidate | Implementing child | Tests | Evidence |
| --- | --- | --- | --- | --- |
| batch-github-workflows | Retained; boolean/numeric inputs keep declared types through seed and expansion | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, sync-idempotency, sync-preservation, boolean-typing, neutrality, selector) | Seed round-trip + idempotent re-sync; custom personal preset untouched; boolean strictness proven on `github-issue-search-and-implement` sharing the same input-type owner |
| batch-workflows | Retained; same typed-input contract as above | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (same set) | Same as above; no per-model branch found in definition |
| document-author | Retained leaf preset; expansion is deterministic per inputs and preserves the registered skill dispatch | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (expansion determinism + skill-dispatch assertion, seed, neutrality, selector) | Same inputs expand to identical step ids and digest; changed intent changes step ids; `skill.id: document-author` present in expanded steps |
| document-health-update | Retained; composes review skill, no catalog-shape change | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through `PresetCatalogService.sync_seed_templates`; no named-model branch |
| document-update-orchestrate | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| github-issue-breakdown-implement | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| github-issue-breakdown-orchestrate | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| github-issue-implement | Retained composition preset (`kind: include`); include targets resolve from the seeded catalog | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | All include targets present after seed; expansion boundary proven on leaf + boolean presets sharing the same `_expand_preset_steps` owner |
| github-issue-orchestrate | Retained composition preset (`kind: include`) | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Same composition evidence as above |
| github-issue-search-and-implement | Retained; boolean inputs (`include_all_authors=false`, `run_verify=true`) keep type and meaning through seed, expansion defaults, and explicit submission | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (boolean typing incl. string rejection, expansion, seed, neutrality, selector) | Explicit `True`/`False` survive expansion as booleans; `"true"` string raises `PresetValidationError`; unknown slug raises `PresetNotFoundError` (no false success) |
| issue-implement-assessment | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| issue-implement-work-pr | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| jira-breakdown | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| jira-breakdown-implement | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| jira-breakdown-orchestrate | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| jira-implement | Retained composition preset (`kind: include`) | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Same composition evidence as other `kind: include` presets |
| jira-orchestrate | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| moonspec-orchestrate | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |
| pr-review-resolve | Retained | Integrated candidate (no rewrite); qualified by this step | `test_skill_preset_catalog_qualification_4278.py` (seed, neutrality, selector) + preset-owned tests | Seeded through existing service; no named-model branch |

## Skill coverage (35 rows)

Repo-native skills are qualified by the harness-neutral trigger-description
contract plus the catalog regression tests. Vendored `moonspec-*` skills are
owned upstream via `tools/sync_moonspec.py` and are pinned here for inventory
completeness, not reworded by this step.

| Skill | Behavior at candidate | Implementing child | Tests | Evidence |
| --- | --- | --- | --- | --- |
| batch-dependabot-resolver | Retained; trigger description harness-neutral | Integrated candidate (no rewrite); qualified by this step | `test_skill_trigger_descriptions_are_harness_neutral`, ledger-completeness test | No harness name in trigger description; named in this ledger |
| batch-github-workflows | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| batch-pr-resolver | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| batch-workflows | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| code-improvement-proposal | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| document-author | Retained; harness-neutral; dispatched by the `document-author` preset with effect assertions | Integrated candidate (no rewrite); qualified by this step | Skill contract tests + expansion skill-dispatch assertion | `skill.id: document-author` asserted in real expansion output |
| document-health-remediate | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| document-health-review | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| document-update | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| fix-ci | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| fix-comments | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| fix-merge-conflicts | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| github-issue-to-jira | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| github-issue-verify | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| jira-implement | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| jira-issue-creator | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| jira-issue-updater | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| jira-pr-verify | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| jira-verify | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| moonspec-align | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-assess | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-breakdown | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-doc-reconcile | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-implement | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-orchestrate | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-plan | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-specify | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-tasks | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| moonspec-verify | Retained vendored (upstream-owned; not reworded here) | Upstream via `tools/sync_moonspec.py` | Ledger-completeness test (inventory only) | Named in this ledger; wording owned upstream |
| pr-resolver | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| queue-moonmind-workflows | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| remediate-issue | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| story-reconcile-implementation | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| tactics-test | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |
| update-moonmind | Retained; harness-neutral | Integrated candidate (no rewrite); qualified by this step | Same skill contract tests | Same as above |

## Scenario matrix (20 rows)

Each row keeps expected outcome, checked evidence, negative control, and
owning test explicit. Rows without an owning test in this step name the gap
honestly; no fixture result is claimed as a live observation.

| Scenario | Expected evidence | Checked evidence in this step | Negative control | Owning test |
| --- | --- | --- | --- | --- |
| Repairable unchecked checklist | Authorized repair and verification without fabricated approval | Out of scope for this slice; feature children own repairs | Not run in this step | Gap: no owning test yet |
| Genuine authority or product decision | Safe work completes; exact remaining decision preserved | Out of scope for this slice | Not run in this step | Gap: no owning test yet |
| Technical requirement without a business metric | API/protocol/migration preserved; no invented threshold | Preset input schemas round-trip without invented thresholds (`_validate_inputs_schema` passthrough) | String-for-boolean rejected rather than coerced | `test_boolean_inputs_keep_type_and_reject_strings` (partial: typing boundary) |
| Missing local Docker CLI with working managed backend | Check runs at owning boundary; no socket fallback | Targeted tests run via the managed container-job service (`moonmind container python-tests`), never a local socket fallback | Local `./tools/test_unit.sh` path documented as unavailable here; no fallback attempted | Procedural evidence: this step's test runs |
| Optional enrichment outage | Disclosed limitation without blocking required work | Limitations section below discloses unavailable metrics | N/A (disclosure, not a gate) | This ledger |
| Unverified mandatory criterion or truncated required source | No whole-scope success or completion mutation | Unknown preset slug raises `PresetNotFoundError` through the production expansion boundary | Test asserts the raise; no success payload is produced | `test_unknown_preset_slug_never_reports_success` |
| Candidate-only versus landed implementation | Correct publication/review path versus already-completed path | Re-sync after seed is a no-op (landed state needs no republication) | Second sync creates zero rows | `test_sync_preserves_personal_custom_preset` (partial: idempotency boundary) |
| Stale assessment or changed candidate/head | Invalidated evidence refreshed, unrelated success not reused | Deterministic digest changes with intent; identical inputs reproduce identical candidates | Altered intent changes step ids | `test_preset_expansion_is_deterministic_with_effect_assertions` (partial: freshness boundary) |
| Two remediation attempts | Cumulative content survives to final handoff | Out of scope for this slice (orchestration history, not catalog) | Not run in this step | Gap: no owning test yet |
| Initial current verification pass | No redundant repair to traverse stages | Ledger records retained-correct rows without rewrites | Completeness test fails on any omitted row | `test_qualification_ledger_covers_every_preset_and_skill` (partial: no-op guard) |
| Omitted/true/false verification setting | Correct type through generation, resume, saved dispatch | Boolean defaults (`false`/`true`) survive seed; explicit booleans survive expansion; strings rejected | `"true"` string raises `PresetValidationError` | `test_boolean_inputs_keep_type_and_reject_strings` |
| Existing PR continuation on another deployment | Same issue/PR/candidate, preserved retry history | Out of scope for this slice (deployment composition) | Not run in this step | Gap: no owning test yet |
| Non-main PR base with unrelated worktree edits | Correct base + task-only commit | Out of scope for this slice (publication path; no PR created here) | Not run in this step | Gap: no owning test yet |
| Fix-only review loop | Zero merge calls from every entrypoint | Out of scope for this slice | Not run in this step | Gap: no owning test yet |
| Draft/proposal and verification-comment failure | No unintended mutation | Sync never mutates personal custom rows | Custom steps byte-identical after two syncs | `test_sync_preserves_personal_custom_preset` (partial: no-mutation boundary) |
| Empty/stale documentation findings | Correct no-op or revalidation | Re-sync no-op proven; ledger completeness guard fails closed | Missing row fails the suite | `test_qualification_ledger_covers_every_preset_and_skill` + sync idempotency (partial) |
| Hostile constraints and partial fan-out | Data stays data; no exec/duplicate/false completion | Out of scope for this slice | Not run in this step | Gap: no owning test yet |
| Immutable references and valid aliases | Assets readable from snapshot | Preset digests stable across identical expansions | Digest equality asserted | `test_preset_expansion_is_deterministic_with_effect_assertions` (partial: digest stability) |
| Build/test/update stale latest result | Current operation requires its own terminal evidence | Every new test in this step executes against the current candidate; evidence refs recorded at verification time | Stale evidence never reused: no recorded run is copied here | Procedural: this step's test runs |
| Saved catalog and in-flight compatibility | Coherent delivery without overwriting custom intent | Personal custom preset survives two seed syncs byte-identical | Title/inputs/steps equality asserted post-sync | `test_sync_preserves_personal_custom_preset` |

## CI selector proof (REQ-07)

Affected files in this step:

- `tests/unit/api/test_skill_preset_catalog_qualification_4278.py`
- `docs/Steps/SkillPresetCatalogQualification4278.md`
- (production catalog service and preset YAMLs are exercised but unchanged)

Required CI consumes the selector (`.github/workflows/pytest-unit-tests.yml`
pipes changed files through `tools/select_test_suites.py`). Actual collection
is demonstrated by running the owning test file (see Rebaseline commands) and
by `test_required_ci_selector_covers_catalog_and_stays_conservative`, which
asserts catalog changes select `unit_fast` + `api_component`, unknown diffs
take conservative full verification, and the ledger doc selects the fast shard
without forcing full backend runs. Empty, skipped, or unavailable work is
never reported as passed: the ledger-completeness test fails closed on any
omitted row.

## Limitations (unavailable metrics and deferred work)

- No live-model or paid-inference trials were run; none are required for this
  qualification. Any future behavioral comparison using actual agent runs must
  retain exact configuration provenance and be separately authorized.
- Three-deployment composition (REQ-06), old-worker/partial-bundle cutover
  (REQ-05), in-flight history replay, and saved-schedule/edit/rerun journeys
  (REQ-04 remainder) have no owning tests in this step; the scenario matrix
  marks them as gaps rather than claiming coverage.
- Before/after context-size, tool/step-count, and resource-use measurements
  (REQ-08 remainder) need pinned equivalent cases from the integrated feature
  children; this step reports only the deterministic equivalences it measured
  (step-id determinism, digest stability, sync idempotency) with denominators
  (19 presets, 35 skills) stated.
- Promotion/authority separation (REQ-09): this step adds no rollout,
  credential, model, or paid-trial mutation paths; measurement outputs
  (digests, step ids, sync counts) authorize nothing.
