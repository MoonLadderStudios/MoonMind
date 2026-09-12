---
name: document-author
description: Author a new docs-native canonical or working document by choosing the
  correct location, filename, viewpoint template, metadata header, stable claims,
  and embedded rationale without creating spec.md.
metadata:
  required-capabilities:
  - git
  required-skills: document-update
---

# Document Author

Author the requested bounded document or document set directly. Do not replace an
authorized multi-document output with an improvement plan solely because it is
substantial. Do not create `spec.md` or write under `specs/` for docs-native work.

## Inputs

Documentation intent, current checkout, and optional preferred area, source
references/issue keys, constraints, document class and verification commands.

## Authority and scope

Resolve role before writing: factual implementation reference, authorized
desired-state design, or temporary execution artifact. Source code establishes
implemented facts; the authorized request and owning contract establish intended
behavior. A proposed design may precede implementation: label it Proposed (or the
repository equivalent), never Implemented without evidence. Buggy or incomplete
code cannot downgrade canonical desired state. Preserve unrelated user changes.

Discover actual repository conventions using the `document-update` bundle's
[repository conventions](../document-update/references/repository-conventions.md).
Resolve dependencies from `MOONMIND_ACTIVE_SKILLS_DIR` first; outside MoonMind use
the installed sibling bundle. Missing MoonMind-specific guidance is not a blocker.

## Workflow

1. Resolve the role, authorized scope and existing owner for each requested topic.
   Search for overlap; extend an existing owner or link to it instead of creating
   a second contract. An unclear owner holds only that claim for escalation.
2. Choose the document class, location, filename and, where required, viewpoint
   template and metadata header. Load only the relevant class/viewpoint reference.
   Preserve requested traceability, stable claim IDs and ownership fields.
3. Write stable claims and embedded rationale near the decisions they explain.
   Separate intended behavior from verified implementation facts. Record unresolved
   choices explicitly; do not treat absence of code as evidence against a design.
4. Check links, ownership, metadata and claim evidence. Run available documentation
   checks and repository-required verification, including executable examples when
   changed. Complete the authorized document set. Use an improvement plan only
   when planning was requested or remaining work lacks scope/decision authority;
   retain a complete escalation handoff for those unresolved portions.

## Output

Report authored paths, roles/status, ownership reason, metadata, claim traceability,
rationale placement, validation and any individual held claims with resume conditions.
Confirm that no docs-native `spec.md` was created. Commit/publish only when requested.
