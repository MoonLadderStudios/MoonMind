# Research: Skill Zip Import

## Classification

Decision: MM-397 is a single-story runtime feature request with a declarative source design.
Evidence: `spec.md` (Input) defines one user story: upload one skill zip from the Skills Page and save it as a new skill when valid.
Rationale: The brief describes one independently testable flow with validation branches, not multiple independent product stories.
Alternatives considered: Treating the input as a broad design and routing through breakdown was rejected because all requirements support one upload/import story.
Test implications: Unit and frontend tests are sufficient for this story, with final full unit validation.

## Existing Runtime Gap

Decision: Existing upload behavior was partial and needed hardening.
Evidence: `api_service/api/routers/task_dashboard.py` exposed `/api/tasks/skills/upload`; `frontend/src/entrypoints/skills.tsx` posted to that path; tests covered basic valid zip, missing manifest, invalid root, and unsafe path.
Rationale: The partial path did not expose `/api/skills/imports`, parse YAML frontmatter, accept `skill.md`, or return import metadata.
Alternatives considered: Building a separate import module was rejected because the existing route already owns dashboard skill creation and local mirror writes.
Test implications: Add focused backend tests for canonical API behavior and frontend tests for canonical endpoint usage.

## Archive Validation

Decision: Use Python `zipfile` inspection and normalized `PurePosixPath` checks at the API boundary.
Evidence: Existing code already rejects bad zips, traversal, symlinks, duplicate paths, encrypted entries, and oversize archives.
Rationale: Import validation belongs before extraction; uploaded scripts must remain inert bytes.
Alternatives considered: Extracting first into quarantine and scanning afterward was rejected because unsafe members should be rejected before writes when possible.
Test implications: Keep path traversal and structure tests; add manifest validation tests.

## Manifest Validation

Decision: Parse Markdown YAML frontmatter with PyYAML and require `name` plus `description` before saving.
Evidence: PyYAML is already a project dependency; `moonmind/workflows/skills/materializer.py` also treats frontmatter as skill metadata.
Rationale: The MM-397 brief requires Markdown with YAML frontmatter and mandatory fields.
Alternatives considered: Header-only parsing was rejected because it would not validate YAML structure or required fields.
Test implications: Add missing frontmatter and name mismatch tests.

## Storage Model

Decision: Save valid imports into the existing configured local skill mirror and return import metadata without adding persistent tables.
Evidence: `resolve_skills_local_mirror_root()` is the current Skills Page storage path; the active technologies for this story call out no new persistent storage.
Rationale: Adding tables would exceed the scoped runtime story and conflict with the existing local skill mirror model.
Alternatives considered: Adding skill import/version tables was rejected as out of scope for this pre-release story.
Test implications: Assert files are saved under the configured local mirror and response metadata is present.
