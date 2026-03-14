# Research: Task Finish Summary System

## Decision 1: Use one canonical finish summary schema (`v1`) for DB and artifacts

- **Decision**: Persist the same `finishSummary` object shape in `agent_jobs.finish_summary_json` and `reports/run_summary.json`.
- **Rationale**: A shared payload removes translation drift across worker, API, and dashboard surfaces and supports deterministic tests.
- **Alternatives considered**:
  - Separate DB and artifact schemas: rejected due to synchronization risk.
  - Artifact-only summary: rejected because dashboard detail must not require artifact download.

## Decision 2: Outcome classification uses strict precedence rules

- **Decision**: Apply terminal classification in this order: `CANCELLED` > `FAILED` > publish-mode/publish-result-derived success outcomes (`PUBLISH_DISABLED`, `NO_CHANGES`, `PUBLISHED_PR`, `PUBLISHED_BRANCH`).
- **Rationale**: Failure/cancel semantics must not be masked by publish configuration; this matches source-doc edge-case requirements.
- **Alternatives considered**:
  - Publish-mode-first classification: rejected because failures could be mislabeled as publish-disabled.
  - Generic success code: rejected because operators need actionable distinctions.

## Decision 3: Finalize stage is mandatory and best-effort for artifacts

- **Decision**: Introduce/maintain explicit finalization behavior that always builds finish metadata on terminal paths, with best-effort artifact write/upload.
- **Rationale**: Queue terminal transitions need outcome metadata even when artifact writes fail.
- **Alternatives considered**:
  - Emit summaries only on success: rejected because failures/cancellations are key triage cases.
  - Fail terminal transition when artifact write fails: rejected because operational correctness favors completion/failure signaling over optional artifact guarantees.

## Decision 4: Redaction is enforced before persistence and output

- **Decision**: Route outcome reasons, proposal errors, and summary payload text through existing redaction helpers prior to DB persistence and artifact creation.
- **Rationale**: Source requirements prohibit secret-bearing text in finish metadata and artifacts.
- **Alternatives considered**:
  - Redact only at UI render: rejected because secrets would remain stored.
  - Block all free-text fields: rejected because short diagnostic reasons are required for triage.

## Decision 5: Queue list/detail payload split remains explicit

- **Decision**: List responses include compact finish outcome fields (`code/stage/reason`) while full `finishSummary` is returned in detail (and optional dedicated finish-summary endpoint).
- **Rationale**: Preserves list performance while satisfying detail-level diagnostics without downloads.
- **Alternatives considered**:
  - Always include full summary in list: rejected for payload size overhead.
  - Expose only finish summary and derive list data client-side: rejected because list should not parse bulky nested payloads.

## Decision 6: Proposal outcomes are modeled as finisher output, not new queue semantics

- **Decision**: Capture proposal requested/generated/submitted/errors as finish summary `proposals` metadata, and extend proposals list filtering with `originId` for queue deep links.
- **Rationale**: Meets traceability goals while preserving existing proposal promotion semantics.
- **Alternatives considered**:
  - Separate proposal summary endpoint per job: rejected as unnecessary duplication.
  - Redesign proposal lifecycle states: rejected as out of scope/non-goal.

## Decision 7: Runtime-vs-docs orchestration mode alignment is enforced in planning

- **Decision**: Treat this feature as runtime mode because `spec.md` declares runtime implementation intent and runtime/test deliverables; planning artifacts explicitly require production code and validation execution.
- **Rationale**: Prevents accidental docs-only completion claims for a runtime-scoped objective.
- **Alternatives considered**:
  - Docs-only planning completion: rejected by `DOC-REQ-019` and FR-012/FR-013.
  - Runtime work without planning artifacts: rejected because this step requires agentkit-plan outputs.

## Decision 8: Validation command source of truth stays `./tools/test_unit.sh`

- **Decision**: Use `./tools/test_unit.sh` for unit/dashboard validation, including WSL docker delegation behavior.
- **Rationale**: Repository instructions define this script as CI/local parity gate.
- **Alternatives considered**:
  - Direct `pytest` invocation: rejected by project testing instructions.
  - Running only touched tests: rejected for this feature because cross-surface contract changes need broader regression coverage.
