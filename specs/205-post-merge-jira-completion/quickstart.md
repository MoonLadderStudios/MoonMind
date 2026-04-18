# Quickstart: Post-Merge Jira Completion

## Purpose

Validate MM-403 with test-first evidence before implementation and final verification.

## Prerequisites

- Python 3.12 environment from the repository.
- No live Jira credentials are required for required unit/workflow coverage.
- Docker is optional for hermetic integration checks; managed agent containers may not expose a Docker socket.

## Test-First Workflow

1. Confirm active feature:

```bash
sed -n '1,20p' .specify/feature.json
```

Expected feature directory:

```text
specs/205-post-merge-jira-completion
```

2. Add failing unit tests for post-merge Jira models and selection helpers:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/test_merge_gate_models.py \
  tests/unit/workflows/temporal/test_post_merge_jira_completion.py \
  tests/unit/integrations/test_jira_tool_service.py
```

Expected before implementation: new MM-403 assertions fail for missing `postMergeJira` config, issue resolution, transition selection, or required-field behavior.

3. Add failing workflow-boundary tests for merge automation completion:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py
```

Required scenarios:

- `merged` runs required Jira completion before terminal success.
- `already_merged` runs required Jira completion before terminal success.
- already-done Jira issue completes as no-op.
- missing or ambiguous issue keys block/ fail without Jira mutation.
- zero or multiple done transitions block/ fail without Jira mutation.
- retry/replay or duplicate completion evaluation does not emit duplicate transitions.

4. Implement the feature until the targeted tests pass.

5. Run targeted regression coverage:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/test_merge_gate_models.py \
  tests/unit/workflows/temporal/test_post_merge_jira_completion.py \
  tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py \
  tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py \
  tests/unit/integrations/test_jira_tool_service.py
```

6. Run full unit verification before finalizing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

7. Run hermetic integration verification if Docker is available:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed runtime, record the blocker and rely on unit/workflow-boundary evidence plus final `/moonspec-verify`.

Implementation verification note (2026-04-18): `./tools/test_integration.sh` could not run in the managed agent container because `/var/run/docker.sock` was unavailable (`failed to connect to the docker API`). Required verification used full unit coverage plus workflow-boundary tests.

## End-to-End Story Verification

Use stubbed trusted Jira responses to exercise one Jira-backed merge automation run:

1. Parent run publishes a PR and starts `MoonMind.MergeAutomation`.
2. Merge readiness gate opens.
3. Resolver child returns `status=success` and `mergeAutomationDisposition=merged`.
4. Post-merge completion validates `MM-403`, selects one done transition, applies it, and records completion evidence.
5. Merge automation returns terminal `merged` only after the Jira completion decision is `succeeded` or `noop_already_done`.

Failure verification:

- Missing issue key produces blocked/failed result with no Jira transition.
- Multiple valid candidate keys produce blocked/failed result with candidate evidence.
- Multiple done transitions without explicit selection produce blocked/failed result.
- Required transition fields without defaults produce blocked/failed result.

Traceability verification:

```bash
rg -n "MM-403|postMergeJira|Post-Merge Jira Completion" \
  specs/205-post-merge-jira-completion \
  docs/tmp/jira-orchestration-inputs/MM-403-moonspec-orchestration-input.md
```
