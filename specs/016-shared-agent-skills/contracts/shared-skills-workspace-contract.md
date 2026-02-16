# Runtime Contract: Shared Skills Workspace Layout

## Required Layout

For each run (`<run_id>`), MoonMind must create:

```text
/work/runs/<run_id>/
├── skills_active/
│   ├── <skill-a> -> /var/lib/moonmind/skill_cache/<sha_a>/<skill-a>
│   └── <skill-b> -> /var/lib/moonmind/skill_cache/<sha_b>/<skill-b>
├── .agents/
│   └── skills -> ../skills_active
└── .gemini/
    └── skills -> ../skills_active
```

## Invariants

- `skills_active/` exists for every ready run.
- Every entry in `skills_active/` is a symlink to immutable cache content.
- `.agents/skills` and `.gemini/skills` are symlinks and both resolve to the same `skills_active` path.
- No runtime step writes mutable content under immutable cache roots after verification.

## Preflight Validation Contract

Before worker execution:

1. Verify `skills_active` exists and is non-empty.
2. Verify both adapter symlinks exist and target the same path.
3. Verify each selected skill target contains `SKILL.md`.
4. Verify duplicate skill names are absent.

If any check fails, run must stop before CLI invocation.

## Cleanup Contract

- Run workspace (`/work/runs/<run_id>`) is ephemeral and can be removed post-run by retention policy.
- Cache root (`/var/lib/moonmind/skill_cache/`) is persistent and pruned by digest-aware GC.
- GC cannot remove cache entries referenced by active run workspaces.

## Compatibility Contract

- Existing stage routing still uses workflow policy controls (`SPEC_WORKFLOW_ALLOWED_SKILLS`, stage overrides).
- Legacy `.codex/skills` assumptions are deprecated for run-scoped execution.
- Codex and Gemini discovery behavior is normalized through adapter symlink layout, not global per-user installs.
