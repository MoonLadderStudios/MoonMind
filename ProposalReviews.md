# Proposal Reviews

Reviewed on 2026-02-25 against current `status=open` proposals (15 total).
Grade scale: `A+` (highest usefulness) to `D` (low usefulness).

1. `12877b9a-d58e-430b-b5e2-0f0537bd9457` - [run_quality] Add regression checks for /tasks/create submit UX flow (tags: artifact_gap)
Grade: `B+`
Summary: Useful UI-regression coverage for a user-facing flow with moderate risk; good preventive value, but narrower system impact than core runtime reliability issues.

2. `58454532-d377-4b52-8c46-e0bbca85fd7c` - [run_quality] Stabilize Codex shell utility assumptions for search commands (tags: retry)
Grade: `B`
Summary: Meaningful reliability improvement by reducing avoidable command failures; valuable but overlaps heavily with multiple other `rg`/tooling proposals.

3. `ac6d502a-0d02-4ac2-964d-2c8698b14027` - [run_quality] Deduplicate repeated assistant completion blocks in step logs (tags: duplicate_output)
Grade: `B`
Summary: Helps artifact clarity and operator trust, but it is partially subsumed by broader duplicate-output proposals already in the queue.

4. `24a6319d-34ee-4e7e-a9ce-49bf0f027c0a` - [run_quality] Add end-to-end replay dedupe coverage for mirrored step artifacts (tags: artifact_gap+duplicate_output)
Grade: `B+`
Summary: Strong regression-test value for preventing recurrence of log duplication across both artifact sinks; good hardening once core dedupe behavior is fixed.

5. `7bc705c7-a0cb-4437-b958-24cb3fe677e9` - [run_quality] Align Codex runtime tooling with agent guidance (ripgrep preflight) (tags: conflicting_instructions)
Grade: `A-`
Summary: High leverage because it resolves instruction/runtime mismatch that repeatedly causes first-command failures; likely to improve run success quality across many tasks.

6. `294b42b3-b0ff-4889-a2fa-6b5a2d7c710c` - [run_quality] Add proposal rendering tests for mobile card vs desktop table (tags: artifact_gap)
Grade: `B`
Summary: Solid UI quality coverage for proposal pages and responsive behavior; worthwhile, but lower systemic impact than execution/publish correctness work.

7. `d612e868-e957-407a-b002-35cdd27be099` - [run_quality] Add proposal layout regression coverage for mobile card view (tags: artifact_gap)
Grade: `C+`
Summary: Reasonable test addition, but near-duplicate scope to proposal `294b42b3-...`; incremental value is limited unless merged with that task.

8. `7fb00340-a4d5-4ac1-92d0-63ca16fba97d` - [run_quality] Add search-tool fallback for missing `rg` in execution shell steps (tags: missing_ref+retry)
Grade: `C`
Summary: Useful in isolation, but largely redundant with broader/higher-quality tooling preflight proposals already open.

9. `d0c6bec7-6b8f-456c-8b1d-f2d5194d5264` - [run_quality] Fail fast when batch-pr-resolver queues zero tasks for fork-only PR sets (tags: conflicting_instructions)
Grade: `A`
Summary: High usefulness because it fixes false-positive job success semantics and improves workflow correctness for objective-level automation.

10. `fdaf9583-3178-4b69-b84e-3456944844b1` - [run_quality] Add compressed full-fidelity step logs when truncation is applied (tags: artifact_gap)
Grade: `B`
Summary: Good observability improvement that preserves forensic depth without unbounded plaintext size; valuable but secondary to correctness gates and duplication fixes.

11. `c3010191-7225-40b5-8fab-ebbf76dbfc4d` - [run_quality] Prevent avoidable tool-not-found retries in Codex runs (tags: artifact_gap+retry)
Grade: `C+`
Summary: Addresses a real issue, but substantially overlaps with stronger ripgrep/runtime preflight proposals, reducing standalone incremental benefit.

12. `f9308a54-9c79-4995-9609-517480d76eec` - [run_quality] Harden step-log delta capture across resume and log truncation boundaries (tags: artifact_gap+duplicate_output)
Grade: `A-`
Summary: High-value reliability hardening for resumed runs and boundary conditions, where regressions are costly and hard to diagnose.

13. `979517e1-6f15-455a-8b70-ee19b9813632` - [run_quality] Prevent step-log self-amplification from self-referential output capture (tags: artifact_gap+duplicate_output+loop_detected)
Grade: `A`
Summary: Very useful for controlling artifact blow-up and runaway logging behavior; directly protects system stability and storage/runtime costs.

14. `7505f6a9-c2d2-481f-a651-d494514dc967` - [run_quality] Deduplicate repeated step output in Codex run logs (tags: duplicate_output)
Grade: `A-`
Summary: Core run-quality fix with broad impact on artifact signal quality; strong candidate, though overlaps partially with related dedupe proposals.

15. `0db826e4-2cba-4548-aa3d-65912616c2e5` - [run_quality] Gate publish on missing verification evidence for code-changing runs (tags: artifact_gap)
Grade: `A+`
Summary: Highest usefulness because it directly guards against shipping unverified code changes and enforces quality/safety at the publish boundary.
