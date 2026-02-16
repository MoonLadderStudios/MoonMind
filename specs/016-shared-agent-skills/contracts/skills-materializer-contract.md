# Runtime Contract: Skill Resolver + Materializer

## Purpose

Resolve the effective skill set for a run and materialize one shared active directory consumed by both Codex and Gemini adapters.

## Resolver Input Contract

The resolver receives:

- `run_id` (required)
- `queue_profile_id` (required)
- `job_skill_overrides` (optional list of `skill_name[:version]`)
- `default_skill_policy` (required global profile)
- `registry_snapshot_id` (required)

## Resolver Output Contract

The resolver returns:

- `run_id`
- `selection_source` (`job_override|queue_profile|global_default`)
- `selected_skills` (ordered list of `{skill_name, version, required}`)
- `registry_entries` (resolved immutable records with `source_uri`, `content_hash`, `signature`)

## Resolver Error Contract

Resolver must fail with non-retryable error when:

- no selected skills remain after policy evaluation,
- selected skill is missing/disabled in registry,
- duplicate `skill_name` exists in selected set.

## Materializer Input Contract

The materializer receives:

- `run_id`
- `run_workspace_root` (e.g. `/work/runs/<run_id>`)
- `resolved_skill_entries` (from resolver)
- `cache_root` (e.g. `/var/lib/moonmind/skill_cache`)
- `verification_mode` (`hash_only|hash_and_signature`)

## Materializer Processing Contract

For each selected skill:

1. Fetch artifact from `source_uri` if cache miss.
2. Validate archive/folder structure contains top-level skill directory and `SKILL.md`.
3. Verify `content_hash` (and signature when configured).
4. Materialize immutable cache path at `<cache_root>/<content_hash>/<skill_name>/`.
5. Link `<run_workspace_root>/skills_active/<skill_name>` to immutable cache skill path.

Then create both adapter links:

- `<run_workspace_root>/.agents/skills -> ../skills_active`
- `<run_workspace_root>/.gemini/skills -> ../skills_active`

## Materializer Output Contract

Returns:

- `status` (`ready|failed`)
- `run_workspace` object with `skills_active_path`, `codex_adapter_path`, `gemini_adapter_path`
- `resolved_hashes` map `{skill_name: content_hash}`
- `materialization_duration_ms`
- `warnings` (optional)

## Materializer Error Contract

Materializer must fail before activation when:

- hash/signature validation fails,
- `SKILL.md` missing or invalid,
- skill name mismatch between directory and metadata,
- adapter symlink invariant check fails.

## Observability Contract

Emit one structured event per run with:

- `run_id`
- `selection_source`
- `selected_skills`
- `resolved_versions`
- `resolved_hashes`
- `status`
- `error_code` and `error_message` on failure
- `duration_ms`
