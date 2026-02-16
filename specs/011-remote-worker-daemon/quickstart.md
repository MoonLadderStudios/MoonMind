# Quickstart: Remote Worker Daemon (015-Aligned)

## Goal

Run `moonmind-codex-worker` with:

- Codex + Speckit preflight checks passing
- Google embedding readiness validated when applicable
- support for `codex_exec` and `codex_skill` queue claims

## Prerequisites

- MoonMind API and queue endpoints are running.
- `codex`, `speckit`, and optionally `gh` CLIs are installed on worker host.
- Codex authentication is complete (`codex login status`).

## 1) Configure environment

```bash
export MOONMIND_URL="http://localhost:5000"
export MOONMIND_WORKER_ID="executor-01"
export MOONMIND_WORKER_TOKEN="<token-if-required>"
export MOONMIND_POLL_INTERVAL_MS="1500"
export MOONMIND_LEASE_SECONDS="120"
export MOONMIND_WORKDIR="/tmp/moonmind-worker"
export MOONMIND_CODEX_MODEL="gpt-5-codex"
export MOONMIND_CODEX_EFFORT="medium"

# Skills policy
export MOONMIND_DEFAULT_SKILL="speckit"
export MOONMIND_ALLOWED_SKILLS="speckit,custom-skill"

# Embedding fast path
export DEFAULT_EMBEDDING_PROVIDER="google"
export GOOGLE_EMBEDDING_MODEL="gemini-embedding-001"
export GOOGLE_API_KEY="<google-api-key>"
```

## 2) Verify preflight prerequisites manually

```bash
codex login status
speckit --version
```

## 3) Start daemon

```bash
poetry run moonmind-codex-worker
```

Or run once for a single claim cycle:

```bash
poetry run moonmind-codex-worker --once
```

## 4) Enqueue `codex_exec` job

```bash
curl -X POST "$MOONMIND_URL/api/queue/jobs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  -d '{
    "type": "codex_exec",
    "priority": 10,
    "payload": {
      "repository": "MoonLadderStudios/MoonMind",
      "ref": "main",
      "workdirMode": "fresh_clone",
      "instruction": "Run tests and summarize failures",
      "codex": {
        "model": "gpt-5-codex",
        "effort": "high"
      },
      "publish": {"mode": "none", "baseBranch": "main"}
    }
  }'
```

## 5) Enqueue `codex_skill` job

```bash
curl -X POST "$MOONMIND_URL/api/queue/jobs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  -d '{
    "type": "codex_skill",
    "priority": 5,
    "payload": {
      "skillId": "speckit",
      "codex": {
        "model": "gpt-5-codex",
        "effort": "medium"
      },
      "inputs": {
        "repo": "MoonLadderStudios/MoonMind",
        "instruction": "Generate a focused implementation patch for the requested task"
      }
    }
  }'
```

Per-task override precedence for both payloads is:

1. `payload.codex.model` / `payload.codex.effort`
2. Worker defaults
3. Codex CLI defaults

## 6) Verify progress and events

```bash
curl -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  "$MOONMIND_URL/api/queue/jobs?limit=5"

curl -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  "$MOONMIND_URL/api/queue/jobs/<JOB_ID>/events"
```

Event payloads include execution metadata:

- `selectedSkill`
- `executionPath`
- `usedSkills`
- `usedFallback`
- `shadowModeRequested`

## 7) Validate artifacts and tests

```bash
curl -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  "$MOONMIND_URL/api/queue/jobs/<JOB_ID>/artifacts"

./tools/test_unit.sh
```
