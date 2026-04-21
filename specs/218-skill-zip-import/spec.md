# Feature Specification: Skill Zip Import

**Feature Branch**: `218-skill-zip-import`
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: the Jira preset brief for MM-397, preserved verbatim below.

```text
Jira issue: MM-397 from MM project
Summary: You should be able to upload a skill zip on the Skills Page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-397 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-397: You should be able to upload a skill zip on the Skills Page

Build skill zip upload on the Skills Page. A user should be able to upload a `.zip` file containing one skill bundle. The uploaded zip is stored, safely inspected and extracted, validated against the agent skill packaging rules, and saved as a new skill only when blocking validation errors are zero. Uploaded scripts must never execute during import.

The uploaded bundle represents one skill folder and must contain exactly one root manifest named `SKILL.md` or `skill.md`. The manifest must be Markdown with YAML frontmatter. Required manifest fields are `name` and `description`; standard optional directories are `scripts/`, `references/`, and `assets/`; additional files and directories are allowed and should be classified rather than rejected.

See docs/tmp/jira-orchestration-inputs/MM-397-moonspec-orchestration-input.md for the full canonical brief, declarative design, validation rules, API response shape, implementation notes, verification expectations, and dependency record.
```

## User Story - Import Skill Zip From Skills Page

**Summary**: As a Skills Page user, I want to upload a zip file containing one valid skill bundle so MoonMind validates and saves it as a local skill without requiring manual filesystem setup.

**Goal**: Users can add local agent skill bundles from the Skills Page while archive safety, manifest correctness, collision policy, and script non-execution are enforced before any skill becomes available.

**Independent Test**: Submit valid and invalid zip uploads through the Skills Page and canonical import API, then verify valid bundles are saved under the local skills mirror and invalid archives or manifests are rejected without leaving a partial skill directory.

**Acceptance Scenarios**:

1. **Given** a user uploads a valid skill zip from the Skills Page, **When** the upload completes, **Then** the skill list refreshes and selects the imported skill.
2. **Given** a valid zip contains one skill directory with `SKILL.md` or `skill.md`, frontmatter `name`, frontmatter `description`, and optional `scripts/`, `references/`, `assets/`, or extra files, **When** the import runs, **Then** the bundle is saved as one local skill and the response includes import, skill, and version identifiers.
3. **Given** a zip is empty, invalid, too large, contains too many files, contains an oversized file, expands beyond the configured total limit, contains unsafe paths, symlinks, hardlinks, device files, duplicate normalized paths, or encrypted entries, **When** the import runs, **Then** it is rejected and no skill directory is saved.
4. **Given** a zip has zero manifests, multiple manifests, a root-level manifest without a skill directory, a manifest outside the skill root, invalid YAML frontmatter, missing `name`, missing `description`, invalid `name`, overlong `description`, or a manifest name that does not match the parent directory, **When** the import runs, **Then** it is rejected and no skill directory is saved.
5. **Given** a valid upload uses the default `reject` collision policy and a local skill with the same name already exists, **When** the import runs, **Then** it is rejected without overwriting the existing skill.
6. **Given** an uploaded bundle contains scripts, **When** the import runs, **Then** scripts are stored only as files and are not executed during import.

### Edge Cases

- `skill.md` is normalized to `SKILL.md` when the bundle is saved.
- Standard directories are recognized but extra files are accepted and preserved.
- Ignorable platform artifacts such as `__MACOSX` and `.DS_Store` do not count as skill content.
- The canonical API is `/api/skills/imports`; existing Skills Page interactions use that endpoint.

## Assumptions

- The current runtime storage target is the configured local skill mirror, so returned version identifiers are deterministic import metadata rather than rows in a new persistent version table.
- `collision_policy=new_version` is accepted by the import contract, but durable immutable version history remains constrained by the existing local mirror storage model unless later storage work introduces a version table.

## Source Design Requirements

- **DESIGN-REQ-001** (MM-397 brief, semantics): One uploaded zip corresponds to one skill folder and saves only when blocking errors are zero. Scope: in scope. Maps to FR-001, FR-009.
- **DESIGN-REQ-002** (MM-397 brief, import API): The canonical import endpoint accepts multipart zip uploads and a collision policy. Scope: in scope. Maps to FR-002, FR-011.
- **DESIGN-REQ-003** (MM-397 brief, archive limits): The importer enforces zip size, file count, single-file size, and total uncompressed size limits. Scope: in scope. Maps to FR-003.
- **DESIGN-REQ-004** (MM-397 brief, archive safety): Unsafe archive entries are rejected. Scope: in scope. Maps to FR-004.
- **DESIGN-REQ-005** (MM-397 brief, structure rules): The zip must contain one skill directory and exactly one root manifest named `SKILL.md` or `skill.md`. Scope: in scope. Maps to FR-005.
- **DESIGN-REQ-006** (MM-397 brief, manifest rules): The manifest requires Markdown YAML frontmatter with valid `name` and `description`. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-007** (MM-397 brief, file classes): Standard directories and additional files are preserved. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-008** (MM-397 brief, execution rule): Uploaded scripts are not executed during import. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-009** (MM-397 brief, success/error response): Saved imports expose import, skill, version, description, and warning metadata. Scope: in scope. Maps to FR-010.
- **DESIGN-REQ-010** (MM-397 brief, persistence tables): New durable skill import, skill version, and skill file tables are out of scope because the existing Skills Page stores local skills in the configured mirror and this story introduces no new persistent storage. Scope: out of scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST import exactly one skill folder from each uploaded zip and MUST save it only when blocking validation errors are zero.
- **FR-002**: The system MUST expose a canonical `POST /api/skills/imports` multipart endpoint with required `file` and optional `collision_policy` fields.
- **FR-003**: The system MUST reject archives over 50 MB, archives with more than 500 files, any file over 25 MB uncompressed, or archives expanding over 200 MB total.
- **FR-004**: The system MUST reject absolute paths, parent traversal, symlinks, hardlinks, device files, duplicate normalized paths, and encrypted entries.
- **FR-005**: The system MUST require one skill directory containing exactly one root manifest named `SKILL.md` or `skill.md`.
- **FR-006**: The system MUST validate the manifest as UTF-8 Markdown with YAML frontmatter containing a valid `name` and non-empty `description`, and the manifest `name` MUST match the skill directory.
- **FR-007**: The system MUST preserve standard optional directories, markdown files, assets, references, scripts, and additional files in the saved skill.
- **FR-008**: The system MUST NOT execute uploaded scripts during import.
- **FR-009**: The system MUST reject invalid imports without leaving a partial skill directory.
- **FR-010**: The system MUST return saved import metadata including import id, saved status, skill id, version id, version number, name, description, and warnings.
- **FR-011**: The system MUST reject same-name imports by default when `collision_policy=reject`.
- **FR-012**: The Skills Page MUST upload zip files through the canonical import endpoint, refresh the skill list, and select the uploaded skill on success.
- **FR-013**: The implementation MUST preserve Jira issue key MM-397 in Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Backend tests cover valid import metadata, `skill.md` normalization, frontmatter validation, name mismatch rejection, default collision rejection, invalid structure rejection, and unsafe path rejection.
- **SC-002**: Frontend tests show Skills Page zip upload posts to `/api/skills/imports`, refreshes the list, and selects the uploaded skill.
- **SC-003**: Focused unit validation for the skill import route and Skills Page passes through the repo test runner.
- **SC-004**: Final verification traces implementation evidence back to MM-397 and all in-scope DESIGN-REQ mappings.
