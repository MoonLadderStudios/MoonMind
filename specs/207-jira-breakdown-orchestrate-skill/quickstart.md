# Quickstart: Jira Breakdown and Orchestrate Skill

## Purpose

Validate MM-404 with test-first evidence before implementation and final verification.

## Prerequisites

- Python 3.12 environment from the repository.
- No live Jira credentials are required for required unit coverage.
- Docker is optional for hermetic integration checks; managed agent containers may not expose a Docker socket.

## Test-First Workflow

1. Confirm active feature:

```bash
sed -n '1,40p' .specify/feature.json
```

Expected feature directory:

```text
specs/207-jira-breakdown-orchestrate-skill
```

2. Add failing unit tests for the new seeded composite preset:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/api/test_task_step_templates_service.py
```

Required assertions:

- the global composite preset is seeded and discoverable,
- it runs normal Jira Breakdown before downstream task creation,
- it references trusted Jira surfaces,
- it includes downstream Jira Orchestrate task creation instructions,
- it preserves MM-404-style traceability requirements.

3. Add failing unit tests for downstream task creation from ordered Jira issue mappings:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/test_story_output_tools.py \
  tests/unit/workflows/temporal/test_temporal_service.py
```

Required scenarios:

- three valid issue mappings create three downstream Jira Orchestrate tasks,
- task 2 depends on task 1 and task 3 depends on task 2,
- one valid issue mapping creates one task and zero dependency edges,
- zero mappings returns a no-downstream-task outcome,
- missing issue keys are reported per story,
- partial task creation failure reports successes and failures,
- idempotency keys prevent duplicate downstream tasks on retry.

4. Implement the seeded preset and deterministic task creation helper until targeted tests pass.

5. Add or update startup seeding integration coverage:

```bash
./tools/test_integration.sh
```

Required startup seeding coverage lives in `tests/integration/test_startup_task_template_seeding.py`, which is marked `integration_ci` and is exercised by the integration runner.

6. Run targeted regression coverage:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/api/test_task_step_templates_service.py \
  tests/unit/workflows/temporal/test_story_output_tools.py \
  tests/unit/workflows/temporal/test_temporal_service.py
```

7. Run full unit verification before finalizing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

8. Run hermetic integration verification if Docker is available:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed runtime, record the blocker and rely on unit plus startup-seeding evidence.

## End-to-End Story Verification

Use stubbed trusted Jira and execution-creation responses to exercise one composite run:

1. The composite surface receives a broad Jira/design input.
2. Normal Jira Breakdown produces three ordered stories.
3. Trusted Jira story creation returns three created or reused Jira issue keys.
4. Downstream task creation creates three Jira Orchestrate tasks.
5. The second created task has `dependsOn` containing the first workflow ID.
6. The third created task has `dependsOn` containing the second workflow ID.
7. The orchestration result reports three created tasks, two dependency edges, and MM-404 traceability.

Failure verification:

- zero stories returns `no_downstream_tasks`,
- a missing Jira issue key skips or fails that story without inventing data,
- downstream task creation failure reports partial results,
- dependency validation failure does not claim a complete chain.

Traceability verification:

```bash
rg -n "MM-404|Jira Breakdown and Orchestrate" \
  specs/207-jira-breakdown-orchestrate-skill \
  docs/tmp/jira-orchestration-inputs/MM-404-moonspec-orchestration-input.md
```
