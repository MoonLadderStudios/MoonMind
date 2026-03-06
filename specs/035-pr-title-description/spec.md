# Feature Specification: Queue Publish PR Title and Description System

**Feature Branch**: `032-pr-title-description`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: User description: "Implement the title and description PR system from docs/TaskQueueSystem.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Producer PR Overrides Are Applied Verbatim (Priority: P1)

As a task producer, I can provide `publish.commitMessage`, `publish.prTitle`, and `publish.prBody` so MoonMind publishes exactly the PR text I supplied.

**Why this priority**: Producer-provided values are the highest-confidence intent and must not be replaced by generated defaults.

**Independent Test**: Queue a `type=task` job with explicit publish overrides and verify the publish stage calls `git commit` and `gh pr create` with those exact text values.

**Acceptance Scenarios**:

1. **Given** a task payload with non-empty `publish.commitMessage`, **When** publish runs, **Then** commit uses the supplied message verbatim.
2. **Given** a task payload with non-empty `publish.prTitle` and `publish.prBody`, **When** publish mode is `pr`, **Then** PR creation uses supplied title/body verbatim.

---

### User Story 2 - Deterministic Defaults Are Generated from Task Intent (Priority: P1)

As an operator, I need readable default PR text when producer overrides are omitted so published PRs still describe the queued work clearly and consistently.

**Why this priority**: Most typed submit flows omit explicit PR text, so deterministic defaults are required for baseline publish quality.

**Independent Test**: Queue `publish.mode=pr` jobs with missing overrides and varying step/instruction content; verify derived title fallback order and generated default body structure.

**Acceptance Scenarios**:

1. **Given** no explicit `publish.prTitle` and at least one non-empty step title, **When** publish runs, **Then** PR title uses the first non-empty step title.
2. **Given** no step titles and no explicit `publish.prTitle`, **When** publish runs, **Then** PR title uses the first sentence/line of `task.instructions`.
3. **Given** no explicit `publish.prBody`, **When** publish runs, **Then** PR body includes a generated summary plus metadata footer with required correlation keys.

---

### User Story 3 - Publish Correlation Metadata Is Machine Parseable (Priority: P2)

As a queue maintainer, I need generated PR text to contain stable correlation metadata so queue jobs and PR records can be reconciled reliably.

**Why this priority**: Queue troubleshooting requires deterministic mapping between job history and GitHub PRs.

**Independent Test**: Run publish with generated body defaults and verify the metadata footer contains begin/end markers and stable key names with full job UUID.

**Acceptance Scenarios**:

1. **Given** a generated default PR body, **When** publish runs, **Then** body includes `moonmind:begin`/`moonmind:end` markers and fields for Job, Runtime, Base, and Head.
2. **Given** generated title and body defaults, **When** publish runs, **Then** title avoids full UUID while body metadata includes full UUID for source-of-truth correlation.

### Edge Cases

- `publish.prTitle` or `publish.prBody` is present but only whitespace; system should treat it as omitted and use deterministic defaults.
- `task.instructions` starts with a very long line; generated title should remain concise and list-readable.
- First step title is non-empty but contains punctuation/newlines requiring normalization before use as a title.
- `publish.mode=branch` should still honor commit message overrides and skip PR title/body generation.
- Generated metadata footer must never include secret-like values.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/TaskQueueSystem.md`, section **4.3 Publish Overrides and Producer Guidance**): Canonical task publish controls include explicit producer overrides `commitMessage`, `prTitle`, and `prBody`.
- **DOC-REQ-002** (Source: `docs/TaskQueueSystem.md`, section **6.3 Publish Stage**): Publish stage owns commit/push operations and default commit/PR text generation.
- **DOC-REQ-003** (Source: `docs/TaskQueueSystem.md`, section **6.4 PR Text Generation and Correlation Best Practices**, item 1): Commit message rule is explicit override first, deterministic default otherwise.
- **DOC-REQ-004** (Source: `docs/TaskQueueSystem.md`, section **6.4 PR Text Generation and Correlation Best Practices**, item 2): PR title rule is explicit override first, then derive from first non-empty step title, then first sentence/line of instructions, then fallback default.
- **DOC-REQ-005** (Source: `docs/TaskQueueSystem.md`, section **6.4 PR Text Generation and Correlation Best Practices**, item 2): Generated title should be concise for list readability, may include short correlation token, and must avoid full UUID text.
- **DOC-REQ-006** (Source: `docs/TaskQueueSystem.md`, section **6.4 PR Text Generation and Correlation Best Practices**, item 3): PR body rule is explicit override first, generated summary plus metadata footer otherwise.
- **DOC-REQ-007** (Source: `docs/TaskQueueSystem.md`, section **6.4 PR Text Generation and Correlation Best Practices** + **Metadata footer requirements**): Generated metadata footer must use stable machine-parseable keys, include full job UUID, and avoid secrets/token-like values.
- **DOC-REQ-008** (Source: `docs/TaskQueueSystem.md`, section **6.3 Publish Stage**): PR creation uses `publish.prBaseBranch` when set, otherwise resolved starting branch, and head must be effective working branch.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-003`): Publish stage MUST use non-empty `publish.commitMessage` verbatim for commit text; when omitted, it MUST generate a deterministic default commit message.
- **FR-002** (`DOC-REQ-001`, `DOC-REQ-004`, `DOC-REQ-005`): Publish stage MUST use non-empty `publish.prTitle` verbatim; when omitted, it MUST derive title by fallback order (first non-empty step title, first sentence/line of task instructions, deterministic fallback) while avoiding full UUIDs and keeping title concise.
- **FR-003** (`DOC-REQ-001`, `DOC-REQ-006`, `DOC-REQ-007`): Publish stage MUST use non-empty `publish.prBody` verbatim; when omitted, it MUST generate a PR body containing a summary plus metadata footer with begin/end markers and stable keys: `MoonMind Job`, `Runtime`, `Base`, `Head`.
- **FR-004** (`DOC-REQ-007`): Generated metadata footer MUST include full job UUID and MUST NOT include secrets or token-like values.
- **FR-005** (`DOC-REQ-002`, `DOC-REQ-008`): In `publish.mode=pr`, publish stage MUST call PR creation with base branch `publish.prBaseBranch` override when present, otherwise resolved starting branch, and head branch equal to effective working branch.
- **FR-006**: Runtime deliverables MUST include production runtime code changes and validation tests; docs-only output is insufficient.

### Key Entities *(include if feature involves data)*

- **PublishTextOverrides**: User-provided `commitMessage`, `prTitle`, and `prBody` fields from canonical task payload.
- **DerivedPrText**: Deterministic publish defaults derived from task steps, task instructions, and job context when overrides are absent.
- **PublishCorrelationFooter**: Machine-parseable metadata block embedded in generated PR body for queue-to-PR reconciliation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests confirm override precedence for commit message, PR title, and PR body in publish mode `pr`.
- **SC-002**: Unit tests confirm default title derivation order (step title -> instruction line/sentence -> fallback).
- **SC-003**: Unit tests confirm generated PR body metadata footer includes required markers/keys and full job UUID while title excludes full UUID.
- **SC-004**: Unit tests confirm PR creation branch semantics use `prBaseBranch` override else starting branch, and head equals working branch.
- **SC-005**: Feature test suite passes via `./tools/test_unit.sh`.

## Assumptions

- This feature is implemented in the canonical queue publish path used by `type="task"` jobs in `moonmind/agents/codex_worker/worker.py`.
- Legacy `codex_exec` publish text behavior may remain unchanged unless explicitly routed through the same canonical helper in this feature.
