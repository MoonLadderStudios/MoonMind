# Feature Specification: DooD Workload Observability

**Feature Branch**: `155-dood-workload-observability`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 of the MoonMind Docker-out-of-Docker plan in runtime mode: artifact, live-log, and session-association integration for Docker-backed workload tools. Successful and failed workload runs must publish durable runtime stdout, runtime stderr, runtime diagnostics, declared output artifacts, and structured execution metadata including selected runner profile, image reference, exit code, duration, timeout or cancel reason, producing step linkage, and optional managed-session association metadata. Workload metadata must make outputs UI- and API-consumable, must let operators diagnose runs from artifacts alone, and must not imply that workload containers are managed sessions or publish workload outputs as session continuity artifacts by default. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Diagnose Workload Runs from Artifacts (Priority: P1)

Operators need every Docker-backed workload run to leave durable evidence so success, failure, timeout, and cancellation can be understood without relying on container-local history or transient terminal output.

**Why this priority**: Durable artifact evidence is the central Phase 4 requirement. Without it, workload execution cannot be recovered, audited, or diagnosed after the container exits.

**Independent Test**: Execute one successful workload and one failed workload, then verify each run produces retrievable stdout, stderr, diagnostics, and structured metadata artifacts that are sufficient to explain the outcome.

**Acceptance Scenarios**:

1. **Given** a Docker-backed workload completes successfully, **When** an operator inspects the run evidence, **Then** runtime stdout, runtime stderr, diagnostics, and execution metadata are available as durable artifacts.
2. **Given** a Docker-backed workload fails with a non-zero exit, **When** an operator inspects the run evidence, **Then** the failure can be diagnosed from durable artifacts without accessing the workload container.
3. **Given** a Docker-backed workload times out or is canceled, **When** finalization runs, **Then** any captured output and final diagnostics include the timeout or cancel reason when available.

---

### User Story 2 - Link Workload Outputs to Producing Steps (Priority: P1)

Task detail consumers need workload outputs to be linked back to the plan step that produced them, so operators can identify which step launched a workload and which artifacts belong to that step.

**Why this priority**: Workload observability is only useful when outputs are attached to the correct execution context rather than appearing as unowned files.

**Independent Test**: Execute a plan step that invokes a Docker-backed workload and verify the resulting workload metadata identifies the task run, producing step, attempt, tool name, runner profile, and output artifact references.

**Acceptance Scenarios**:

1. **Given** a plan step launches a workload, **When** the run completes, **Then** the workload metadata links the output artifacts to the task run, step, attempt, tool name, and selected runner profile.
2. **Given** a workload declares expected outputs such as logs, summaries, reports, or primary artifacts, **When** those outputs exist at completion, **Then** they are linked as declared output artifacts for the producing step.
3. **Given** a declared output is missing at completion, **When** diagnostics are published, **Then** the missing declaration is visible without failing to publish the available runtime artifacts.

---

### User Story 3 - Preserve Managed Session Boundaries (Priority: P2)

Operators need workloads launched from managed-session-assisted steps to show session association context without implying that the workload container is itself a managed session.

**Why this priority**: The Docker-out-of-Docker architecture depends on keeping session containers, workload containers, and true managed agent runs separate.

**Independent Test**: Execute a workload with optional session association metadata and verify that the resulting metadata groups the workload with the session turn while excluding session continuity artifact classes by default.

**Acceptance Scenarios**:

1. **Given** a workload is launched from a managed-session-assisted step, **When** artifacts and metadata are published, **Then** session id, session epoch, and source turn id appear only as association context.
2. **Given** a workload produces runtime logs or diagnostics, **When** outputs are published, **Then** they are not published as session summary, session checkpoint, session control event, or session reset boundary artifacts by default.
3. **Given** an operator views workload metadata, **When** a session association is present, **Then** the view makes clear which step launched the workload without presenting the workload container as the session.

---

### User Story 4 - View Workload Evidence Through Existing Detail Surfaces (Priority: P3)

Operators need task detail and execution detail surfaces to expose workload logs, diagnostics, and output references so they can inspect workload evidence from the normal operational workflow.

**Why this priority**: Artifact publication is the durable truth, but operators still need convenient access through the existing observability surfaces.

**Independent Test**: Query task or execution detail for a workload-producing step and verify the response includes workload metadata and artifact references for logs, diagnostics, and declared outputs.

**Acceptance Scenarios**:

1. **Given** a workload-producing step has completed, **When** an API or UI consumer requests execution details, **Then** workload metadata and output artifact references are available for that step.
2. **Given** live log tailing is available for a workload, **When** a consumer opens the detail view during execution, **Then** the live tail can be shown while durable artifacts remain the final source of truth.
3. **Given** live log tailing is unavailable, **When** a consumer opens the detail view after completion, **Then** durable stdout and stderr artifacts are still available.

### Edge Cases

- Workload execution fails before the main command starts, but launcher diagnostics still need to explain the selected profile, image reference, and ownership labels.
- The workload emits no stdout, no stderr, or only one stream; the missing or empty stream must still be represented clearly.
- Workload output is large enough that detail metadata must remain bounded while artifacts retain the durable evidence.
- A declared output path is missing, malformed, duplicated, or outside the allowed artifact location.
- Artifact publication partially fails after workload completion.
- Timeout cleanup races with process exit.
- Session association metadata is absent, incomplete, or present for a non-session-assisted workload.
- Operators inspect a workload from historical detail after the workload container has been removed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST publish durable runtime stdout artifacts for every Docker-backed workload run when stdout capture is available, including successful and failed runs.
- **FR-002**: The system MUST publish durable runtime stderr artifacts for every Docker-backed workload run when stderr capture is available, including successful and failed runs.
- **FR-003**: The system MUST publish durable runtime diagnostics for every Docker-backed workload run that reaches finalization, including status, selected runner profile, image reference, exit code, duration, timeout reason, cancel reason when available, task run id, step id, attempt, tool name, and workload ownership labels.
- **FR-004**: The system MUST keep workload result metadata bounded and use artifact references for durable logs and detailed diagnostics.
- **FR-005**: The system MUST link workload artifacts and metadata to the producing task run, step, attempt, and tool invocation.
- **FR-006**: The system MUST expose workload metadata and artifact references through API- and UI-consumable execution detail surfaces.
- **FR-007**: The system MUST support declared output artifacts for workload-produced logs, summaries, primary outputs, test reports, packaged artifacts, or similar outputs.
- **FR-008**: The system MUST record missing declared outputs in diagnostics without discarding available runtime stdout, stderr, or diagnostics artifacts.
- **FR-009**: The system MUST include optional session association metadata when provided, limited to session id, session epoch, and source turn id or their equivalent grouping fields.
- **FR-010**: The system MUST NOT treat workload containers as managed sessions or true managed agent runs solely because session association metadata is present.
- **FR-011**: The system MUST NOT publish workload outputs as session continuity artifacts by default, including session summary, session checkpoint, session control event, or session reset boundary artifacts.
- **FR-012**: The system MUST allow operators to diagnose successful and failed workload runs from durable artifacts alone after the workload container has exited or been removed.
- **FR-013**: The system MUST preserve the existing boundary where Docker-backed workloads are ordinary executable tools unless the launched runtime is itself a true managed agent runtime.
- **FR-014**: The system MUST handle artifact publication failures with an operator-visible diagnostic outcome rather than silently reporting complete observability.
- **FR-015**: Required deliverables include production runtime code changes, not docs-only or spec-only changes.
- **FR-016**: Required deliverables MUST include validation tests covering artifact publication, declared output handling, workload-to-step linkage, session association metadata, and session-boundary preservation.

### Key Entities *(include if feature involves data)*

- **Workload Execution Evidence**: Durable stdout, stderr, diagnostics, and declared output references produced by one workload run.
- **Workload Execution Metadata**: Bounded metadata describing the workload outcome, selected runner profile, image reference, timing, exit status, timeout or cancel reason, and ownership labels.
- **Declared Output Artifact**: A workload-produced file or output category that the caller expects to be linked after completion, such as logs, summaries, reports, packages, or primary outputs.
- **Workload-Step Linkage**: The relationship between a workload run and the task run, step, attempt, and tool invocation that produced it.
- **Session Association Context**: Optional grouping metadata that connects a workload to a managed-session step without making the workload part of session identity.

### Assumptions

- Phase 1 request and runner-profile validation already define the workload request, runner profile, and ownership metadata contracts.
- Phase 2 launcher behavior already bounds execution, captures process streams, applies cleanup, and returns workload status metadata.
- Phase 3 tool exposure already routes Docker-backed workload tools through the ordinary executable tool path.
- Phase 4 focuses on one-shot workload containers; bounded helper containers remain out of scope.
- Runtime mode is required: production behavior and validation tests are in scope, while docs-only alignment is not sufficient.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of finalized successful and failed workload runs in validation, operators can retrieve stdout, stderr, diagnostics, and outcome metadata from durable artifacts or artifact references.
- **SC-002**: For 100% of workload-producing validation steps, execution detail metadata identifies the producing task run, step, attempt, tool name, selected runner profile, and image reference.
- **SC-003**: For 100% of validation runs with declared outputs, existing declared outputs are linked and missing declared outputs are reported in diagnostics.
- **SC-004**: For 100% of validation runs with session association metadata, the association is visible as grouping context and no session continuity artifact class is produced by default.
- **SC-005**: Failed, timed-out, and canceled validation workloads provide enough durable diagnostics for an operator to determine the final status and reason without inspecting the container.
- **SC-006**: The required validation suite demonstrates artifact publication and workload metadata behavior for both successful and unsuccessful workload outcomes.
