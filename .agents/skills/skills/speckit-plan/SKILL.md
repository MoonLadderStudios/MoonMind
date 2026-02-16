---
name: speckit-plan
description: Execute the implementation planning workflow using the plan template to generate design artifacts.
---

# Spec Kit Plan Skill

## When to Use

- The feature spec is ready and you need a technical implementation plan.

## Inputs

- `specs/<feature>/spec.md`
- Repo context and `.specify/` templates
- User-provided constraints or tech preferences (if any)

If the spec is missing, ask the user to run speckit-specify first.

## Workflow

1. **Setup**: Run `.specify/scripts/bash/setup-plan.sh --json` from repo root and parse JSON for FEATURE_SPEC, IMPL_PLAN, SPECS_DIR, BRANCH. For single quotes in args like "I'm Groot", use escape syntax: e.g 'I'\''m Groot' (or double-quote if possible: "I'm Groot").

2. **Load context**: Read FEATURE_SPEC and `.specify/memory/constitution.md`. Load IMPL_PLAN template (already copied).

3. **Execute plan workflow**: Follow the structure in IMPL_PLAN template to:
   - Fill Technical Context (mark unknowns as "NEEDS CLARIFICATION")
   - Fill Constitution Check section from constitution
   - Evaluate gates (ERROR if violations unjustified)
   - Phase 0: Generate research.md (resolve all NEEDS CLARIFICATION)
   - Phase 1: Generate data-model.md, contracts/, quickstart.md
   - Phase 1: Optionally refresh shared agent context only when explicitly requested by the user
   - Re-evaluate Constitution Check post-design
   - If `spec.md` contains `DOC-REQ-*`, generate `contracts/requirements-traceability.md` mapping each `DOC-REQ-*` to FR IDs, planned implementation surfaces, and validation strategy
   - Fail planning if any `DOC-REQ-*` remains unmapped or lacks planned validation

4. **Stop and report**: Command ends after Phase 2 planning. Report branch, IMPL_PLAN path, and generated artifacts.

## Phases

### Phase 0: Outline & Research

1. **Extract unknowns from Technical Context** above:
   - For each NEEDS CLARIFICATION → research task
   - For each dependency → best practices task
   - For each integration → patterns task

2. **Generate and dispatch research agents**:

   ```text
   For each unknown in Technical Context:
     Task: "Research {unknown} for {feature context}"
   For each technology choice:
     Task: "Find best practices for {tech} in {domain}"
   ```

3. **Consolidate findings** in `research.md` using format:
   - Decision: [what was chosen]
   - Rationale: [why chosen]
   - Alternatives considered: [what else evaluated]

**Output**: research.md with all NEEDS CLARIFICATION resolved

### Phase 1: Design & Contracts

**Prerequisites:** `research.md` complete

1. **Extract entities from feature spec** → `data-model.md`:
   - Entity name, fields, relationships
   - Validation rules from requirements
   - State transitions if applicable

2. **Generate API contracts** from functional requirements:
   - For each user action → endpoint
   - Use standard REST/GraphQL patterns
   - Output OpenAPI/GraphQL schema to `/contracts/`

3. **Optional shared agent-context refresh (explicit opt-in)**:
   - Skip this step unless the user explicitly requests a shared context refresh.
   - Use `.specify/scripts/bash/update-agent-context.sh <agent_type> --write-shared` only when intentionally rebuilding shared AGENTS context on `main`/CI.
   - Do not trigger shared/root AGENTS updates by default during feature planning.

**Output**: data-model.md, /contracts/\*, quickstart.md, and `requirements-traceability.md` when `DOC-REQ-*` exists

## Key rules

- Use absolute paths
- ERROR on gate failures or unresolved clarifications

## Outputs

- `specs/<feature>/plan.md` (filled implementation plan)
- `specs/<feature>/research.md`
- `specs/<feature>/data-model.md`
- `specs/<feature>/contracts/` (API schemas)
- `specs/<feature>/contracts/requirements-traceability.md` when `DOC-REQ-*` exists in `spec.md`
- `specs/<feature>/quickstart.md`
- Optional shared AGENTS refresh only when explicitly requested

## Next Steps

After planning:

- **Generate tasks** with speckit-tasks.
- **Create a checklist** with speckit-checklist when a quality gate is needed.
