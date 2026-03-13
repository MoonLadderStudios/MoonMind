# Task Queue Ingestion API

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  

## 1. Purpose

Define the legacy HTTP Ingestion boundary for MoonMind task automation. This defines the canonical payload and API surfaces for initiating and probing automated runs (now internally backed by Temporal Workflows).

## 2. API Surface

The API acts as a translation layer buffering UI requests into Temporal executions.

### 2.1 REST Operations

- `POST /api/queue/jobs`
- `GET /api/queue/jobs`
- `GET /api/queue/jobs/{jobId}`
- `PUT /api/queue/jobs/{jobId}`
- `POST /api/queue/jobs/{jobId}/resubmit`
- `POST /api/queue/jobs/{jobId}/cancel`
- `GET /api/queue/jobs/{jobId}/events`
- `GET /api/queue/jobs/{jobId}/events/stream`
- `GET /api/queue/jobs/{jobId}/artifacts`
- `GET /api/queue/jobs/{jobId}/artifacts/{artifactId}/download`
- `POST /api/queue/jobs/with-attachments`

Control operations such as pauses and live interactions translate the REST requests (`POST /api/queue/jobs/{jobId}/control`) into Temporal Signals targeted at the specific Workflow Execution ID.

## 3. Canonical Task Payload

Tasks submitted via `/api/queue/jobs` expect the canonical payload shape.

```json
{
  "repository": "owner/repo",
  "requiredCapabilities": ["git", "claude"],
  "targetRuntime": "claude",
  "auth": {
    "repoAuthRef": null,
    "publishAuthRef": null
  },
  "task": {
    "instructions": "Implement feature and run tests",
    "skill": {
        "id": "auto"
    },
    "publish": {
      "mode": "branch"
    }
  }
}
```

The system ingests this schema and maps it directly into the inputs for the `MoonMind.Run` workflow on Temporal.

## 4. Capability Claims

While the legacy system restricted jobs to explicit queue pull-policies, Temporal Workflows dispatch internal Activities strictly based on the requested `task.skill` or `targetRuntime` mapped to specific Temporal Task Queues.

1. High-level planning and workflow routing are un-constrained.
2. Actual LLM queries route to `mm.activity.llm` queues.
3. Code-execution Sandboxes route to `mm.activity.sandbox` queues.
