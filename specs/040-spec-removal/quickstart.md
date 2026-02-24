# Quickstart: Canonical Workflow Surface Naming

## 1. Confirm scope and mode alignment

- Read `docs/SpecRemovalPlan.md` and this feature's `spec.md`.
- Validate the selected orchestration mode requirement:
  - In runtime mode, confirm this feature intentionally excludes runtime behavior changes in this pass.
  - Record required runtime follow-up implementation and validation in `specs/040-spec-removal/tasks.md` (`T040`/`T041`).

## 2. Run baseline discovery before edits

```bash
rg -l "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|spec_automation|moonmind\\.spec_workflow|var/artifacts/spec_workflows" \
  docs specs \
  --glob '*.md' --glob '*.yaml' --glob '*.yml'
```

If `rg` is not available in your local environment, run:

```bash
grep -R -nE "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|spec_automation|moonmind\\.spec_workflow|var/artifacts/spec_workflows" \
  docs specs
```

## 3. Apply canonical naming updates

- Replace legacy tokens only in files listed in `docs/SpecRemovalPlan.md`.
- Preserve `docs/SpecRemovalPlan.md` historical sections for approved legacy references only.
- Keep file structure and meaning stable; do not change unrelated technical content.

## 4. Generate required planning artifacts

- Fill `research.md` with migration decisions and trade-offs.
- Fill `data-model.md` with planning entities for verification and traceability.
- Generate `contracts/requirements-traceability.md` covering all `DOC-REQ-*`.

## 5. Verify completion

```bash
rg -l "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|spec_automation|moonmind\\.spec_workflow|var/artifacts/spec_workflows" \
  docs specs \
  --glob '*.md' --glob '*.yaml' --glob '*.yml' \
| sed -e 's#^#match: #'  # includes expected historical appendix reference
```

- Expected non-appendix result: zero matches outside approved `docs/SpecRemovalPlan.md` sections.
- Record any residual matches in a migration follow-up note.

For runtime follow-up validation (`T040`/`T041`), run:

```bash
grep -R -nE "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|spec_automation|moonmind\\.spec_workflow|var/artifacts/spec_workflows" \
  api_service services tests \
  --include='*.py' --include='*.md' --include='*.yaml' --include='*.yml'
```

- Runtime validation should pass with no matches outside intentionally retained historical references.

## 6. Final handoff

- Ensure `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/requirements-traceability.md` are all present and coherent.
- Confirm `DOC-REQ-*` count in spec equals rows in `contracts/requirements-traceability.md`.
- Keep all legacy-code surface changes for the explicit runtime follow-up tasks tracked in this feature (`T040`/`T041`).
