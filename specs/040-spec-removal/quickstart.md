# Quickstart: Canonical Workflow Surface Naming

## 1. Confirm selected orchestration mode and scope

- Selected mode for this feature: **runtime implementation mode**.
- Docs mode remains aligned by using the same canonical token map and verification gates.
- Review:
  - `docs/SpecRemovalPlan.md`
  - `specs/040-spec-removal/spec.md`
  - `specs/040-spec-removal/plan.md`

## 2. Baseline discovery (docs/spec surfaces)

```bash
rg -n "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|moonmind\.spec_workflow|var/artifacts/spec_workflows" \
  docs specs \
  --glob '*.md' --glob '*.yaml' --glob '*.yml'
```

## 3. Baseline discovery (runtime surfaces)

```bash
rg -n "SPEC_WORKFLOW_|SPEC_AUTOMATION_|/api/spec-automation|/api/workflows/speckit|SpecWorkflow|spec_workflow|spec_workflows|spec-automation|moonmind\.spec_workflow|var/artifacts/spec_workflows" \
  api_service services tests celery_worker \
  --glob '*.py' --glob '*.md' --glob '*.yaml' --glob '*.yml' --glob '*.sh'
```

## 4. Implement canonical naming updates

- Apply canonical replacements to planned docs/spec/runtime surfaces.
- Preserve legacy wording only in explicit historical traceability sections.
- Do not introduce compatibility transforms that alter execution semantics.

## 5. Validate docs/runtime alignment

Run docs/spec verification:

```bash
./tools/verify_workflow_naming.sh \
  --mode docs-spec \
  --exceptions-file specs/040-spec-removal/contracts/legacy-naming-exceptions.regex
```

Run runtime verification:

```bash
./tools/verify_workflow_naming.sh \
  --mode runtime \
  --exceptions-file specs/040-spec-removal/contracts/legacy-naming-exceptions.regex
```

Run unit tests (required command):

```bash
./tools/test_unit.sh
```

Expected criteria:
- `docs-spec` mode passes with only approved historical exceptions.
- `runtime` mode passes after runtime migration tasks (`T004-T006`, `T017-T021`) complete.
- `./tools/test_unit.sh` exits successfully.

## 6. Handoff checklist

- `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` are synchronized.
- `contracts/requirements-traceability.md` contains one row for each `DOC-REQ-001` through `DOC-REQ-011`.
- Verification evidence records:
  - docs/spec scan result
  - runtime scan result
  - unit test result via `./tools/test_unit.sh`

## 7. Validation evidence (2026-03-01)

- Command: `./tools/verify_workflow_naming.sh --mode docs-spec --exceptions-file specs/040-spec-removal/contracts/legacy-naming-exceptions.regex`
  - Result: `PASS` (`[docs-spec] PASS: No unapproved legacy naming matches found.`)
- Command: `./tools/verify_workflow_naming.sh --mode runtime --exceptions-file specs/040-spec-removal/contracts/legacy-naming-exceptions.regex`
  - Result: `FAIL` (legacy tokens still present across runtime surfaces, including `moonmind/config/settings.py`, `api_service/api/routers/spec_automation.py`, and `api_service/main.py`)
- Command: `./tools/test_unit.sh`
  - Result: `PASS` (`895 passed, 8 subtests passed`)
