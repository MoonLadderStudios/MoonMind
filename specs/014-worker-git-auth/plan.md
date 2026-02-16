# Implementation Plan: Worker GitHub Token Authentication Fast Path

**Branch**: `014-worker-git-auth` | **Date**: 2026-02-14 | **Spec**: `specs/014-worker-git-auth/spec.md`
**Input**: Feature specification from `/specs/014-worker-git-auth/spec.md`

## Summary

Implement the WorkerGitAuth fast path by adding GitHub CLI authentication preflight on worker startup (driven by `GITHUB_TOKEN`), preserving existing queue/clone/publish execution interfaces, enforcing token-free repository inputs, and adding validation coverage proving no credential leakage in worker logs/artifacts.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: Existing `moonmind.agents.codex_worker` modules, Python stdlib (`subprocess`, `urllib.parse`, `os`), pytest test suite  
**Storage**: Existing queue persistence and local worker filesystem artifacts (`var/worker/...`)  
**Testing**: Unit tests via `./tools/test_unit.sh` (plus focused codex worker tests)  
**Target Platform**: Linux container/local shell worker runtime with `codex`, `git`, and `gh` CLIs available  
**Project Type**: Backend worker runtime enhancement (CLI preflight + handler safety checks + unit tests)  
**Performance Goals**: Keep startup preflight overhead bounded to a few synchronous CLI checks and no change to steady-state poll cadence  
**Constraints**: No queue schema/API contract changes; no `repoAuthRef` resolver; no token material in queue payloads, command args, logs, or artifacts  
**Scale/Scope**: Codex worker startup/auth setup, repository input validation, command-log redaction safeguards, and unit test coverage updates

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` currently contains placeholder text only and no enforceable MUST/SHOULD rules.
- Repository-local requirements from `AGENTS.md` are honored (runtime code changes + test execution via `./tools/test_unit.sh`).

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/014-worker-git-auth/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── worker-github-auth-runtime.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
└── agents/codex_worker/
    ├── cli.py
    ├── handlers.py
    └── worker.py

tests/
└── unit/agents/codex_worker/
    ├── test_cli.py
    ├── test_handlers.py
    └── test_worker.py
```

**Structure Decision**: Keep all runtime auth and repository-safety logic in the existing codex worker modules, with corresponding unit-test updates under `tests/unit/agents/codex_worker/`.

## Phase 0: Research Plan

1. Determine startup preflight sequencing that adds GitHub auth setup without disrupting existing Codex preflight semantics.
2. Define secure token handling so `GITHUB_TOKEN` is piped via stdin (never argv/logged command text) and sanitized from surfaced errors.
3. Define repository input validation rules that reject tokenized HTTPS URLs while preserving accepted slug/HTTPS/SSH forms.
4. Define log/artifact safety checks and test strategy to prove no PAT leakage and preserve publish flow compatibility.

## Phase 1: Design Outputs

- `research.md`: selected strategies and tradeoffs for startup auth, input validation, and log redaction.
- `data-model.md`: runtime value objects/state for startup auth context, repository target validation, and command log sanitization.
- `contracts/worker-github-auth-runtime.md`: runtime contract for startup behavior, accepted repository forms, rejection conditions, and failure modes.
- `contracts/requirements-traceability.md`: one-row-per-`DOC-REQ-*` mapping to FRs, code surfaces, and validation strategy.
- `quickstart.md`: reproducible verification flow for worker startup, private clone/publish, and token leak checks.

## Post-Design Constitution Re-check

- Runtime code modifications and validation tasks are explicitly in scope.
- No constitution blockers were identified beyond placeholder constitution content.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
