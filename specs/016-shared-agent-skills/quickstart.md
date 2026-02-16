# Quickstart: Validate Shared Skills Directory for Codex + Gemini

## Prerequisites

- MoonMind repository checked out with this feature artifacts.
- Docker Compose runtime available for API and workers.
- Access to configured skill registry sources (git and/or object bundles).

## 1) Configure runtime knobs

Set environment values for resolver/materializer integration:

- `SPEC_WORKFLOW_USE_SKILLS=true`
- `SPEC_WORKFLOW_ALLOWED_SKILLS=<comma-separated skill names>`
- `SPEC_SKILLS_CACHE_ROOT=var/skill_cache`
- `SPEC_SKILLS_WORKSPACE_ROOT=runs`
- `SPEC_SKILLS_REGISTRY_SOURCE=<registry uri or profile>`
- `SPEC_SKILLS_VERIFY_SIGNATURES=<true|false>`

## 2) Start required services

```bash
docker compose up -d rabbitmq api celery_worker orchestrator
```

## 3) Trigger a run with explicit skill selection

Submit a workflow run with job overrides (example):

- `skill_overrides=["speckit:1.2.0","docs-lint:0.4.1"]`

Expected behavior:

- Resolver chooses effective run skill set.
- Materializer verifies artifacts and builds `skills_active`.
- `.agents/skills` and `.gemini/skills` symlink to the same active directory.

## 4) Verify workspace invariants

Inspect run workspace:

```bash
RUN_ID=<run_id>
ls -la /work/runs/${RUN_ID}
readlink -f /work/runs/${RUN_ID}/.agents/skills
readlink -f /work/runs/${RUN_ID}/.gemini/skills
find /work/runs/${RUN_ID}/skills_active -maxdepth 1 -type l -print
```

Expected results:

- Both readlink commands resolve to `/work/runs/<run_id>/skills_active`.
- Each entry in `skills_active` points into `/var/lib/moonmind/skill_cache/<sha>/...`.

## 5) Verify failure behavior

Run with one intentionally tampered or unknown version:

- Expect materialization failure before CLI execution.
- Expect structured error event containing mismatch or registry-miss details.

## 6) Run unit validation gate

```bash
./tools/test_unit.sh
```
