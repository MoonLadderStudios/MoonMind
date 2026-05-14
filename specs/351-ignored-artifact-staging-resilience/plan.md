# Implementation Plan: Ignored Artifact Staging Resilience

## Summary

Split publish staging by Git status class. Tracked changes use `git add -u`, which handles tracked files under ignored parents. Untracked paths continue to use `git add -A` so normal new files are included while ignored untracked artifacts remain excluded by Git.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS - uses Git's status and staging semantics directly.
- IV Own Your Data: PASS - preserves artifact-backed outputs and target repository changes.
- IX Resilient by Default: PASS - prevents a post-agent finalization failure after useful work completed.
- XI Spec-Driven Development: PASS - feature artifacts define the narrow behavior change.
- XII Canonical Documentation: PASS - rollout details stay in this spec, not canonical docs.
- XIII Compatibility Policy: PASS - no compatibility alias or hidden semantic transform is introduced.

## Test Strategy

- Add a unit regression covering a tracked modified file under ignored `artifacts/`.
- Run the focused publish test module, then the repo unit runner if feasible.
