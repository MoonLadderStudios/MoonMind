# Research: Task Presets Catalog

## Decision: Catalog storage + versioning layout
- **Decision**: Model templates and versions separately. `task_step_templates` stores identity/scope metadata; `task_step_template_versions` stores immutable blueprints, inputs schema, and annotations. Seed YAML files hydrate both tables at migration time.
- **Rationale**: Separation keeps slug ownership/governance distinct from version history, allows soft-deleting templates without losing version audit, and simplifies "latest version" lookups.
- **Alternatives considered**:
  - *Single table with JSON `versions[]`*: complicates SQL filtering and RBAC per version; poor for Alembic migrations.
  - *File backed catalog only*: works for defaults but prevents UI/CLI creation flows and per-user scopes.

## Decision: Expansion + templating engine
- **Decision**: Use Jinja2 sandboxed environment with a curated set of filters to substitute `{{inputs.*}}` and built-ins (now, iso_today, counter). Templates compile once at load; expansion clones environment with per-request variables.
- **Rationale**: Jinja2 already bundled via FastAPI for templates, has mature escaping and conditionals if we later enable optional `when` logic, and sandbox mode lets us disallow unsafe attributes.
- **Alternatives considered**:
  - *`str.format` / f-string style*: lacks sandboxing, no conditionals, error-prone default handling.
  - *Custom DSL*: higher maintenance and not needed for v1 scope.

## Decision: Secret scrubbing + parameterization
- **Decision**: Reuse existing secret detectors from `moonmind.utils.secrets` (heuristics for `ghp_`, `token=`, PEM blocks). On save, run text through detectors and refuse to persist until scrubbed; UI proactively highlights hits. Provide quick actions to convert repeated strings into input variables before submission.
- **Rationale**: Reusing shared detectors reduces false negatives, and blocking server-side ensures CLI/MCP clients comply even if UI misses a case.
- **Alternatives considered**:
  - *Rely only on UI redaction*: brittle for CLI/MCP flows.
  - *Encrypt secret-laden templates*: defeats the goal of shareable presets and would still leak when expanded.

## Decision: Deterministic step IDs
- **Decision**: Format `stepId` as `tpl:{slug}:{version}:{index:02d}` plus suffix when duplicate indexes exist (e.g., `:a`, `:b`). Include hash of normalized instructions when template is inserted multiple times in one task.
- **Rationale**: Determinism simplifies diff review, templates remain auditable, and duplicates remain unique when repeated.
- **Alternatives considered**:
  - *Random UUIDs*: lose human readability and hamper cross-run comparisons.
  - *Delegating to UI*: duplicates logic across clients and risks collisions.
