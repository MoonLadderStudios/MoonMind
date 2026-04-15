# MM-330 MoonSpec Orchestration Input

## Source

- Jira issue: MM-330
- Board scope: TOOL
- Issue type: Story
- Current status at fetch time: Backlog
- Summary: MM-316: Move binary and large Temporal payloads to explicit serializers or artifacts
- Canonical source: `recommendedImports.presetInstructions` from the normalized Jira issue detail response

## Canonical MoonSpec Feature Request

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

## Supplemental Acceptance Criteria

- No covered Temporal JSON/dict-shaped payload embeds nested raw bytes.
- Large text, structured data, diagnostics, transcripts, summaries, checkpoints, and binary outputs are stored outside Temporal history with compact typed refs and metadata.
- Special JSON behavior is implemented through explicit serializers/validators or a project-standard converter.
- Bounded metadata bags remain annotations only and cannot hide workflow-control fields.
- Tests prove the intended wire shape for binary and artifact-reference cases.

Requirements
- Use intentional wire shapes for binary data.
- Prefer artifacts or claim-check references over large workflow-history payloads.
- Make serializer behavior explicit instead of relying on generic JSON coercion.

Independent Test
Run schema serialization tests for binary/base64 fields and Temporal round-trip tests for artifact-ref payloads, verifying no large body or nested raw bytes are serialized into workflow history for the covered boundaries.

Dependencies
- STORY-001

Out of Scope
- Redesigning the artifact-storage architecture itself.
- Changing task queue topology or retry policy semantics.
- Migrating unrelated non-Temporal storage formats.

Source Design Coverage
- DESIGN-REQ-017: Owns binary, large-payload, artifact-ref, and serializer policy.
- DESIGN-REQ-019: Owns bounded metadata-bag constraints where used.

Needs Clarification
- None

Notes
Temporal reliability depends on replayable, durable histories; large or accidental payload shapes create operational and compatibility risk.
