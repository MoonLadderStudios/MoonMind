# Research and Repo Gap Analysis: Transition MM-667 to "In Progress"

This research resolves planning unknowns and classifies each in-scope requirement against the current repo state. No `NEEDS CLARIFICATION` items remain after this pass.

## Test Tooling

Decision: pytest for unit verification; for live Jira validation, use existing `provider_verification` markers (`@pytest.mark.provider_verification`, `@pytest.mark.requires_credentials`, `@pytest.mark.jules`) — out of CI per repo policy. The independent end-to-end check is the per-run verification fetch defined in `quickstart.md`.
Evidence: `CLAUDE.md` test taxonomy section; `tools/test_unit.sh`; `tools/test_jules_provider.sh`.
Rationale: Matches repo norms; avoids introducing a new test surface for a one-shot story.
Alternatives considered: New CI integration test exercising live Jira — rejected; would require Jira credentials in CI, violates one-click deployment and secret hygiene principles.
Test implications: No new unit tests; quickstart-driven manual verification; existing unit coverage of trusted Jira tools is the regression anchor.

## Trusted Jira Tool Surface

Decision: Use the already-registered tools `jira.get_transitions`, `jira.transition_issue`, and `jira.get_issue` exclusively. No new tool, no new adapter, and no raw HTTP from the agent runtime.
Evidence: `moonmind/mcp/jira_tool_registry.py:108-131`; `moonmind/integrations/jira/tool.py:220-286`; `moonmind/integrations/jira/models.py:223-237`. Existing unit tests at `tests/unit/integrations/test_jira_tool_service.py:196-272` confirm preflight transition lookup, expanded transition-field metadata, and credential exclusion from responses.
Rationale: Constitution principle I (Orchestrate, Don't Recreate), III (Avoid Vendor Lock-In), and operational guardrails (FR-004, FR-009) are satisfied by reusing the trusted surface.
Alternatives considered: Inline `httpx` from the agent — rejected; violates FR-004 and constitution principle III, and would require leaking Jira credentials into the agent runtime.
Test implications: None new; existing tool-service unit tests are the regression anchor.

## Transition Selection Logic for "In Progress"

Decision: Perform case-insensitive trimmed name matching of `transition.to.name == "In Progress"` inline during the agent run. Treat zero matches as `no_matching_transition`, more than one match as `ambiguous_transition`, and exactly one match as the `selected` transition. Detect required fields with `expand_fields=true` on the discovery call and raise `missing_required_fields` (no value guessing). Do not reuse `select_done_transition` from `moonmind/workflows/temporal/post_merge_jira_completion.py:267-311` — that helper biases toward done-category semantics and starts with a done-category no-op short-circuit, which would falsely no-op when MM-667 happens to already be in a done-category status (irrelevant to "In Progress" target semantics).
Evidence: `post_merge_jira_completion.py:273-275` (done-category no-op); `post_merge_jira_completion.py:289-300` (`transitionName` matching is case-insensitive, but the surrounding `_is_done_category` short-circuit makes the helper unsuitable as-is); `post_merge_jira_completion.py:336-349` (`_missing_required_fields`).
Rationale: Spec demands target-status-name semantics, not done-category semantics. A bespoke inline match is small, explicit, and avoids modifying a helper that already has callers tuned for "Done".
Alternatives considered:
- Generalize `select_done_transition` to accept a target-status-name parameter and decouple the no-op check from done-category — rejected as **out of scope** for a one-shot run targeting MM-667. Defer to a follow-up story when this pattern needs to be repeated for arbitrary issues and target statuses.
- Use `select_done_transition(config.transitionName="In Progress")` and ignore the done-category no-op — rejected; the no-op path returns `noop_already_done` even when the issue is in any done-category status, which is the wrong semantics for "In Progress".
Test implications: Per-run quickstart assertions cover the four named outcomes (transitioned / no-op / no_matching_transition / ambiguous_transition / missing_required_fields). No new unit test required for this story.

## No-Op Detection ("Already In Progress")

Decision: Compare the pre-transition issue's status name (case-insensitive trimmed) against `"in progress"`. When equal, skip both the transition discovery match step and the `jira.transition_issue` call; emit a `noop_already_in_progress` outcome carrying the current status; do not invoke any mutation.
Evidence: Spec FR-006, SCN-002. `jira.get_issue` already returns the issue including its current status (used today for read flows in `JiraToolService`).
Rationale: Avoids a redundant transition call and zero-mutation guarantee even when the issue is already in `In Progress`.
Alternatives considered: Always discover and execute, relying on Jira's no-op behavior — rejected; not all Jira workflows expose a self-loop transition, so an unconditional execution attempt could produce a misleading `no_matching_transition` error or a side-effect transition.
Test implications: Quickstart assertion covers the no-op path.

## Error Surface and Named Outcomes

Decision: The run emits exactly one of the following outcomes:
1. `transitioned` — discovery + match + execute + verify all succeed; final status is `In Progress`.
2. `noop_already_in_progress` — pre-fetch shows `In Progress`; no transition executed.
3. `stopped:no_matching_transition` — discovery returns zero matches; available transition names listed.
4. `stopped:ambiguous_transition` — discovery returns more than one match; candidate transitions listed (id, name, target status name).
5. `stopped:issue_not_found` — `jira.get_issue` for `MM-667` raises a not-found error.
6. `stopped:missing_required_fields` — selected transition declares required fields not supplied; named field IDs listed; no mutation.
7. `stopped:auth_or_permission` — auth/permission failure surfaced sanitized (no tokens, no headers).
8. `stopped:validation_failure` — Jira validation/policy failure (non-auth 4xx such as 400/422) surfaced through existing `JiraToolError`; no ad-hoc retry; operator must inspect the request/transition configuration.
9. `stopped:tool_unavailable` — trusted Jira tool surface is not registered or disabled at runtime.
10. `stopped:transient_failure` — Jira transient (rate-limited / 5xx) surfaced through existing `JiraToolError`; no ad-hoc retry; no claim of success.
11. `stopped:final_status_mismatch` — post-transition fetch shows a status other than `In Progress` (Jira advanced through an intermediate state, etc.); the actual final status is reported.
Evidence: Spec FR-007, FR-009, FR-010, edge-cases section. `JiraToolError` raised in `tool.py:262-267` for unavailable transitions; existing redaction in `post_merge_jira_completion._scrub_exception` (`post_merge_jira_completion.py:264-265`).
Rationale: Enumerated outcomes satisfy SC-002 (one of the named outcomes; zero partial mutations).
Alternatives considered: Free-form error messages — rejected; SC-002 demands enumerated outcomes, and Mission Control benefits from stable outcome IDs.
Test implications: Quickstart documents each outcome and how to verify it from the run report.

## Credential Hygiene

Decision: All errors and outputs flow through the existing `redact_sensitive_text` / `SecretRedactor` pipeline before being included in the run report. The agent never includes the raw `Authorization` header, raw exception bodies, env dumps, or full `docker compose` outputs in any user-visible artifact. Trusted tool responses already exclude credentials.
Evidence: `moonmind/workflows/temporal/post_merge_jira_completion.py:264-265`; `moonmind/integrations/jira/tool.py` raises `JiraToolError` with sanitized messages; `tests/unit/integrations/test_jira_tool_service.py` asserts no-credential responses; `tests/unit/workflows/temporal/test_post_merge_jira_completion.py:154-214` covers credential scrubbing on read failures.
Rationale: Constitution Non-Negotiable Product & Operational Constraints (Security/secret hygiene); FR-009; SC-003.
Alternatives considered: None — secret leakage is a hard non-negotiable.
Test implications: None new; existing tests are the regression anchor. Quickstart includes a final scan of the run report for known secret patterns (`ghp_`, `token=`, `password=`, etc.) before publishing.

## Per-Requirement Coverage Classification

| ID | Classification | Notes |
|---|---|---|
| FR-001 | partial | Run-time discipline: pass exactly `MM-667` to every tool call. |
| FR-002 | implemented_unverified | `jira.get_transitions` exists; verification via run report. |
| FR-003 | partial | Inline matching during the run; existing helper is post-merge-specific. |
| FR-004 | implemented_verified | Trusted tool registry enforces single-channel access. |
| FR-005 | implemented_unverified | `jira.get_issue` exists; verification via post-transition fetch. |
| FR-006 | partial | Inline pre-fetch comparison; no helper currently provides "In Progress" no-op. |
| FR-007 | partial | Inline named error mapping over existing tool errors. |
| FR-008 | implemented_verified | `transition_issue` only mutates workflow status; agent never calls `jira.edit_issue` in this story. |
| FR-009 | implemented_verified | Existing redaction pipeline. |
| FR-010 | implemented_unverified | Run report shape defined in `data-model.md` and verified per quickstart. |
| SCN-001 | implemented_unverified | Composition of FR-002 + FR-003 + FR-005 work. |
| SCN-002 | partial | Inline pre-fetch no-op (FR-006). |
| SCN-003 | partial | Named error path for zero matches. |
| SCN-004 | partial | Named error path for >1 match. |
| DESIGN-REQ-001 | partial | Same coverage as FR-001/FR-008. |
| DESIGN-REQ-002 | partial | Same coverage as FR-003. |
| DESIGN-REQ-003 | implemented_verified | Same coverage as FR-008. |
| SC-001 | implemented_unverified | Operator-side Jira UI verification + run report. |
| SC-002 | implemented_unverified | Outcome enumeration above. |
| SC-003 | implemented_verified | Existing redaction. |
| SC-004 | implemented_verified | Same coverage as FR-008. |
| SC-005 | implemented_unverified | Fail-fast paths preserve single-cycle behavior. |

## Open Items

None. All in-scope requirements resolve to either `implemented_verified`, `implemented_unverified`, or `partial`. No `NEEDS CLARIFICATION` blockers remain.
