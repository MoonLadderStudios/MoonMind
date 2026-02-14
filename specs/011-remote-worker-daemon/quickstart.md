# Quickstart: Agent Queue Remote Worker Daemon (Milestone 3)

**Feature**: Agent Queue Remote Worker Daemon  
**Branch**: `011-remote-worker-daemon`

## Prerequisites

- MoonMind API service and queue endpoints are running.
- Milestone 1 and Milestone 2 queue/artifact migrations are applied.
- `codex` CLI is installed and authenticated (`codex login status`).

## 1. Configure Worker Environment

```bash
export MOONMIND_URL="http://localhost:5000"
export MOONMIND_WORKER_ID="executor-01"
export MOONMIND_WORKER_TOKEN="<token-if-required>"
export MOONMIND_POLL_INTERVAL_MS="1500"
export MOONMIND_LEASE_SECONDS="120"
export MOONMIND_WORKDIR="/tmp/moonmind-worker"
```

Optional:

```bash
export GITHUB_TOKEN="<optional>"
```

## 2. Validate Codex Preflight

```bash
codex login status
```

## 3. Start Worker Daemon

```bash
poetry run moonmind-codex-worker
```

## 4. Enqueue a `codex_exec` Job

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
      "publish": {"mode": "none", "baseBranch": "main"}
    }
  }'
```

## 5. Verify Job Progress

```bash
curl -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  "$MOONMIND_URL/api/queue/jobs?type=codex_exec&limit=5"
```

## 6. Verify Uploaded Artifacts

```bash
curl -H "Authorization: Bearer $MOONMIND_WORKER_TOKEN" \
  "$MOONMIND_URL/api/queue/jobs/<JOB_ID>/artifacts"
```

## 7. Run Unit Tests

```bash
./tools/test_unit.sh
```
