---
name: pre-existing-test-executions-failures
description: tests/unit/api/routers/test_executions.py describe_execution tests fail with a pre-existing IllegalStateChangeError unrelated to any change
metadata:
  type: reference
---

`tests/unit/api/routers/test_executions.py::test_describe_execution_*` tests fail
with `sqlalchemy.exc.IllegalStateChangeError: Method 'close()' can't be called here`
in this managed-agent workspace. Confirmed reproducing on `main` (base commit
a5a387ba2) in a clean `git worktree`, and the failure count is non-deterministic
(varies 14–19) — it is an async-session/event-loop fixture/environment artifact,
NOT caused by feature branches.

**Why:** During verification it looks alarming when a focused run bundles these
tests, but they are unrelated to most changes (they exercise the untouched
`describe_execution` recovery-evidence/feature-flag paths).

**How to apply:** Before treating a `test_executions.py` describe_execution
failure as a regression, reproduce it on `main` in a worktree. If it reproduces
there too, it is pre-existing and out of scope. Also avoid running two pytest
processes over the same test files concurrently — shared sqlite/async resources
amplify these errors.
