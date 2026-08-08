---
name: omnigent-oauth-lifecycle-workspace-authority-env-failures
description: test_oauth_profile_lifecycle failures with WORKSPACE_AUTHORITY_MISMATCH are a hermetic-env quirk, not a regression
metadata:
  type: reference
---

`tests/unit/omnigent/test_oauth_profile_lifecycle.py` (the coordinator failure
matrix / cleanup / provider-release tests) fails in the hermetic agent workspace
with `WorkspaceLocatorResolutionError: WORKSPACE_AUTHORITY_MISMATCH: workspace is
outside the daemon mapping authority` (~23 fails in that file, ~56 across the
omnigent unit dir when the reliability-journey imports pull it in).

**Why:** the sandbox workspace-locator resolution expects a daemon workspace
mapping authority that is not present in this hermetic env; it is unrelated to
source changes. Verified by `git stash --include-untracked` → the same failures
reproduce on the clean baseline.

**How to apply:** do not treat these as a regression caused by your change, and
do not try to "fix" them from application code. Scope test verification to the
suites your change actually touches. Related: [[pre-existing-test-executions-failures]],
[[frontend-vitest-colon-path-workaround]].
