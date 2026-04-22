# Feature Specification: Sensitive Report Access and Retention

**Feature Branch**: `231-sensitive-report-access-retention`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-463 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-463-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-463 from MM project
Summary: Sensitive Report Access and Retention
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-463 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-463: Sensitive Report Access and Retention

Short Name
sensitive-report-access-retention

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 7. Consumer and producer invariants, 14. Security and access model, 15. Retention guidance
- Coverage IDs: DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-022

User Story
As an operator, I can rely on report artifacts to use existing authorization, preview, retention, pinning, and deletion behavior so sensitive reports remain useful without widening raw access.

Acceptance Criteria
- Given a sensitive report has restricted raw access, then Mission Control uses preview/default-read behavior where available and does not assume full download is allowed.
- Given `report.primary` or `report.summary` artifacts are created, then their default retention policy is long unless product policy overrides it.
- Given `report.structured` or `report.evidence` artifacts are created, then their retention follows standard or long policy based on the report family and audit needs.
- Given a final report is important to retain, then it can be pinned or unpinned through existing artifact APIs.
- Deleting a report artifact uses artifact-system-native soft/hard deletion and does not implicitly delete unrelated runtime stdout, stderr, diagnostics, or other observability artifacts.

Requirements
- Reuse the existing artifact authorization model for report artifacts and evidence.
- Support preview artifacts and `default_read_ref` for sensitive report presentation.
- Apply recommended retention mappings for primary, summary, structured, evidence, and related observability artifacts.
- Keep deletion artifact-system-native without undefined cascading into unrelated observability artifacts.

Relevant Implementation Notes
- Keep report artifact access within the existing artifact authorization, preview, and default-read boundaries.
- Do not add report-specific raw download assumptions for sensitive report artifacts.
- Use existing artifact APIs for pinning, unpinning, soft deletion, and hard deletion.
- Preserve separation between report artifact lifecycle behavior and unrelated runtime observability artifacts such as stdout, stderr, diagnostics, and logs.
- Preserve MM-463 and coverage IDs DESIGN-REQ-015, DESIGN-REQ-016, and DESIGN-REQ-022 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- Widening raw artifact access for sensitive reports.
- Creating report-specific authorization, retention, pinning, or deletion systems separate from the existing artifact system.
- Cascading report deletion into unrelated runtime stdout, stderr, diagnostics, logs, or other observability artifacts.

Validation
- Verify sensitive report presentation uses preview/default-read behavior where available and does not require raw download access.
- Verify `report.primary` and `report.summary` artifacts default to long retention unless policy overrides them.
- Verify `report.structured` and `report.evidence` artifacts follow standard or long retention based on report family and audit needs.
- Verify final report artifacts can be pinned and unpinned through existing artifact APIs.
- Verify report artifact deletion uses artifact-system-native soft/hard deletion without implicitly deleting unrelated runtime observability artifacts.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable operator story and does not require processing multiple specs.
- Selected mode: Runtime.
- Source design: `docs/Artifacts/ReportArtifacts.md` is treated as runtime source requirements because the Jira brief points at implementation behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-463 were found under `specs/`; specification is the first incomplete stage.

## User Story - Preserve Sensitive Report Access and Retention

**Summary**: As an operator, I want sensitive report artifacts to use existing artifact authorization, preview, retention, pinning, and deletion behavior so reports remain useful without widening raw access.

**Goal**: Report artifacts preserve safe read behavior, report-aware retention defaults, ordinary pin/unpin controls, and artifact-native deletion while keeping unrelated logs and diagnostics separate.

**Independent Test**: Create sensitive report artifacts through the artifact service, verify restricted raw access exposes preview/default-read metadata without granting raw download, verify report link types derive the expected retention defaults, verify pin/unpin keeps report retention semantics, and verify deleting a report artifact does not delete unrelated observability artifacts.

**Acceptance Scenarios**:

1. **Given** a sensitive report artifact has restricted raw access and a preview artifact, **When** a caller can read metadata but cannot access the raw artifact, **Then** the artifact metadata exposes the preview as `default_read_ref` and raw download remains denied.
2. **Given** a `report.primary` or `report.summary` artifact is created without an explicit retention override, **When** it is linked to an execution, **Then** its default retention class is `long`.
3. **Given** a `report.structured` or `report.evidence` artifact is created without an explicit retention override, **When** it is linked to an execution, **Then** its default retention class is either `standard` or `long` according to report policy and remains distinct from observability retention.
4. **Given** a final report artifact is pinned and later unpinned, **When** the existing artifact API completes both mutations, **Then** the artifact returns to its report-derived retention class rather than a generic default.
5. **Given** a report artifact and unrelated runtime observability artifacts are linked to the same execution, **When** the report artifact is deleted, **Then** only the report artifact enters deleted state and unrelated stdout, stderr, diagnostics, or log artifacts remain intact.

### Edge Cases

- A report artifact has restricted raw content but no preview artifact yet.
- A report artifact is explicitly created with a product-policy retention override.
- An artifact is linked as a report after being created with generic retention.
- A pinned report artifact is already deleted or has no active pin when unpin is requested.
- A report deletion happens while the execution still has related evidence and observability artifacts.

## Assumptions

- Existing authorization rules determine which principals can read metadata and which principals can read raw artifact bytes.
- Product policy may still explicitly override retention at artifact creation time.
- `report.structured` and `report.evidence` default to `standard` unless producers or policy choose `long`; `report.primary` and `report.summary` default to `long`.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-015 | `docs/Artifacts/ReportArtifacts.md` §7, §14 | Sensitive reports must use existing artifact authorization and preview/default-read behavior instead of widening raw access. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-016 | `docs/Artifacts/ReportArtifacts.md` §15 | Report primary and summary artifacts default to long retention; structured and evidence artifacts retain standard or long policy based on report/audit needs; final reports can be pinned and unpinned through the existing artifact API. | In scope | FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-022 | `docs/Artifacts/ReportArtifacts.md` §7, §15.3 | Report deletion remains artifact-system-native and must not implicitly delete unrelated runtime stdout, stderr, diagnostics, logs, or other observability artifacts. | In scope | FR-008, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST keep sensitive report artifacts under the existing artifact authorization model.
- **FR-002**: The system MUST expose a preview artifact as `default_read_ref` when a caller can read report metadata but cannot access restricted raw report bytes and a preview is available.
- **FR-003**: The system MUST deny raw report download or presign operations when the caller lacks restricted raw access.
- **FR-004**: The system MUST derive `long` retention for `report.primary` artifacts when no explicit retention override is provided.
- **FR-005**: The system MUST derive `long` retention for `report.summary` artifacts when no explicit retention override is provided.
- **FR-006**: The system MUST derive a non-observability retention class of `standard` or `long` for `report.structured` and `report.evidence` artifacts when no explicit retention override is provided.
- **FR-007**: The system MUST allow final report artifacts to be pinned and unpinned through the existing artifact API while restoring report-derived retention after unpin.
- **FR-008**: The system MUST soft-delete and hard-delete report artifacts through the existing artifact lifecycle path.
- **FR-009**: The system MUST NOT implicitly delete unrelated runtime stdout, stderr, diagnostics, logs, provider snapshots, or session continuity artifacts when deleting a report artifact.
- **FR-010**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-463.

### Key Entities

- **Sensitive Report Artifact**: A report artifact whose raw bytes may be restricted while metadata and preview/default-read behavior remain available to authorized readers.
- **Report Retention Class**: The retention class derived from report link type or explicit policy, including `long`, `standard`, and `pinned`.
- **Artifact Pin**: Existing artifact lifecycle state that protects an artifact from automatic deletion until it is unpinned.
- **Observability Artifact**: Runtime stdout, stderr, merged logs, diagnostics, provider snapshots, or session continuity artifacts that remain separate from curated report deletion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A restricted report artifact with a preview returns a `default_read_ref` pointing at that preview for a metadata-readable caller without raw access.
- **SC-002**: 100% of newly created `report.primary` and `report.summary` artifacts without explicit retention overrides receive `long` retention.
- **SC-003**: `report.structured` and `report.evidence` artifacts without explicit retention overrides receive `standard` or `long` retention and never inherit observability-style retention such as logs or diagnostics.
- **SC-004**: Pinning then unpinning a report-primary artifact restores the report-derived retention class.
- **SC-005**: Deleting one report artifact changes only that artifact's status and leaves unrelated observability artifacts for the same execution readable and undeleted.
- **SC-006**: MM-463 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
