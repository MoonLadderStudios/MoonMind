# Data Model: Skill Zip Import

## SkillImport

- `import_id`: Generated identifier for the import attempt returned to the client.
- `status`: `saved` for successful imports.
- `original_zip`: Uploaded zip payload held only for validation and extraction during the request.
- `collision_policy`: `reject` by default; `new_version` is accepted by the contract for future version-aware storage.
- `warnings`: Non-blocking validation issues; currently empty for saved imports.

Validation:

- File must be a non-empty valid zip.
- Zip size must not exceed 50 MB.
- File count must not exceed 500.
- Any single uncompressed file must not exceed 25 MB.
- Total uncompressed size must not exceed 200 MB.
- Unsafe archive entries are blocking errors.

## SkillBundle

- `skill_name`: Name from manifest frontmatter and parent directory.
- `root_prefix`: Single top-level directory in the zip.
- `manifest_path`: `SKILL.md` or `skill.md` at the skill root.
- `description`: Manifest frontmatter description.
- `files`: Normalized file inventory preserved into the local mirror.

Validation:

- Exactly one top-level skill directory is required.
- Exactly one root manifest named `SKILL.md` or `skill.md` is required.
- `skill.md` is saved as `SKILL.md`.
- Standard directories `scripts/`, `references/`, and `assets/` are preserved.
- Additional files are preserved.

## SkillManifest

- `name`: Required; lowercase letters, digits, and single hyphens only; 1-64 characters; must match the parent directory.
- `description`: Required; 1-1024 characters.
- `license`: Optional.
- `compatibility`: Optional.
- `metadata`: Optional.
- `allowed-tools`: Optional.

Validation:

- Manifest must be UTF-8 Markdown.
- YAML frontmatter must start and end with `---`.
- YAML frontmatter must parse to a mapping.

## State Transitions

```text
received -> inspected -> extracted -> validated -> saved
received -> inspected -> rejected
received -> inspected -> extracted -> rejected
received -> inspected -> extracted -> validated -> rejected
```

No uploaded script execution occurs in any state.
