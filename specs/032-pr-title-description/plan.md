# Implementation Plan: Queue Publish PR Title and Description System

**Branch**: `032-pr-title-description` | **Date**: 2026-02-19 | **Spec**: `specs/032-pr-title-description/spec.md`
**Input**: Feature specification from `/specs/032-pr-title-description/spec.md`

## Summary

Implement the canonical queue publish PR text system from `docs/TaskQueueSystem.md` by adding deterministic commit/PR text generation in the `moonmind.task.publish` stage: strict override precedence for `publish.commitMessage`/`publish.prTitle`/`publish.prBody`, fallback title derivation from task intent, and generated PR body correlation metadata footer.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Existing `moonmind.agents.codex_worker.worker` publish flow, queue task contract models, `gh` CLI invocation path, pytest  
**Storage**: N/A for persistence; publish artifacts (`publish_result.json`, `logs/publish.log`) on filesystem  
**Testing**: pytest unit tests via `./tools/test_unit.sh`  
**Target Platform**: MoonMind worker runtime (Linux container/local shell)  
**Project Type**: Backend worker runtime behavior update + unit tests  
**Performance Goals**: PR text derivation remains O(n) over short payload text and does not add network round-trips beyond existing publish flow  
**Constraints**: Preserve existing publish mode semantics, keep explicit override values verbatim, avoid secret leakage in generated metadata, do not require API schema changes  
**Scale/Scope**: Focused changes in canonical task publish stage and test coverage for title/body derivation and branch correlation semantics

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` is still placeholder-only and does not define enforceable MUST/SHOULD principles.
- No constitution blockers were identified beyond repository and AGENTS constraints.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/032-pr-title-description/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── publish-pr-text-contract.md
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── agents/codex_worker/worker.py

tests/
└── unit/agents/codex_worker/test_worker.py
```

**Structure Decision**: Implement default PR text generation as deterministic helper logic inside the canonical task publish stage (`CodexWorker._run_publish_stage`) and validate behavior through focused worker unit tests.

## Phase 0: Research Plan

1. Confirm the exact fallback order and metadata requirements from `docs/TaskQueueSystem.md` section 6.4.
2. Determine the safest title-normalization strategy (first step title, then first instruction sentence/line, then fallback) while preventing full UUID leakage in title.
3. Define generated PR body format with parseable metadata footer and source-of-truth correlation fields.
4. Determine minimal, high-signal unit tests that validate precedence, fallback behavior, metadata integrity, and base/head branch semantics.

## Phase 1: Design Outputs

- `research.md`: decisions and alternatives for title derivation and metadata formatting.
- `data-model.md`: publish text entities and derivation invariants.
- `contracts/publish-pr-text-contract.md`: behavioral contract for publish text resolution.
- `contracts/requirements-traceability.md`: one row per `DOC-REQ-*` with implementation surface and validation strategy.
- `quickstart.md`: local verification flow for publish text behavior via tests.

## Post-Design Constitution Re-check

- Design includes production runtime code changes and validation tests (required by runtime intent).
- No constitution violations found (placeholder constitution remains non-binding).

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
