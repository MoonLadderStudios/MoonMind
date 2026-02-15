# Feature Specification: Worker GitHub Token Authentication Fast Path

**Feature Branch**: `014-worker-git-auth`  
**Created**: 2026-02-14  
**Status**: Draft  
**Input**: User description: "Implement docs/WorkerGitAuth.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Worker Clones and Publishes Private Repositories (Priority: P1)

As a queue operator, I need workers to authenticate to GitHub at startup using host-level `GITHUB_TOKEN` so private repositories can be cloned and publish flows can push branches or open PRs.

**Why this priority**: The core contract requires private repository clone/push support immediately without queue model redesign.

**Independent Test**: Start the worker with `GITHUB_TOKEN`, run one `codex_exec` job against a private repository payload using slug and HTTPS forms, and verify clone plus optional publish path succeed.

**Acceptance Scenarios**:

1. **Given** a worker runtime with valid `GITHUB_TOKEN`, **When** the worker starts, **Then** it completes GitHub CLI auth setup before entering the poll loop.
2. **Given** a private repository payload in slug or HTTPS form, **When** a `codex_exec` job executes, **Then** clone succeeds without embedding credentials in repository URLs.
3. **Given** publish mode is enabled, **When** the job has local changes, **Then** branch push and optional PR creation succeed through the established publish flow.

---

### User Story 2 - Worker Prevents Credential Leakage (Priority: P1)

As a security-conscious operator, I need runtime behavior that prevents GitHub PAT exposure in queue payloads, command arguments, logs, and artifacts.

**Why this priority**: Secret leakage is a critical safety risk and is explicitly forbidden by the source contract.

**Independent Test**: Execute startup auth and at least one job while capturing logs/artifacts, then verify no token values or tokenized repository URLs are present.

**Acceptance Scenarios**:

1. **Given** startup auth executes with `GITHUB_TOKEN`, **When** command logging is emitted, **Then** no raw token value appears in worker logs.
2. **Given** producers send repository values, **When** payload validation runs, **Then** token-in-URL repository formats are rejected.
3. **Given** job completion artifacts are uploaded, **When** artifact contents are inspected, **Then** they contain no raw PAT material.

---

### User Story 3 - Worker Fails Fast and Supports Operational Recovery (Priority: P2)

As an operator, I need deterministic startup failures and diagnostics for GitHub auth so clone/publish failures can be quickly triaged and recovered by rotating/replacing tokens.

**Why this priority**: Fail-fast startup and clear diagnostics are required to keep worker fleets reliable during auth drift.

**Independent Test**: Start worker without `gh` or with invalid token, verify startup exits with clear error, then rotate token and restart successfully without code changes.

**Acceptance Scenarios**:

1. **Given** GitHub CLI is unavailable, **When** worker startup preflight runs, **Then** startup fails with an explicit actionable error.
2. **Given** `GITHUB_TOKEN` is configured but invalid, **When** auth setup/status checks run, **Then** startup fails before polling begins.
3. **Given** token rotation occurs in runtime configuration, **When** worker restarts, **Then** auth and private repository operations resume without code updates.

### Edge Cases

- `GITHUB_TOKEN` is unset; worker should continue with current behavior and fail later only when private repository auth is required.
- Repository payload uses unsupported or insecure tokenized URL forms.
- `gh auth setup-git` succeeds but `gh auth status` fails due to revoked/expired token.
- Existing command logging captures authentication commands and must avoid leaking token material.
- Worker queue policy allows repository but GitHub token permissions are insufficient for push/PR.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `WorkerGitAuth.md:9-13`, `WorkerGitAuth.md:33`): Worker auth fast path MUST use runtime-provided `GITHUB_TOKEN` for GitHub authentication configuration.
- **DOC-REQ-002** (Source: `WorkerGitAuth.md:17-20`): Fast path implementation MUST avoid queue schema changes and must not require a `repoAuthRef` resolver.
- **DOC-REQ-003** (Source: `WorkerGitAuth.md:47`, `WorkerGitAuth.md:61`): Worker startup MUST verify GitHub CLI availability and fail fast with clear error on auth setup failure.
- **DOC-REQ-004** (Source: `WorkerGitAuth.md:48-53`): If `GITHUB_TOKEN` is present, worker startup MUST run `gh auth login --with-token` followed by `gh auth setup-git`.
- **DOC-REQ-005** (Source: `WorkerGitAuth.md:55-59`): Worker startup MUST validate GitHub authentication status before entering poll loop.
- **DOC-REQ-006** (Source: `WorkerGitAuth.md:67-71`, `WorkerGitAuth.md:75`): Repository payloads MUST stay token-free and reject tokenized HTTPS URL formats.
- **DOC-REQ-007** (Source: `WorkerGitAuth.md:24-27`, `WorkerGitAuth.md:63`): Existing clone/push/PR behavior MUST remain compatible while enabling private repository access through configured GitHub auth.
- **DOC-REQ-008** (Source: `WorkerGitAuth.md:79-82`): Worker runtime logging and artifacts MUST not expose `GITHUB_TOKEN` or other PAT values.
- **DOC-REQ-009** (Source: `WorkerGitAuth.md:107-109`): Existing worker repository allowlist guardrails MUST remain active for blast-radius control.
- **DOC-REQ-010** (Source: `WorkerGitAuth.md:115-118`): Implementation validation MUST demonstrate private clone support, publish without tokenized URLs, no token leaks, and token-rotation operability without code changes.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`): Worker startup MUST perform GitHub auth preflight that checks for `gh`, configures git credential integration when `GITHUB_TOKEN` is set, validates auth status, and fails with clear runtime errors when setup is invalid.
- **FR-002** (`DOC-REQ-002`, `DOC-REQ-007`): Implementation MUST preserve existing queue payload and worker execution interfaces while enabling private repository clone/push/PR via startup GitHub auth configuration.
- **FR-003** (`DOC-REQ-006`, `DOC-REQ-008`): Worker payload handling and command logging MUST enforce token-free repository inputs and prevent PAT exposure in logs, command arguments, and artifacts.
- **FR-004** (`DOC-REQ-009`): Queue claim and execution paths MUST continue honoring repository allowlist policy constraints enforced by existing worker-token authorization.
- **FR-005** (`DOC-REQ-010`): Validation suite MUST cover startup preflight success/failure, private repository clone/publish compatibility, and negative checks for token leakage and tokenized URL rejection.
- **FR-006**: Runtime deliverables MUST include production runtime code changes plus validation tests; docs-only output is insufficient.

### Key Entities *(include if feature involves data)*

- **GitAuthStartupContext**: Derived worker startup context for GitHub CLI availability, token presence, and auth preflight outcomes.
- **CodexExecRepositoryInput**: Normalized repository input string in accepted forms (`owner/repo`, token-free HTTPS URL, optional SSH URL).
- **WorkerCommandLogRecord**: Structured command log output that records command intent while redacting sensitive argument values.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Worker startup succeeds with valid `GITHUB_TOKEN` and fails fast with clear error when `gh` is missing or auth status is invalid.
- **SC-002**: Automated tests validate private repository clone/push behavior for token-free slug/HTTPS inputs without queue schema changes.
- **SC-003**: Automated tests reject tokenized repository URLs and confirm logs/artifacts do not contain PAT values.
- **SC-004**: Existing queue repository allowlist behavior remains enforced in worker claim/execution paths.
- **SC-005**: New and updated unit tests pass via `./tools/test_unit.sh`.

## Assumptions

- Worker runtime environments already include the GitHub CLI binary (`gh`) or can install it via existing container setup.
- Producers continue sending repository payloads in existing supported formats and do not rely on token-in-URL patterns.
- Secret rotation is achieved operationally by updating `GITHUB_TOKEN` and restarting workers.
