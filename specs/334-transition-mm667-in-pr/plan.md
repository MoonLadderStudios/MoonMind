# Implementation Plan: Transition Jira Issue MM-667 to "In Progress"

**Branch**: `change-jira-issue-mm-667-to-status-in-pr-a8186bb9` (spec dir: `334-transition-mm667-in-pr`) | **Date**: 2026-05-09 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/334-transition-mm667-in-pr/spec.md`

## Summary

The story moves a single Jira issue (`MM-667`) into workflow status `In Progress`, exactly once, through MoonMind's trusted Jira tool surface. The technical approach is to perform the transition during this agent run by calling the already-registered trusted tools (`jira.get_transitions`, `jira.transition_issue`, `jira.get_issue`), with the agent applying case-insensitive trimmed name matching against the literal target `In Progress`, executing the transition only when exactly one match exists, and re-reading MM-667 afterward to verify the final status. Test strategy is verification-first: existing unit tests already cover the trusted tool surface and required-field handling; the per-run independent test is to re-fetch MM-667 from Jira after the run and confirm it reads `In Progress` (or that the workflow recorded a no-op). Repo gap analysis: every supporting tool, request model, and credential-redaction layer already exists in `moonmind/integrations/jira/` and `moonmind/mcp/jira_tool_registry.py`; the per-issue orchestration is performed inline by the agent during this run and produces a structured run report — no new persistent Python orchestration is required for this single-issue, one-shot story.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
|---|---|---|---|---|
| FR-001 (literal `MM-667` target) | partial | `moonmind/integrations/jira/models.py:223` (`TransitionIssueRequest.issueKey`) accepts any key; agent must pass exactly `MM-667` | Run-time discipline: pass `issueKey="MM-667"` to every Jira tool call in this story; no inferred substitutions | Run-report assertion (independent test) |
| FR-002 (discover available transitions before mutation) | implemented_unverified | `moonmind/integrations/jira/tool.py:220-228` (`get_transitions`); `moonmind/mcp/jira_tool_registry.py:120-125` registers `jira.get_transitions` | None — call existing tool with `issueKey="MM-667"` and `expand_fields=true` so required-field metadata is present | Verification via run report capturing transition list |
| FR-003 (case-insensitive trimmed match for `In Progress`, unambiguous) | partial | Existing `select_done_transition` in `moonmind/workflows/temporal/post_merge_jira_completion.py:267-311` supports `transitionName` matching but is post-merge-specific (done-category bias). | Inline matching during the run: lowercase-trim each candidate transition's `to.name`, match against `"in progress"`, fail with named ambiguity error when match count != 1 | Verification via run report asserting unique match (or named error) |
| FR-004 (only via trusted Jira tools, no raw HTTP) | implemented_verified | `moonmind/mcp/jira_tool_registry.py:120-131` registers transition tools through `JiraToolService`; `tool.py` ensures `_ensure_enabled`/`_ensure_action_allowed`/`_ensure_project_allowed` gates; agent runtime has no Jira credentials | None | Final verification only |
| FR-005 (re-read and report verified final status) | implemented_unverified | `jira.get_issue` registered in `moonmind/mcp/jira_tool_registry.py:108-112` | Inline post-transition `jira.get_issue(MM-667)` call; surface the observed status in the run report; flag if final status != `In Progress` | Run-report assertion |
| FR-006 (already `In Progress` → no-op, no transition invoked) | partial | `select_done_transition` no-op handling exists for done category only (`post_merge_jira_completion.py:273-275`); not applicable to "In Progress" target | Inline check: if pre-transition status name (case-insensitive trimmed) equals `In Progress`, skip transition call and emit `noop_already_in_progress` outcome | Run-report assertion |
| FR-007 (named errors: no match / ambiguous / not found / missing required fields / tool unavailable / auth or permission / transient / final status mismatch) | partial | Existing tool errors for unknown issue and required-field surfacing exist (`_fetch_transitions` `expand_fields` enables `transitions.fields` metadata; `JiraToolError` raised on unavailable transition); no current ambiguity error specific to "In Progress"; redaction pipeline already covers auth/permission and transient surfaces | Inline error mapping during the run produces explicit named outcomes: `no_matching_transition`, `ambiguous_transition`, `issue_not_found`, `missing_required_fields`, `tool_unavailable`, `auth_or_permission`, `transient_failure`, and `final_status_mismatch` — no mutation in any error case | Run-report assertion for each error path that applies |
| FR-008 (no other field updates, no other issues) | implemented_verified | `tool.py:268-281` `transition_issue` only POSTs to `/issue/<key>/transitions` with the chosen `transitionId`; passing `fields={}` and `update={}` keeps the request scoped | None — never call `jira.edit_issue` in this story; only invoke transition + verification reads against `MM-667` | Final verification via Jira issue change history |
| FR-009 (no secrets in any output) | implemented_verified | `redact_sensitive_text` and `SecretRedactor` already used in `post_merge_jira_completion._scrub_exception` and across error paths; tool service excludes credentials from responses | None — reuse existing redaction; never paste env, raw exception bodies, or auth headers in run report | Final verification by scanning run output |
| FR-010 (final report: issue key, prior status, action, verified final status) | implemented_unverified | Pattern exists (`PostMergeJiraCompletionDecision` model in `post_merge_jira_completion.py`), but this story produces an inline run report rather than a persisted decision object | Emit a structured run report block at the end of the run with: `issueKey=MM-667`, `priorStatus`, `action ∈ {transitioned, noop_already_in_progress, stopped}`, `transition` (id + name when applicable), `verifiedFinalStatus`, and `errors` | Run-report shape assertion in quickstart |
| SCN-001 (transition path) | implemented_unverified | Same as FR-002, FR-003, FR-005 | Covered by FR-002+FR-003+FR-005 implementation; no extra work | Independent test in quickstart |
| SCN-002 (already-in-progress no-op) | partial | See FR-006 | Same as FR-006 | Independent test in quickstart |
| SCN-003 (no transition matches `In Progress`) | partial | See FR-007 | Inline named error: `no_matching_transition`, list available transition names | Independent test in quickstart |
| SCN-004 (multiple matching transitions) | partial | See FR-007 | Inline named error: `ambiguous_transition`, list candidate transitions | Independent test in quickstart |
| DESIGN-REQ-001 (only `MM-667`) | partial | See FR-001 + FR-008 | None beyond FR-001/FR-008 work | Run-report assertion |
| DESIGN-REQ-002 (target = `In Progress`) | partial | See FR-003 | None beyond FR-003 work | Run-report assertion |
| DESIGN-REQ-003 (status change only) | implemented_verified | See FR-008 | None | Final verification |
| SC-001 (post-run Jira shows `In Progress`) | implemented_unverified | Verification fetch wired to `jira.get_issue` | Run-report and operator-side Jira check | Independent test in quickstart |
| SC-002 (one of three outcomes; zero partial mutations) | implemented_unverified | Outcome enumeration enforced by inline error mapping (FR-007) | None beyond FR-006/FR-007 work | Run-report assertion |
| SC-003 (zero secret exposure) | implemented_verified | Existing redactor pipeline | None | Run-output scan |
| SC-004 (no other Jira issue modified) | implemented_verified | See FR-008 | None | Jira change-history check |
| SC-005 (single observed cycle, no manual retry) | implemented_unverified | Trusted tool retries are bounded; ambiguous/missing-field cases stop fast with named error | None — fail-fast behavior is the existing default | Run-report assertion |

## Technical Context

**Language/Version**: Python 3.12 (existing MoonMind runtime); no new code modules required.
**Primary Dependencies**: Existing trusted Jira tool registry (`moonmind/mcp/jira_tool_registry.py`), `JiraToolService` (`moonmind/integrations/jira/tool.py`), `redact_sensitive_text` / `SecretRedactor` redaction helpers.
**Storage**: N/A — this is a one-shot tool-driven workflow. The only persisted artifact is the run's structured outcome report (artifact-backed run output); no new persistent tables.
**Unit Testing**: pytest (`./tools/test_unit.sh`). No new unit tests are added in this plan; existing trusted-tool unit tests cover transition discovery, transition execution, required-field handling, and credential redaction.
**Integration Testing**: pytest with the existing Jira provider verification tests (`@pytest.mark.provider_verification`, `@pytest.mark.requires_credentials`, `@pytest.mark.jules`) when validating against a live Jira tenant. These are not in CI. The story's independent integration test is the per-run verification fetch defined in `quickstart.md`.
**Target Platform**: MoonMind agent runtime (managed agent runtime container under `/work/agent_jobs/<job_id>/`).
**Project Type**: Single-story Moon Spec (operator workflow); no new module boundaries.
**Performance Goals**: Single observed cycle (SC-005); bounded by Jira REST round-trip times — typically < 5s for fetch + transition + verify.
**Constraints**: No raw Jira HTTP from the agent; no new credentials in the runtime; no mutation of any issue other than `MM-667`; no field updates beyond the workflow status; zero secret leakage.
**Scale/Scope**: Exactly one Jira issue (`MM-667`), one transition, one verification fetch, one structured run report.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Verdict | Notes |
|---|---|---|
| I. Orchestrate, Don't Recreate | PASS | Uses Jira-native transitions through MoonMind's adapter; no rebuilt agent cognition. |
| II. One-Click Agent Deployment | PASS | No new prerequisites or services; the trusted Jira binding is already configured. |
| III. Avoid Vendor Lock-In | PASS | Trusted Jira behavior remains behind the existing `JiraToolService` adapter. |
| IV. Own Your Data | PASS | No new external SaaS; outputs persist as MoonMind run artifacts. |
| V. Skills Are First-Class | PASS | Reuses existing trusted Jira tool surface; no new skill required for this one-shot run. |
| VI. Bittersweet Lesson | PASS | No new scaffolding; thin adapter usage. |
| VII. Powerful Runtime Configurability | PASS | Project allowlist and tool toggles in existing `JiraToolService` settings remain authoritative. |
| VIII. Modular & Extensible | PASS | No cross-cutting changes. |
| IX. Resilient by Default | PASS | Fail-fast on ambiguity / missing fields / not-found, with named errors and zero partial mutation. Tool-call failures (transient 5xx, rate limits) surface through existing `JiraToolError` without ad-hoc retries; the run reports the failure rather than claiming success. |
| X. Continuous Improvement | PASS | Run-report outcome enumeration (FR-010, SC-002) feeds Mission Control. |
| XI. Spec-Driven Development | PASS | This `plan.md` accompanies `spec.md`; subsequent `tasks.md` will consume `## Requirement Status`. |
| XII. Canonical Docs vs. Feature Artifacts | PASS | Plan/research/contracts/quickstart all live under `specs/334-transition-mm667-in-pr/`. |
| XIII. Pre-Release Velocity (Delete, Don't Deprecate) | PASS | No compatibility shims introduced; no legacy aliases retained. |

No violations to record under Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/334-transition-mm667-in-pr/
├── spec.md              # Story (single user story)
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output (tool I/O entities)
├── quickstart.md        # Phase 1 output (independent-test commands)
├── contracts/
│   └── transition-mm667.md  # Tool-call contract for the run
└── checklists/
    └── requirements.md  # Pre-existing requirements checklist
```

### Source Code (repository root)

This story does not add new Python source files. It binds to existing trusted-tool surfaces:

```text
moonmind/
├── integrations/
│   └── jira/
│       ├── tool.py            # JiraToolService.get_transitions / transition_issue (existing)
│       └── models.py          # GetTransitionsRequest / TransitionIssueRequest (existing)
└── mcp/
    └── jira_tool_registry.py  # Registers jira.get_transitions / jira.transition_issue / jira.get_issue (existing)

tests/
└── unit/
    ├── integrations/test_jira_tool_service.py        # Existing unit coverage of transition tools
    └── workflows/temporal/test_post_merge_jira_completion.py  # Existing selection-helper coverage (reference)
```

**Structure Decision**: Reuse — no new orchestration module is created for this single-issue, one-shot run. The agent invokes the existing trusted Jira tools inline during the run and emits a structured run report. If, in a later story, the same workflow needs to be repeated for arbitrary issues and target statuses, the inline matching/no-op logic should be lifted into a generalized `transition_issue_to_named_status` helper alongside `select_done_transition` in `moonmind/workflows/temporal/`. That generalization is **out of scope** for MM-667.

## Complexity Tracking

> No constitution violations. Section intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _none_ | _n/a_ | _n/a_ |
