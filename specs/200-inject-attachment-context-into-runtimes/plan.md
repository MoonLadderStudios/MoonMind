# Implementation Plan: Inject Attachment Context Into Runtimes

**Branch**: `200-inject-attachment-context-into-runtimes` | **Date**: 2026-04-17 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/200-inject-attachment-context-into-runtimes/spec.md`

**Note**: The setup script could not run because the active managed branch is `mm-372-69e6909c`; artifacts were generated manually from `.specify/feature.json`.

## Summary

MM-372 requires text-first runtime instructions to receive target-scoped attachment context before `WORKSPACE`, while planning receives only compact later-step inventory and multimodal paths preserve existing refs/contracts. The implementation will extend the Codex worker instruction composition helpers to read prepared `.moonmind/attachments_manifest.json` and optional `.moonmind/vision/image_context_index.json`, select objective plus current-step entries, render a safety-marked `INPUT ATTACHMENTS` block, and keep non-current step entries out of active step instructions. Validation will use focused worker unit coverage for prompt ordering, target filtering, planning inventory, guardrails, and absent-manifest behavior.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Existing Codex worker runtime instruction composition, existing attachment materialization manifest, existing target-aware vision context index, standard-library `json` and `pathlib`  
**Storage**: Workspace-local prepared files under `.moonmind/attachments_manifest.json` and `.moonmind/vision/`; no new persistent storage  
**Unit Testing**: pytest via `./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py tests/unit/agents/codex_worker/test_attachment_materialization.py` for focused iteration and `./tools/test_unit.sh` for final unit verification  
**Integration Testing**: Worker prepare/instruction boundary coverage in the required unit suite; hermetic integration via `./tools/test_integration.sh` when Docker is available  
**Target Platform**: MoonMind managed runtime worker containers and per-job workspaces  
**Project Type**: Python worker/runtime orchestration service  
**Performance Goals**: Attachment block rendering is linear in manifest entries and avoids reading or embedding raw image bytes  
**Constraints**: Inject before `WORKSPACE`; include only objective and current-step context for step execution; planning gets compact inventory only; preserve source refs/target bindings/manifest as source of truth; do not add provider-specific multimodal schemas; preserve MM-372 traceability  
**Scale/Scope**: One prepared task workspace with objective attachments and zero or more step targets within existing attachment limits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Extends existing runtime instruction composition rather than creating a new agent behavior layer.
- II. One-Click Agent Deployment: PASS. No new service or external dependency.
- III. Avoid Vendor Lock-In: PASS. Uses MoonMind artifact refs and manifest/context paths, not provider-specific payload formats.
- IV. Own Your Data: PASS. References operator-controlled workspace artifacts without embedding bytes.
- V. Skills Are First-Class and Easy to Add: PASS. No skill source mutation.
- VI. Replaceability and Scientific Method: PASS. Adds deterministic unit evidence for prompt injection behavior.
- VII. Runtime Configurability: PASS. Consumes existing prepared artifacts and does not hardcode provider behavior.
- VIII. Modular and Extensible Architecture: PASS. Keeps rendering in worker helpers near existing instruction composition.
- IX. Resilient by Default: PASS. Missing manifests produce no injection instead of corrupting runtime instructions; malformed optional context is treated conservatively.
- X. Facilitate Continuous Improvement: PASS. Prompt text points agents to manifest/context paths that aid diagnostics.
- XI. Spec-Driven Development: PASS. This plan follows the one-story MM-372 spec with traceable tests.
- XII. Canonical Documentation Separates Desired State from Migration Backlog: PASS. Canonical docs remain source requirements; implementation artifacts live under `specs/`.
- XIII. Pre-release Compatibility Policy: PASS. No compatibility aliases or hidden provider transforms.

## Project Structure

### Documentation (this feature)

```text
specs/200-inject-attachment-context-into-runtimes/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-attachment-injection.md
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
└── agents/codex_worker/
    └── worker.py

tests/
└── unit/
    └── agents/codex_worker/
        └── test_worker.py
```

**Structure Decision**: Implement injection in `moonmind/agents/codex_worker/worker.py` because that file already owns prepared task workspaces and text-first runtime instruction composition. Keep tests in `tests/unit/agents/codex_worker/test_worker.py`, where existing instruction composition behavior is covered.

## Complexity Tracking

No constitution violations.
