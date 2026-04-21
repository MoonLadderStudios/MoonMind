# Feature Specification: Skill Zip Import

**Feature Branch**: `218-skill-zip-import`
**Created**: 2026-04-21
**Status**: Draft
**Input**: User description: the Jira preset brief for MM-397, preserved verbatim below.

```text
# MM-397 MoonSpec Orchestration Input

## Source

- Jira issue: MM-397
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: You should be able to upload a skill zip on the Skills Page
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-397 from MM project
Summary: You should be able to upload a skill zip on the Skills Page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-397 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-397: You should be able to upload a skill zip on the Skills Page

Build skill zip upload on the Skills Page. A user should be able to upload a `.zip` file containing one skill bundle. The uploaded zip is stored, safely inspected and extracted, validated against the agent skill packaging rules, and saved as a new skill only when blocking validation errors are zero. Uploaded scripts must never execute during import.

The uploaded bundle represents one skill folder and must contain exactly one root manifest named `SKILL.md` or `skill.md`. The manifest must be Markdown with YAML frontmatter. Required manifest fields are `name` and `description`; standard optional directories are `scripts/`, `references/`, and `assets/`; additional files and directories are allowed and should be classified rather than rejected.

### User Story

As a Skills Page user, I want to upload a zip file containing a skill bundle so MoonMind can validate and save the bundle as a new skill without requiring manual filesystem setup.

### Acceptance Criteria

- The Skills Page exposes an upload path for a skill zip file.
- The import API accepts `multipart/form-data` with a required binary `file` field whose accepted MIME type is `application/zip`.
- The import API supports a `collision_policy` enum with values `reject` and `new_version`, defaulting to `reject`.
- One uploaded zip corresponds to exactly one skill folder.
- The importer stores the original zip before extraction.
- The importer rejects unsafe archives, including absolute paths, parent traversal, symlinks, hardlinks, device files, duplicate normalized paths, and encrypted entries.
- The importer applies archive limits: 50 MB max zip size, 500 max files, 25 MB max single uncompressed file size, and 200 MB max total uncompressed size.
- The importer safely extracts the zip and resolves a single skill root directory.
- The importer requires exactly one case-insensitive manifest named `SKILL.md` or `skill.md` at the skill root.
- The importer validates that the manifest is Markdown with YAML frontmatter.
- The manifest requires `name` and `description`.
- The manifest `name` must be a non-empty lowercase/digit/hyphen identifier, 1-64 characters, with no leading, trailing, or consecutive hyphens, and must match the parent directory.
- The manifest `description` must be non-empty and capped at 1024 characters.
- Optional manifest fields include `license`, `compatibility`, `metadata`, and `allowed-tools`.
- Standard directories `scripts/`, `references/`, and `assets/` are recognized.
- Additional files and directories are allowed and classified as `other` rather than rejected solely for being non-standard.
- Recommendation lint warnings are non-blocking and should cover large `SKILL.md` files, deep file references, and missing recommended sections such as `When to use`, `How to run`, and `Examples`.
- The importer saves the skill only when `blocking_error_count == 0`.
- Rejected imports return validation issues without saving the skill.
- Saved imports return import, skill, and version identifiers plus validation warnings.
- Uploaded scripts are never executed during import.

### Declarative Design

```yaml
skill_import_design:
  version: 1

  semantics:
    one_zip_equals_one_skill: true
    save_only_when_blocking_errors_are_zero: true
    scripts_are_executed_during_import: false
    name_collision_policy: reject

  api:
    create_import:
      method: POST
      path: /api/skills/imports
      content_type: multipart/form-data
      fields:
        file:
          type: binary
          required: true
          accepted_mime_types: ["application/zip"]
        collision_policy:
          type: enum
          values: ["reject", "new_version"]
          default: "reject"
    get_import:
      method: GET
      path: /api/skills/imports/{import_id}

  state_machine:
    states:
      - received
      - quarantined
      - extracted
      - validated
      - saved
      - rejected
    transitions:
      - from: received
        to: quarantined
      - from: quarantined
        to: extracted
      - from: extracted
        to: validated
      - from: validated
        to: saved
        when: "blocking_error_count == 0"
      - from: validated
        to: rejected
        when: "blocking_error_count > 0"

  file_classes:
    manifest:
      match: ["{root}/SKILL.md", "{root}/skill.md"]
    script:
      match: ["{root}/scripts/**"]
    reference:
      match: ["{root}/references/**"]
    asset:
      match: ["{root}/assets/**"]
    markdown:
      match: ["{root}/**/*.md"]
    other:
      match: ["{root}/**"]

  workflow:
    - id: store_original
      action: store_blob
      input: request.file
      output: original_zip_uri
    - id: inspect_archive
      action: inspect_zip
      validators: ["archive_limits", "archive_safety"]
    - id: safe_extract
      action: safe_unzip
      output: extracted_tree_uri
    - id: discover_root
      action: resolve_single_top_level_folder
      output: root_dir
    - id: validate_structure
      action: validate_tree
      validators: ["structure_rules"]
    - id: validate_manifest
      action: parse_markdown_frontmatter
      validators: ["manifest_rules", "spec_adapter"]
    - id: classify_files
      action: classify_tree
      output: file_inventory
    - id: validate_recommendations
      action: lint_skill_bundle
      validators: ["recommendation_rules"]
    - id: persist
      action: persist_skill_bundle
      when: "blocking_error_count == 0"
      config:
        create_skill_if_missing: true
        create_version_if_existing_and_collision_policy_is: "new_version"
        reject_if_existing_and_collision_policy_is: "reject"
        set_latest_version_pointer: true
        set_default_version_pointer_when_first_version: true
    - id: finalize
      action: write_import_result

  validators:
    archive_limits:
      severity: error
      rules:
        max_zip_bytes: 52428800
        max_file_count: 500
        max_single_uncompressed_file_bytes: 26214400
        max_total_uncompressed_bytes: 209715200

    archive_safety:
      severity: error
      rules:
        reject_absolute_paths: true
        reject_parent_traversal: true
        reject_symlinks: true
        reject_hardlinks: true
        reject_device_files: true
        reject_duplicate_normalized_paths: true
        reject_encrypted_entries: true
        drop_patterns:
          - "__MACOSX/**"
          - "**/.DS_Store"

    structure_rules:
      severity: error
      rules:
        require_single_top_level_folder: true
        require_exactly_one_manifest_case_insensitive: true
        require_manifest_at_skill_root: true
        allow_standard_directories: ["scripts", "references", "assets"]
        allow_additional_files_and_directories: true

    manifest_rules:
      severity: error
      rules:
        format: markdown_with_yaml_frontmatter
        required_fields:
          name:
            type: string
            min_length: 1
            max_length: 64
            pattern: "^[a-z0-9]+(?:-[a-z0-9]+)*$"
            must_match_parent_directory: true
          description:
            type: string
            min_length: 1
            max_length: 1024
        optional_fields:
          license:
            type: string
          compatibility:
            type: string
            max_length: 500
          metadata:
            type: "map<string,string>"
          allowed-tools:
            type: string

    spec_adapter:
      severity: error
      rules:
        validator: "skills-ref"
        invocation: "skills-ref validate {root_dir}"

    recommendation_rules:
      severity: warning
      rules:
        warn_if_skill_md_lines_gt: 500
        warn_if_file_reference_depth_gt: 1
        warn_if_missing_sections:
          - "When to use"
          - "How to run"
          - "Examples"
```

### Persistence Model

- Store the original zip at `skills/imports/{import_id}/original.zip`.
- Store the extracted bundle at `skills/{tenant_id}/{skill_name}/versions/{version}/bundle/`.
- Record import status, original zip URI, extracted tree URI, validation errors, warnings, creator, and creation time.
- Store skills uniquely by tenant and name.
- Store immutable skill versions with manifest JSON, manifest Markdown, root directory, file count, content hash, original zip URI, extracted bundle URI, creator, and creation time.
- Store skill files uniquely by version and path, including file class, hash, size, and media type.
- Maintain `default_version_id` and `latest_version_id` pointers for each skill.

### API Response Expectations

Successful import:

```yaml
status_code: 201
body:
  import_id: string
  status: "saved"
  skill_id: string
  version_id: string
  version_number: integer
  name: string
  description: string
  warnings: "validation_issue[]"
```

Rejected import:

```yaml
status_code: 422
body:
  import_id: string
  status: "rejected"
  errors: "validation_issue[]"
  warnings: "validation_issue[]"
```

Validation issue fields:

```yaml
validation_issue:
  fields:
    - code
    - severity
    - path
    - message
    - rule_id
```

### Relevant Implementation Notes

- This story modifies the Skills Page and the trusted skill import path, not agent runtime execution.
- Import should only store, extract, validate, classify, hash, and persist uploaded skill bundles.
- Script execution belongs later in the normal sandbox/runtime path and must not happen during upload/import.
- Validation should separate blocking standard compliance errors from non-blocking recommendations.
- The design should stay aligned with the current agent skill packaging model and canonical `.agents/skills` active-path semantics.
- Existing skill system docs distinguish agent instruction bundles under `.agents/skills` from executable `tool.type = "skill"` contracts; this story is about importing agent skill bundles.
- Preserve MM-397 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Verification

- Verify a valid skill zip can be uploaded from the Skills Page and saved as a new skill.
- Verify a valid skill zip with optional `scripts/`, `references/`, `assets/`, and additional files is accepted and classified.
- Verify unsafe archive entries are rejected and no skill is saved.
- Verify archives over configured size/count limits are rejected and no skill is saved.
- Verify an upload with zero manifests is rejected.
- Verify an upload with multiple manifests is rejected.
- Verify a manifest outside the skill root is rejected.
- Verify invalid manifest frontmatter, missing `name`, missing `description`, invalid `name`, overlong `name`, overlong `description`, and parent-directory mismatch are rejected.
- Verify warnings do not block save when blocking errors are zero.
- Verify collision policy `reject` prevents overwriting an existing skill.
- Verify collision policy `new_version` creates a new immutable version when supported by implementation scope.
- Verify uploaded scripts are not executed during import.
- Preserve MM-397 in MoonSpec artifacts, verification output, commit text, and pull request metadata.

### Dependencies

- None exposed by the trusted MM-397 Jira issue response at fetch time.
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
- **DESIGN-REQ-011** (MM-397 brief, get_import and durable rejected-import response): A durable `GET /api/skills/imports/{import_id}` status resource and import-id-bearing rejected response bodies are out of scope because this story does not add persistent import records. Scope: out of scope.

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
