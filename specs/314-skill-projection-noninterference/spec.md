# Feature Specification: Skill Projection Noninterference

**Feature Branch**: `314-skill-projection-noninterference`
**Created**: 2026-05-07
**Status**: Draft
**Input**: Trusted Jira preset brief for MM-608 from `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/artifacts/moonspec/MM-608-orchestration-input.md`. Preserve `MM-608` and the original preset brief for final verification.

Preserved source Jira preset brief: `MM-608` from the trusted Jira preset brief handoff, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response synthesized into `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/artifacts/moonspec/MM-608-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-608` under `specs/`, so `Specify` is the first incomplete stage.
Runtime intent: Jira Orchestrate always runs as a runtime implementation workflow. Source design references in the brief are treated as runtime source requirements.

## Original Preset Brief

````text
# MM-608 MoonSpec Orchestration Input

## Source

- Jira issue: MM-608
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Make MoonMind Skill Projection Non-Interfering With Repo Skills and Verification Workspaces
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-608 from MM project
Summary: Make MoonMind Skill Projection Non-Interfering With Repo Skills and Verification Workspaces
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-608 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-608: Make MoonMind Skill Projection Non-Interfering With Repo Skills and Verification Workspaces

## Jira Story: Make MoonMind Skill Projection Non-Interfering With Repo Skills and Verification Workspaces

Issue Type: Story / Reliability Bug
Priority: P0 / Highest
Component/s: Managed Agents, Agent Skills, MoonSpec Verification, Codex Runtime
Labels: agent-skills, managed-runtime, moonspec, projection, reliability, verification-blocker
Epic: MoonMind Skill System Robustness
Suggested Summary: Prevent active skill projection from masking tracked .agents/skills files

### Background

MoonMind’s skill system has a hard requirement that runtime skill projection must not interfere with existing repository skills. The canonical Skill System docs define .agents/skills as both the repo-facing checked-in skill source path and the managed runtime-facing active skill path, but they also explicitly say repo skill folders are resolution inputs, not mutable runtime state, and MoonMind must not rewrite checked-in user-authored skill files during materialization. The projection strategy order further states that symlink projection should only be used when safe, and adapters must not replace tracked or repo-authored .agents/skills directories in a publishable checkout as the normal mechanism.

The current implementation still materializes the active skill set into workspace_root / "skills_active" and attempts to expose it at workspace_root / ".agents" / "skills". When source_preservation_root is configured, the materializer may move an existing .agents/skills directory aside, create the projection link, and only restore it on projection failure, not on a normal successful run.

The shared workspace link helper currently treats .agents/skills as a symlink it owns for runtime projection, and _replace_link will unlink an existing symlink that points elsewhere without proving MoonMind ownership.

A recent /moonspec-verify verdict exposed this as a real requirements failure: feature-specific evidence passed, but the full unit suite could not complete because the active .agents/skills projection masked tracked skill files and caused unrelated unit failures.

### User Story

As a MoonMind operator running managed Codex or MoonSpec workflows against repositories that may contain checked-in .agents/skills,
I need runtime active skill projection to be isolated from repository-authored skill source files,
so that managed agents can read their selected active skills without masking tracked skills, breaking unrelated tests, contaminating Git status, or blocking final MoonSpec verification.

### Problem Statement

MoonMind currently overloads .agents/skills as both:

1. the repository’s checked-in skill source directory, and
2. the runtime-visible active selected skill projection.

That is unsafe in a shared managed workspace because agent commands, unit tests, verification gates, Git diffing, and publish staging all see the same filesystem view. Even if projection changes are filtered from publish, the active symlink can still hide tracked skill files while tests are running. The fix must separate repo skill sources from runtime active skill materialization and make .agents/skills an optional compatibility alias only when it is provably safe.

## Scope

### In Scope

1. Prevent runtime skill projection from masking any existing repo-authored .agents/skills directory.
2. Materialize active skill snapshots into MoonMind-owned, run-scoped backing paths outside the repo checkout or inside a clearly isolated runtime support directory.
3. Make .agents/skills projection optional and safe:
  
  - create it only when missing,
  - reuse/update it only when it is a proven MoonMind-owned projection,
  - never move or replace a repo-authored directory in normal managed runs.
4. Update turn instruction preparation so managed agents receive the actual active skill path through materialization metadata instead of assuming .agents/skills.
5. Harden symlink ownership checks.
6. Fix built-in skill discovery so it does not read Path.cwd()/.agents/skills as a built-in source.
7. Add loader guards so repo skill loading does not silently consume active projection symlinks as repo-authored source.
8. Add MoonSpec verification preflight protection against contaminated projected workspaces.
9. Add regression tests for success-path non-interference, not just failure-path restoration.
10. Update docs to reflect the corrected non-interference invariant.

### Out of Scope

1. Reworking the whole Agent Skill catalog data model.
2. Changing required-skill closure semantics.
3. Rewriting Codex managed sessions unrelated to skill materialization.
4. Changing user-facing skill authoring UX.
5. Removing .agents/skills as a documented repo source path.

## Acceptance Criteria

### AC1 — Existing repo-authored .agents/skills is never masked in normal managed runs

Given a repository checkout contains a real directory at .agents/skills with tracked skill files,
When MoonMind materializes an active selected skill snapshot for a managed runtime,
Then .agents/skills remains a directory,
And its existing files remain readable and unchanged,
And MoonMind does not replace it with a symlink,
And MoonMind does not move it to preservation storage as part of the normal success path.

### AC2 — Active skills are still available to the managed agent

Given .agents/skills is repo-authored and cannot be used as the active projection alias,
When a managed Codex turn is prepared,
Then selected active skills are materialized under a MoonMind-owned run-scoped active path, for example:

```
/work/agent_jobs/<task_run_id>/runtime/skills_active/<snapshot_id>
```

And the activation summary instructs the agent to read from the actual materialized active path,
And the run metadata records both backingPath and visiblePath,
And visiblePath points to the safe active path when .agents/skills cannot be safely used.

### AC3 — .agents/skills alias is only installed when safe

Given .agents/skills is missing,
When MoonMind materializes active skills,
Then MoonMind may create .agents/skills as a MoonMind-owned projection alias.

Given .agents/skills is already a MoonMind-owned symlink to the same active backing store,
When MoonMind materializes active skills,
Then MoonMind may reuse it.

Given .agents/skills is a MoonMind-owned stale projection symlink,
When MoonMind materializes active skills,
Then MoonMind may replace it only after ownership is proven.

Given .agents/skills is a directory, file, or unknown symlink,
When MoonMind materializes active skills,
Then MoonMind must not replace it in a publishable checkout.

### AC4 — Preserve-and-link fallback is no longer the default

Given a workspace has a repo-authored .agents/skills directory,
When source_preservation_root is configured,
Then the materializer must not move .agents/skills aside unless an explicit disposable-workspace projection mode is selected.

And if disposable preserve-and-link mode is ever used, it must create a projection lease and restore the original tree in a finally path before verification, publish, or task completion.

### AC5 — Unknown symlinks are not overwritten

Given .agents/skills exists as a symlink,
When MoonMind attempts to create or update a projection alias,
Then MoonMind must verify that the symlink is MoonMind-owned before replacing it.

Ownership may be proven by one or more of:

- symlink resolves under configured MoonMind active skill roots,
- matching _manifest.json identifies the active snapshot and workspace,
- matching projection lease exists,
- path is within the current run’s owned runtime support directory.

Unknown symlinks must fail with an actionable diagnostic rather than being unlinked.

### AC6 — Built-in skill loading is independent of the active workspace projection

Given the current working directory contains .agents/skills as an active projection,
When BuiltInSkillLoader loads built-in skills,
Then it must load from packaged/configured MoonMind built-in roots only,
And it must not treat Path.cwd()/.agents/skills as built-in skill source.

### AC7 — Repo skill loading detects contamination

Given repo skill loading is enabled,
And .agents/skills resolves to a MoonMind active projection symlink,
When RepoSkillLoader scans repository skills,
Then it must not treat the active projection as repo-authored source,
And it should either skip with explicit diagnostics or fail fast with a workspace-contamination error.

### AC8 — MoonSpec final verification uses a clean repository view

Given /moonspec-verify runs the full unit suite,
When the workspace contains an active skill projection at .agents/skills, .gemini/skills, or root skills_active,
Then the verifier must repair, restore, or reclone before running full-suite evidence,
Or fail with a clear environment-contamination verdict rather than reporting feature evidence as blocked by masked tracked skill files.

### AC9 — Git and publish surfaces remain clean

Given a managed skill run completes successfully,
When Git status is inspected,
Then there must be no deletion, symlink replacement, or modification under repo-authored .agents/skills.

And publish staging must continue to exclude projection-only paths such as:

```
.agents/skills
.gemini/skills
skills_active
```

when they are MoonMind-owned projections.

### AC10 — Regression tests cover the original verification failure

Given a MoonMind checkout with tracked .agents/skills/*,
When a managed selected-skill turn is prepared and then a representative unit or MoonSpec verification command runs,
Then the test must prove the tracked skills are still visible,
And the unit suite must not fail because active projection masked those files.

## Implementation Tasks

### Task 1 — Update canonical docs

Update docs/Steps/SkillSystem.md to clarify the corrected invariant:

- .agents/skills is the repo-authored source path.
- Managed runtimes may see active skills at .agents/skills only when this can be done without masking repo-authored files.
- Otherwise the runtime activation summary must point to a MoonMind-owned active path.
- .agents/skills alias is optional compatibility, not the authoritative active path.
- Preserve-and-link is a disposable-workspace fallback only, not the normal managed runtime path.

### Task 2 — Refactor AgentSkillMaterializer active path policy

Update moonmind/services/skill_materialization.py.

Required behavior:

- Default active backing store should be run-scoped and MoonMind-owned.
- Do not default to <workspace>/skills_active when that workspace is the publishable repo checkout.
- Introduce explicit projection result metadata:

```
{
  "activeSkills": ["..."],
  "backingPath": "...",
  "visiblePath": "...",
  "canonicalAliasPath": ".agents/skills",
  "canonicalAliasAvailable": true,
  "canonicalAliasSkippedReason": null,
  "repoSkillSourcePreserved": true
}
```

- If .agents/skills is a repo-authored directory, leave it untouched and set:

```
{
  "visiblePath": "<run-scoped active path>",
  "canonicalAliasAvailable": false,
  "canonicalAliasSkippedReason": "repo_authored_skills_present"
}
```

- Ensure _manifest.json records the actual visible path, not always .agents/skills.

### Task 3 — Replace normal preserve-and-link behavior

Remove or disable the current behavior that moves existing .agents/skills aside during successful materialization.

If a fallback is still required for disposable workspaces:

- add an explicit enum/config mode, for example ProjectionMode.TRANSACTIONAL_PRESERVE_AND_LINK;
- create a projection lease;
- restore in a finally block;
- reconcile stale leases on startup;
- ensure verification and publish cannot run while a projection lease is active.

### Task 4 — Harden workspace_links.py

Update moonmind/workflows/skills/workspace_links.py.

Required behavior:

- _replace_link must not unlink arbitrary symlinks.
- Add ownership validation before replacing a symlink.
- Add optional owned_roots, manifest_path, or projection_lease parameters.
- Return metadata that distinguishes:
  
  - alias created,
  - alias reused,
  - alias skipped,
  - alias blocked,
  - alias failed.

Suggested helper:

```
def is_moonmind_owned_projection(
    path: Path,
    *,
    target: Path,
    owned_roots: Sequence[Path],
) -> bool:
    ...
```

### Task 5 — Update instruction generation to use visiblePath

Find all activation summaries and prompt blocks that hard-code .agents/skills.

Update them to use materialization metadata:

```
Full active MoonMind skill content is available at:
{materialization.metadata.visiblePath}
```

When .agents/skills alias is unavailable, include an explicit note:

```
The repository also contains `.agents/skills`; that directory is repo-authored source and must not be modified or treated as the active selected skill snapshot.
```

### Task 6 — Fix built-in skill discovery

Update moonmind/services/skill_resolution.py.

Remove Path.cwd() / ".agents" / "skills" from BuiltInSkillLoader.skills_root.

Built-in loader should use only:

- configured built-in skill root,
- packaged MoonMind source root,
- container packaged root,
- legacy mirror root if explicitly configured.

Add tests proving an active projection in the current working directory does not alter built-in discovery.

### Task 7 — Add repo/local loader projection guards

Update RepoSkillLoader and LocalSkillLoader.

Required behavior:

- If .agents/skills is a MoonMind active projection symlink, do not scan it as repo source.
- If .agents/skills/local is hidden by a projection, surface a clear diagnostic.
- Add a helper that detects MoonMind projection manifests or known active backing roots.

### Task 8 — Add MoonSpec verifier preflight

Add a preflight to /moonspec-verify and any full-suite verification command path.

Preflight checks:

```
test ! -L .agents/skills
test ! -L .gemini/skills
test ! -e skills_active || verify_moonmind_owned_and_ignored
git status --porcelain -- .agents/skills .gemini/skills skills_active
```

Expected behavior:

- restore/reclone if active projection is present;
- fail with ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION if repair is not possible;
- do not produce NO_DETERMINATION for feature work merely because MoonMind’s own projection masked tracked skills.

### Task 9 — Publish filtering cleanup

Keep projection-only publish filtering, but make it ownership-aware.

Projection paths should be excluded only when they are MoonMind-owned generated state. Real checked-in .agents/skills directories must remain publishable.

### Task 10 — Observability and diagnostics

Add structured diagnostics when alias projection is skipped or blocked:

```
{
  "event": "skill_projection_alias_skipped",
  "workspace": "...",
  "aliasPath": ".agents/skills",
  "reason": "repo_authored_skills_present",
  "activeVisiblePath": "...",
  "snapshotId": "..."
}
```

Add an operator-facing summary when a hard collision prevents launch.

### Task 11 — Regression tests

Add or update unit tests in:

```
tests/unit/services/test_skill_materialization.py
tests/unit/services/test_skill_resolution.py
tests/unit/workflows/temporal/test_agent_runtime_activities.py
tests/unit/workflows/adapters/test_codex_session_adapter.py
```

Required test cases:

1. test_materializer_preserves_existing_agents_skills_directory_on_success
2. test_materializer_uses_active_visible_path_when_agents_skills_is_repo_authored
3. test_materializer_creates_agents_skills_alias_when_path_missing
4. test_materializer_reuses_moonmind_owned_projection_symlink
5. test_materializer_refuses_unknown_agents_skills_symlink
6. test_builtin_loader_ignores_cwd_agents_skills_projection
7. test_repo_loader_rejects_active_projection_as_repo_source
8. test_prepare_turn_instructions_uses_materialized_visible_path
9. test_moonspec_verify_preflight_rejects_active_projection
10. test_publish_filter_excludes_projection_symlink_but_preserves_real_repo_skill_dir

### Task 12 — End-to-end verification

Run and record:

```
git diff --check
./tools/test_unit.sh tests/unit/services/test_skill_materialization.py tests/unit/services/test_skill_resolution.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_agent_runtime_activities.py
./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Also run a targeted MoonSpec verification scenario in a checkout that contains tracked .agents/skills files.

## Suggested Technical Design

### Before

```
repo checkout
  .agents/
    skills -> skills_active
  skills_active/
    _manifest.json
    selected-skill/
      SKILL.md
```

This masks tracked .agents/skills.

### After

```
repo checkout
  .agents/
    skills/
      repo-authored-skill/
        SKILL.md

run/runtime support
  runtime/
    skills_active/
      <snapshot_id>/
        _manifest.json
        selected-skill/
          SKILL.md
```

The activation summary points to:

```
/work/agent_jobs/<task_run_id>/runtime/skills_active/<snapshot_id>
```

When .agents/skills is missing or safely MoonMind-owned, an alias may exist:

```
repo checkout
  .agents/
    skills -> ../runtime/skills_active/<snapshot_id>
```

But this is optional and never installed over repo-authored source.

## Definition of Done

This story is complete when:

1. Managed skill materialization cannot mask repo-authored .agents/skills during normal runs.
2. Active skills remain readable by Codex and other managed runtimes through a reliable visiblePath.
3. .agents/skills alias creation is safe, ownership-aware, and optional.
4. Built-in skill loading no longer depends on the current working directory.
5. Repo skill loading refuses or diagnoses active projection contamination.
6. MoonSpec final verification runs in a clean repo view or fails fast with an environment-contamination diagnostic.
7. Publish filtering excludes generated projection state without suppressing real repo-authored skills.
8. Regression tests cover the original failure mode that produced NO_DETERMINATION.
9. Full unit suite evidence is captured without active skill projection masking tracked skill files.
10. Docs reflect the corrected contract.

## Notes for PR Description

Suggested PR summary:

```
## Summary

- Stop normal managed skill materialization from masking repo-authored `.agents/skills`.
- Materialize active selected skill snapshots into MoonMind-owned run-scoped paths and pass the actual `visiblePath` to runtime instructions.
- Make `.agents/skills` a safe optional alias only when absent or proven MoonMind-owned.
- Harden projection symlink ownership checks.
- Prevent built-in skill loading from reading current-workspace `.agents/skills`.
- Add MoonSpec verification preflight checks for active projection contamination.
- Add regression coverage for the prior full-suite blocker where active projection masked tracked skill files.

## Validation

- `git diff --check`
- `./tools/test_unit.sh tests/unit/services/test_skill_materialization.py tests/unit/services/test_skill_resolution.py`
- `./tools/test_unit.sh tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- `./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py`
- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
```
````

## User Story - Keep Runtime Skill Projection From Masking Repo Skills

**Summary**: As a MoonMind operator running managed agent or MoonSpec workflows against repositories with checked-in skill sources, I want runtime active skill projection isolated from repo-authored skill files so verification, Git status, and publish flows stay clean while agents can still read their selected active skills.

**Goal**: Managed runtime skill activation exposes the selected active skill snapshot through a safe materialized visible path without replacing, moving, hiding, or publishing changes to repository-authored `.agents/skills` content.

**Independent Test**: Can be validated end-to-end by preparing a managed selected-skill run in a checkout that contains tracked `.agents/skills` files, confirming the active skill bundle remains readable at the reported runtime visible path, confirming repo-authored skill files remain visible and unchanged, and running representative verification without projection-related contamination.

**Acceptance Scenarios**:

1. **Given** a publishable repository checkout contains a real repo-authored `.agents/skills` directory, **When** MoonMind materializes an active selected skill snapshot for a managed runtime, **Then** the repo-authored directory remains in place, readable, and unchanged, and MoonMind does not replace it with a symlink or preservation move in the normal success path.
2. **Given** `.agents/skills` cannot safely be used as an active projection alias, **When** a managed agent turn is prepared, **Then** the activation summary points to the actual MoonMind-owned active visible path, records backing and visible path metadata, and explicitly distinguishes the repo-authored `.agents/skills` directory from the active selected skill snapshot.
3. **Given** `.agents/skills` is missing or is already a proven MoonMind-owned projection, **When** MoonMind materializes active skills, **Then** it may create, reuse, or replace the alias only after ownership and safety checks pass.
4. **Given** `.agents/skills` is a directory, file, or unknown symlink that MoonMind cannot prove it owns, **When** projection planning evaluates the workspace, **Then** MoonMind leaves repo-authored content untouched or fails before launch with actionable diagnostics rather than unlinking or masking the path.
5. **Given** repo or built-in skill loaders run in a workspace that may contain an active projection, **When** they resolve skill sources, **Then** built-in loading does not read the current workspace projection and repo/local loading does not silently treat a MoonMind active projection as repo-authored source.
6. **Given** MoonSpec final verification or publish staging runs after a managed skill run, **When** projection artifacts or aliases are present, **Then** verification detects and repairs or fails fast on contaminated workspace state, and publish filtering excludes only MoonMind-owned projection state while preserving real repo-authored skills.

### Edge Cases

- `.agents/skills` is a symlink whose target is outside configured MoonMind-owned active roots.
- `.agents` exists as a file or `.agents/skills` exists as a regular file.
- A stale MoonMind-owned projection symlink points at an old backing store.
- Local-only `.agents/skills/local` input is hidden by a projection attempt.
- Active materialization succeeds but alias projection is skipped because repo-authored skills are present.
- Verification begins while a disposable projection lease or root `skills_active` path still exists in the checkout.
- Publish filtering sees a path named `.agents/skills` and must distinguish generated projection state from checked-in skill source content.

## Assumptions

- The selected story is limited to noninterference between runtime active skill projection and repo-authored skill sources; broader agent skill catalog redesign remains out of scope.
- `.agents/skills` remains a documented repo source path and can also be used as a compatibility alias only when that alias is safe and MoonMind-owned.
- Runtime source requirements are derived from the MM-608 Jira brief and the desired-state skill system contract in `docs/Steps/SkillSystem.md`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST preserve an existing repo-authored `.agents/skills` directory during normal managed runtime skill materialization.
- **FR-002**: MoonMind MUST NOT move, delete, replace, or mask repo-authored `.agents/skills` content in the normal success path for a publishable checkout.
- **FR-003**: MoonMind MUST materialize selected active skill snapshots into a MoonMind-owned run-scoped backing path when the repo checkout cannot safely host the active alias.
- **FR-004**: Managed runtime activation summaries MUST identify the actual active skill `visiblePath` and instruct agents to read selected skills from that path.
- **FR-005**: Materialization metadata MUST record active skills, backing path, visible path, canonical alias path, alias availability, alias skip or block reason, and whether repo skill source content was preserved.
- **FR-006**: MoonMind MUST create, reuse, or replace a `.agents/skills` alias only when the path is missing or proven MoonMind-owned.
- **FR-007**: MoonMind MUST fail before launch with actionable diagnostics when `.agents/skills` is a file, unknown symlink, or otherwise unsafe projection target that cannot be repaired without mutating repo-authored content.
- **FR-008**: Any disposable preserve-and-link projection mode MUST be explicit, lease-backed, restored before verification, publish, or task completion, and excluded from normal managed runtime behavior.
- **FR-009**: Built-in skill discovery MUST load only configured or packaged built-in sources and MUST NOT load `Path.cwd()/.agents/skills` as a built-in source.
- **FR-010**: Repo and local skill loading MUST detect MoonMind active projection symlinks and skip or fail with explicit contamination diagnostics instead of treating active projection content as repo-authored source.
- **FR-011**: MoonSpec verification preflight MUST detect active projection contamination at `.agents/skills`, `.gemini/skills`, and root `skills_active`, then repair, restore, reclone, or fail with a clear environment-contamination verdict before full-suite evidence is classified.
- **FR-012**: Publish filtering MUST exclude projection-only paths only when they are MoonMind-owned generated state and MUST preserve real checked-in `.agents/skills` directories.
- **FR-013**: MoonMind MUST emit structured operator diagnostics whenever projection aliases are skipped, reused, replaced, blocked, or fail.
- **FR-014**: Regression coverage MUST prove the original failure mode is resolved: selected active skills remain available while tracked `.agents/skills` files remain visible and unrelated unit or MoonSpec verification work is not blocked by projection masking.
- **FR-015**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-608` and this original Jira preset brief for final traceability.

### Key Entities *(include if feature involves data)*

- **Active Skill Snapshot**: The immutable selected skill bundle for one run or step, including active skill names, snapshot identity, and manifest metadata.
- **Runtime Skill Materialization**: The runtime-facing projection decision and paths that expose an active snapshot to an agent, including backing path, visible path, alias path, alias availability, and preservation status.
- **Projection Alias**: An optional `.agents/skills` compatibility path that may point to active skill content only when absent or proven MoonMind-owned.
- **Repo Skill Source**: Checked-in or local-only repository skill files under `.agents/skills` or `.agents/skills/local` that are inputs to resolution and must not be treated as mutable runtime state.
- **Projection Diagnostic**: Operator-visible structured evidence describing alias decisions, collision paths, ownership proof, skip or failure reasons, and remediation guidance.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Steps/SkillSystem.md` §5): Managed runtimes see the active selected skill set at the materialized `visiblePath`; `.agents/skills` remains the repo-authored source path and optional safe alias. Scope: in scope. Maps to FR-001, FR-003, FR-004, FR-006.
- **DESIGN-REQ-002** (`docs/Steps/SkillSystem.md` §6.3 and §12.4): Repo checked-in skills are resolution inputs, not mutable runtime state, and MoonMind must not rewrite checked-in user-authored skill files during materialization. Scope: in scope. Maps to FR-001, FR-002, FR-012.
- **DESIGN-REQ-003** (`docs/Steps/SkillSystem.md` §14.2): Active backing stores must be MoonMind-owned, run-scoped, read-only where practical, and correspond exactly to one resolved snapshot. Scope: in scope. Maps to FR-003, FR-005.
- **DESIGN-REQ-004** (`docs/Steps/SkillSystem.md` §14.4): The active materialization contract is the actual `visiblePath`, exact snapshot content, no alternate skill discovery during execution, optional `.agents/skills` compatibility alias, and safe projection before launch. Scope: in scope. Maps to FR-004, FR-005, FR-006, FR-007.
- **DESIGN-REQ-005** (`docs/Steps/SkillSystem.md` §14.4.1): Projection strategy must preserve publishable repository workspaces and use run-scoped visible paths, isolated workspaces, or safe aliases rather than replacing tracked repo-authored skill directories. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-008.
- **DESIGN-REQ-006** (`docs/Steps/SkillSystem.md` §14.7): Collision handling must leave checked-in `.agents/skills` directories untouched, replace only proven MoonMind-owned projection links, and fail before launch for unsafe files, unknown symlinks, or unrecoverable projection targets. Scope: in scope. Maps to FR-006, FR-007, FR-013.
- **DESIGN-REQ-007** (`docs/Steps/SkillSystem.md` §14.8 and §14.9): Runtime adapters consume pinned snapshots, avoid re-resolving or adding skills during execution, avoid rewriting checked-in skill inputs, and verify the runtime view before submitting the turn. Scope: in scope. Maps to FR-004, FR-005, FR-014.
- **DESIGN-REQ-008** (`MM-608` Jira brief AC6 and AC7): Built-in, repo, and local loaders must not confuse active projection content with built-in or repo-authored skill sources. Scope: in scope. Maps to FR-009, FR-010.
- **DESIGN-REQ-009** (`MM-608` Jira brief AC8 and AC9): MoonSpec verification and publish surfaces must detect projection contamination, preserve repo-authored skill files, and exclude only MoonMind-owned projection state. Scope: in scope. Maps to FR-011, FR-012, FR-014.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In tests with a real repo-authored `.agents/skills` directory, normal managed materialization leaves the directory present, readable, and unchanged in 100% of covered success-path cases.
- **SC-002**: Managed runtime preparation reports an active skill visible path that contains the selected snapshot and does not require masking repo-authored skill source files when `.agents/skills` already exists.
- **SC-003**: Alias ownership tests cover missing paths, proven MoonMind-owned current links, proven MoonMind-owned stale links, repo-authored directories, files, and unknown symlinks with the expected create, reuse, replace, skip, or fail outcome.
- **SC-004**: Loader tests prove built-in discovery ignores current-workspace active projections and repo/local loading reports projection contamination instead of silently consuming projected active skills.
- **SC-005**: MoonSpec verification preflight produces clean full-suite evidence or a clear environment-contamination verdict without classifying the feature as indeterminate because tracked skills were masked.
- **SC-006**: Publish filtering tests prove MoonMind-owned projection state is excluded while real repo-authored `.agents/skills` content remains publishable.
- **SC-007**: Traceability review confirms `MM-608`, the original Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-009 remain preserved across MoonSpec artifacts and final evidence.
