---
name: document-update
description: Maintain factual references, desired-state designs and temporary documentation
  according to their role and authority. Use for bounded documentation drift correction.
metadata:
  required-capabilities:
  - git
---

# Document Update

Maintain the requested document or bounded document set according to its role.
Correct factual references against current implementation; preserve intended
behavior in canonical desired-state designs. Code is evidence of implementation,
not permission to change a satisfiable contract to match a bug.

## Inputs

Target document path(s), topic or drift report; current checkout; optional source
contracts, scope limits, constraints, verification commands and artifact locations.
Directory-dispatched children also receive `document_path`, `source_directory`,
`constraints` and `edit_scope: target_only`. That mode permits edits only to the
exact target. Read shared owners as evidence, but record required owner/reference
edits as structured handoffs for coordinated maintenance, never write global files
from competing children. Publication intent belongs to the caller.

## Workflow

1. Resolve document role and authority before comparing claims: factual
   implementation reference, authorized desired-state design, or temporary
   execution artifact. In mixed documents classify affected claims individually.
   Follow [repository conventions](references/repository-conventions.md), loading
   only relevant local standards; absent MoonMind taxonomy is not a blocker.
2. Extract claims and inspect the source, tests, schemas, configuration and real
   invocation boundaries needed to establish facts. Preserve a drift ledger with
   claim, role, owner, evidence and status: `accurate`, `stale`, `missing`,
   `implementation_gap`, `ambiguous`, or `out_of_scope`.
3. Correct stale factual references. Preserve canonical intended behavior when code
   is incomplete or buggy and record the implementation gap. An authorized proposed
   design can be authored before implementation, with truthful Proposed/Accepted
   status. Do not silently invent an owner or mark uncertain behavior implemented.
4. Apply the smallest coherent authorized edits, including bounded multi-document
   work and required links. Preserve unique content, metadata, traceability,
   rationale, terminology and unrelated working-tree changes. A large scope alone
   does not require a plan-only detour. Temporary execution notes stay temporary.
5. Recheck changed claims against evidence; run documentation/link checks and
   relevant executable-example tests. If everything is accurate, return a verified
   no-op. If checks cannot run, record the attempted command and exact blocker.
6. For conflicting owners or a desired-state change lacking authority, retain the
   complete provider-neutral escalation handoff defined in the conventions
   reference. Missing Jira never changes desired state or discards the review.
   Continue independent authorized work. A plan is appropriate only for unresolved
   scope/decisions or a requested planning deliverable.

## Output

Updated paths or `no_update_required`, drift ledger including each deferred claim,
evidence and validation results, escalation artifact/verified tracker receipt when
applicable, and commit hash only when the caller requested a commit.
