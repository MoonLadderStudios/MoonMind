# Feature Specification: Temporal Payload Policy

**Feature Branch**: `175-temporal-payload-policy`
**Created**: 2026-04-15
**Status**: Draft
**Input**: Jira issue MM-330 on TOOL board. Original Jira preset brief:

```text
MM-330: MM-316: Move binary and large Temporal payloads to explicit serializers or artifacts

User Story
As a MoonMind operator, I need Temporal histories to carry only intentional compact payloads and artifact references so large diagnostics, transcripts, summaries, checkpoints, binary data, and special JSON fields do not bloat histories or depend on accidental encoder behavior.

Source Document
docs/Temporal/TemporalTypeSafety.md

Source Sections
- 9 Binary, large payload, and serialization policy
- 12 Approved escape hatches

Coverage IDs
- DESIGN-REQ-017
- DESIGN-REQ-019

Story Metadata
- Story ID: STORY-004
- Short name: temporal-payload-policy
- Breakdown JSON: docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json
```

## User Story & Testing

### User Story 1 - Compact Temporal Payloads (Priority: P1)

Operators need Temporal histories to contain compact typed payloads, explicit artifact references, and intentional serializers so replay and history inspection are not affected by raw bytes, large transcripts, diagnostics, summaries, checkpoints, or accidental generic JSON coercion.

**Independent Test**: Validate representative Temporal boundary models with nested raw bytes, overlarge metadata/provider summaries, explicit base64 bytes, and compact artifact refs. Raw bytes and large bodies must be rejected, while compact refs serialize as JSON.

**Acceptance Scenarios**:

1. **Given** a Temporal-facing result metadata bag contains nested raw bytes, **When** the model is validated, **Then** validation fails and instructs callers to use `Base64Bytes` or artifact refs.
2. **Given** a Temporal-facing metadata or provider-summary bag contains a large transcript, diagnostics body, summary, or checkpoint text, **When** the model is validated, **Then** validation fails and directs the caller to store the body as an artifact.
3. **Given** a managed-session response carries summary/checkpoint artifact refs, **When** the model serializes for Temporal, **Then** the JSON payload contains only the compact refs and metadata.
4. **Given** an integration signal or callback needs provider detail, **When** the provider summary is compact, **Then** it remains accepted as annotation metadata; when it carries a large body, it is rejected in favor of `payloadArtifactRef`.

## Requirements

- **FR-001**: Temporal-facing binary fields MUST use explicit serializers such as `Base64Bytes` or true top-level bytes contracts; nested raw bytes in JSON/dict-shaped payloads MUST be rejected.
- **FR-002**: Temporal-facing metadata/provider-summary escape hatches MUST be bounded JSON mappings and MUST reject values that would carry large bodies into history.
- **FR-003**: Large diagnostics, transcripts, summaries, checkpoints, binary outputs, and provider bodies MUST move through artifact references or claim-check refs rather than inline Temporal payloads.
- **FR-004**: Managed-session and agent-runtime response contracts MUST preserve compact artifact refs in their existing metadata/ref fields.
- **FR-005**: Provider-summary escape hatches for integration signals and callbacks MUST remain annotation-only and compact.

## Source Design Coverage

| Coverage ID | Mapping |
| --- | --- |
| DESIGN-REQ-017 | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-019 | FR-002, FR-005 |

## Out Of Scope

- Redesigning artifact storage.
- Renaming Temporal activity, workflow, signal, update, or query names.
- Migrating unrelated non-Temporal storage formats.
- Removing existing compatibility shims unrelated to binary/large payload policy.

## Success Criteria

- **SC-001**: Schema tests prove nested raw bytes are rejected in Temporal-facing metadata.
- **SC-002**: Schema tests prove large text bodies are rejected and compact artifact refs are accepted.
- **SC-003**: Existing Base64Bytes tests continue proving explicit binary serialization.
- **SC-004**: Focused unit verification passes for the changed schema boundaries.
