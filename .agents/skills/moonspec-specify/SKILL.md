---
name: moonspec-specify
description: Create or update a Moon Spec feature specification from a natural language feature request or referenced declarative design. Use when the user asks to run or reproduce `/speckit.specify`, create a `spec.md`, initialize a feature directory under `specs/`, preserve the original feature request for later verification, map source design requirements, or generate a single-story Moon Spec specification with a requirements quality checklist.
---

# MoonSpec Specify

Use this skill to perform the Moon Spec specify workflow.

## When To Use

Use this skill when the user wants to create or update a Moon Spec `spec.md` from:

- A natural language feature request.
- A referenced declarative design, contract, or source requirements document.
- A request to run or reproduce `/speckit.specify`.

Do not use this skill to split a broad design into multiple specs; use `/speckit.breakdown` for that.

## Inputs

- Treat the user's request text as the feature description.
- Do not ask the user to repeat the request unless it is empty or no independently testable story can be derived.
- If the feature description is empty, stop with: `ERROR "No feature description provided"`.
- Preserve the original feature description verbatim in the generated spec's `**Input**` field. Do not summarize or normalize it; `/speckit.verify` relies on this source request.
- If the request references a source design file, contract, or declarative design artifact, resolve and read it before generating the spec.
- Default the intent to implemented system behavior. Use documentation-only intent only when the user explicitly asks for docs-only or documentation alignment work.

## Intent And Source Designs

Before creating artifacts, classify the request:

- `runtime`: The spec should describe behavior the product/system must implement and validate. This is the default.
- `docs`: The spec should describe documentation changes only. Use this only when the user explicitly asks for docs-only output.

If the prompt says to implement, build, support, or verify behavior described by a document, treat the document as source requirements for `runtime` intent, not as the implementation target.

For source-backed requests:

1. Resolve the referenced path relative to the repo root unless the path is absolute.
2. Read the source artifact before drafting the spec.
3. Extract only concrete, testable source requirements:
   - Normative terms such as must, shall, required, forbidden, cannot, should when used as a requirement.
   - Behavior tables, acceptance criteria, invariants, limits, failure handling, and compatibility constraints.
   - Explicit non-goals or exclusions that constrain scope.
4. Assign stable IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, and so on.
5. If the source artifact contains multiple independent stories, do not collapse them into one spec. Ask the user to choose one story, or direct them to `/speckit.breakdown` when the goal is to extract stories from a broader technical or declarative design.
6. If a source requirement is intentionally out of scope for the selected story, keep it in the source mapping as out of scope with a short rationale. Do not silently drop it.

Complete source reading, requirement extraction, and single-story selection before creating the feature directory.

## Pre-Spec Hooks

Before creating the spec, check for extension hooks:

1. If `.specify/extensions.yml` exists, read it and look for `hooks.before_specify`.
2. If the YAML cannot be parsed or is invalid, skip hook checking silently.
3. Ignore hooks where `enabled` is explicitly `false`; hooks without `enabled` are enabled.
4. Do not evaluate non-empty `condition` expressions. Treat hooks with no condition, null condition, or empty condition as executable. Skip hooks with non-empty conditions.
5. For each executable hook:
   - Optional hook (`optional: true`): print:

```markdown
## Extension Hooks

**Optional Pre-Hook**: {extension}
Command: `/{command}`
Description: {description}

Prompt: {prompt}
To execute: `/{command}`
```

   - Mandatory hook (`optional: false`): print:

```markdown
## Extension Hooks

**Automatic Pre-Hook**: {extension}
Executing: `/{command}`
EXECUTE_COMMAND: {command}

Wait for the result of the hook command before proceeding to the Outline.
```

If a successful `before_specify` hook created or switched branches and returns `BRANCH_NAME` or `FEATURE_NUM`, keep them for reference only. The branch name does not determine the spec directory name. If the user explicitly provides `GIT_BRANCH_NAME`, pass it to the hook unchanged.

## Create Feature Directory

Generate a short name first:

- Use 2-4 words.
- Prefer action-noun style: `add-user-auth`, `oauth2-api-integration`, `analytics-dashboard`, `fix-payment-timeout`.
- Preserve meaningful technical terms and acronyms.

Resolve `SPECIFY_FEATURE_DIRECTORY`:

1. If the user explicitly provided `SPECIFY_FEATURE_DIRECTORY` through arguments, environment, or configuration, use it as-is.
2. Otherwise create one under `specs/`:
   - Read `.specify/init-options.json` if present.
   - If `branch_numbering` is `"timestamp"`, use prefix `YYYYMMDD-HHMMSS`.
   - If `branch_numbering` is `"sequential"` or absent, use the next available 3-digit prefix after scanning existing directories in `specs/`.
   - Directory name is `<prefix>-<short-name>`, for example `003-user-auth`.

Create artifacts:

- Create `SPECIFY_FEATURE_DIRECTORY`.
- Copy `templates/spec-template.md` to `SPECIFY_FEATURE_DIRECTORY/spec.md`.
- Set `SPEC_FILE` to that `spec.md`.
- Write `.specify/feature.json` with the actual resolved feature directory path:

```json
{
  "feature_directory": "<resolved feature dir>"
}
```

Important constraints:

- Create only one feature per invocation.
- Generate exactly one user story in the spec.
- Do not generate P1/P2/P3 stories or multiple story sections.
- If the input describes multiple independent stories, choose the primary story only when obvious and record the rest as out of scope; otherwise ask for clarification or direct the user to `/speckit.breakdown` when starting from a technical or declarative design.
- If the request is too ambiguous to identify one independently testable story, ask one targeted question before creating files.
- The spec directory name and git branch name are independent.
- The spec directory and file are created by this workflow, not by hooks.
- Do not use legacy branch-numbering logic to determine the feature directory. `.specify/feature.json` is the downstream locator.

## Generate The Spec

Load `templates/spec-template.md` and preserve its section order and headings. For source-backed requests only, add `## Source Design Requirements` after `## Assumptions` when that section is present; otherwise place it immediately before `## Requirements`.

Fill the template with concrete details derived from the feature description:

1. Extract actors, actions, data, constraints, and user value.
2. Make informed guesses from context, industry standards, and common patterns.
3. Add `[NEEDS CLARIFICATION: specific question]` only when:
   - The choice significantly impacts scope, security/privacy, or user experience.
   - Multiple reasonable interpretations exist with different implications.
   - No reasonable default exists.
4. Use at most 3 clarification markers, prioritized as scope > security/privacy > user experience > technical details.
5. Fill one `## User Story - ...` section with:
   - `Summary`
   - `Goal`
   - `Independent Test`
   - `Acceptance Scenarios`
   - `Edge Cases`
6. If no independently testable story can be derived, stop with: `ERROR "Cannot determine a single independently testable story"`.
7. Generate testable functional requirements.
8. Document assumptions in `## Assumptions` only when assumptions are used; remove the section if no assumptions were made.
9. For source-backed requests, add `## Source Design Requirements` with:
   - Requirement ID.
   - Source citation such as heading, table name, or line number when available.
   - Requirement summary in testable, implementation-agnostic language.
   - Scope status: in scope or out of scope with rationale.
   - Mapped functional requirement ID after functional requirements are generated.
10. Ensure every in-scope `DESIGN-REQ-*` maps to at least one functional requirement.
11. Define measurable, technology-agnostic success criteria.
12. Include key entities only if the feature involves data.
13. Apply the intent guard:
   - For `runtime` intent, the spec must describe observable system behavior and validation outcomes, not merely edits to documentation.
   - For `docs` intent, the spec may describe documentation outcomes, but still keep requirements testable and acceptance-based.

Guidelines:

- Focus on what users need and why.
- Avoid how to implement it: no tech stack, APIs, code structure, frameworks, or databases.
- Write for business stakeholders, not developers.
- Remove optional sections when irrelevant instead of writing `N/A`.
- Do not embed checklists in the spec.
- Keep source requirement mappings traceable without introducing implementation details.

## Quality Checklist

After writing the initial spec, create `SPECIFY_FEATURE_DIRECTORY/checklists/requirements.md`:

```markdown
# Specification Quality Checklist: [FEATURE NAME]

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: [DATE]
**Feature**: [Link to spec.md]

## Content Quality

- [ ] No implementation details (languages, frameworks, APIs)
- [ ] Focused on user value and business needs
- [ ] Written for non-technical stakeholders
- [ ] All mandatory sections completed

## Requirement Completeness

- [ ] No [NEEDS CLARIFICATION] markers remain
- [ ] Exactly one user story is defined
- [ ] Requirements are testable and unambiguous
- [ ] Runtime intent describes system behavior rather than docs-only changes, unless docs-only was explicitly requested
- [ ] Success criteria are measurable
- [ ] Success criteria are technology-agnostic (no implementation details)
- [ ] All acceptance scenarios are defined
- [ ] Independent Test describes how the story can be validated end-to-end
- [ ] Acceptance scenarios are concrete enough to derive unit and integration tests
- [ ] No in-scope source design requirements are unmapped from functional requirements
- [ ] Edge cases are identified
- [ ] Scope is clearly bounded
- [ ] Dependencies and assumptions identified

## Feature Readiness

- [ ] All functional requirements have clear acceptance criteria
- [ ] The single user story covers the primary flow
- [ ] Feature meets measurable outcomes defined in Success Criteria
- [ ] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit.clarify` or `/speckit.plan`
```

Validate the spec against every checklist item. Mark passing items complete. For failures, quote the relevant spec section in the notes.

If checklist items fail for reasons other than clarification markers:

1. List failing items and specific issues.
2. Update the spec to fix them.
3. Re-run validation.
4. Repeat up to 3 iterations.
5. If still failing, document remaining issues in checklist notes and warn the user.

## Clarifications

If `[NEEDS CLARIFICATION]` markers remain:

1. Extract all markers.
2. If more than 3 exist, keep only the 3 most critical and make informed guesses for the rest.
3. Present all clarification questions together before waiting.
4. Use this format for each question:

```markdown
## Question [N]: [Topic]

**Context**: [Quote relevant spec section]

**What we need to know**: [Specific question from NEEDS CLARIFICATION marker]

**Suggested Answers**:

| Option | Answer | Implications |
|--------|--------|--------------|
| A      | [First suggested answer] | [What this means for the feature] |
| B      | [Second suggested answer] | [What this means for the feature] |
| C      | [Third suggested answer] | [What this means for the feature] |
| Custom | Provide your own answer | [Explain how to provide custom input] |

**Your choice**: _[Wait for user response]_
```

After the user responds, replace each marker with the selected answer and re-run validation.

## Reasonable Defaults

Do not ask for clarification when a safe, conventional default is available. Record the assumption instead.

Examples:

- Data retention: use domain-standard retention unless the request specifies legal or policy constraints.
- Error handling: require user-visible, recoverable failures where applicable.
- Performance: use normal product expectations unless the request names a threshold.
- Authentication: use the project's established auth pattern if one exists.
- Integration style: use the project's existing interface patterns rather than inventing a new one.

## Success Criteria Guidance

Success criteria must be:

- Measurable: include time, percentage, count, rate, or similar metrics.
- Technology-agnostic: no frameworks, languages, databases, or tools.
- User-focused: describe user or business outcomes rather than internals.
- Verifiable: testable without knowing implementation details.

Good examples:

- `Users can complete checkout in under 3 minutes`
- `System supports 10,000 concurrent users`
- `95% of searches return results in under 1 second`
- `Task completion rate improves by 40%`

Bad examples:

- `API response time is under 200ms`
- `Database can handle 1000 TPS`
- `React components render efficiently`
- `Redis cache hit rate above 80%`

## Outputs

- `SPECIFY_FEATURE_DIRECTORY/spec.md`
- `SPECIFY_FEATURE_DIRECTORY/checklists/requirements.md`
- `.specify/feature.json` pointing to the active feature directory
- Source design mapping in `spec.md` when the request is source-backed

## Report And Post-Spec Hooks

Report completion with:

- `SPECIFY_FEATURE_DIRECTORY`
- `SPEC_FILE`
- Checklist results summary
- Intent classification and source design mapping summary, when applicable
- Readiness for `/speckit.clarify` or `/speckit.plan`

After reporting, check `.specify/extensions.yml` for `hooks.after_specify` using the same parsing, filtering, and condition rules as pre-spec hooks. For each executable hook:

- Optional hook (`optional: true`): print:

```markdown
## Extension Hooks

**Optional Hook**: {extension}
Command: `/{command}`
Description: {description}

Prompt: {prompt}
To execute: `/{command}`
```

- Mandatory hook (`optional: false`): print:

```markdown
## Extension Hooks

**Automatic Hook**: {extension}
Executing: `/{command}`
EXECUTE_COMMAND: {command}
```

If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.
