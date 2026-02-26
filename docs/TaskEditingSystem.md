# Task Editing System

Status: Draft (implementation-ready)
Owners: MoonMind Engineering
Last Updated: 2026-02-25

## 1. Purpose

Add the ability to **edit a queued Task** (Agent Queue `type="task"`) **after it’s been enqueued**, but **before it has started running**, by:

* Reusing the existing **Create** UI (`/tasks/new` / `/tasks/queue/new`) and switching the primary CTA to **Update**.
* Adding an API route to **update an existing queued job in place** (preserving the job ID / correlation).
* Enforcing **state + concurrency safety** so updates cannot “race” a worker claim.

This fits the thin-dashboard philosophy in `docs/TaskUiArchitecture.md` (typed submit flows over existing REST) .

---

## 2. Goals and Non-Goals

### Goals

1. **Edit queued task jobs** only when:

   * `job.status == queued`
   * `job.started_at == null` (never started)
     (Worker claim sets `started_at = coalesce(started_at, now)` when transitioning queued→running .)
2. **Preserve the job ID** (no “cancel + create new” workaround).
3. **Reuse the Create UI**:

   * Click “Edit” → opens `/tasks/queue/new?editJobId=<jobId>` (Create route alias already exists: `create -> queue/new` ).
   * Prefill the form from `GET /api/queue/jobs/{jobId}`.
   * Submit uses the new update endpoint.
4. **Auditability**: append a queue event like “Job updated” (queue creation already appends “Job queued” ).

### Non-Goals (v1)

* Editing tasks that are **running** or **already started**.
* Editing orchestrator runs (separate lifecycle).
* Editing attachments (see §10; attachments are deliberately “create-atomically” to avoid claim races ).

---

## 3. UX Design

### 3.1 Entry points

**Queue job detail (`/tasks/queue/:jobId`)**

* If the job is:

  * `type="task"`
  * `status="queued"`
  * `startedAt == null`
* Show **Edit** next to existing controls (Cancel, etc.). Queue detail already loads job detail via `GET /api/queue/jobs/{job_id}` .

**Queue list (`/tasks/queue`)** (optional in v1)

* Add a small “Edit” action on queued, not-started rows/cards.

### 3.2 Edit flow (Create UI reuse)

1. User clicks **Edit**.
2. Browser navigates to:

   * `/tasks/queue/new?editJobId=<uuid>`
3. The Create page:

   * Detects `editJobId`
   * Fetches job: `GET /api/queue/jobs/{jobId}`
   * If not editable, show a clear error + link back to detail.
4. Form shows:

   * Title: “Edit queued task”
   * Primary CTA: **Update**
   * Secondary CTA: “Cancel” (navigates back to `/tasks/queue/{jobId}`)
5. On submit, the UI calls:

   * `PUT /api/queue/jobs/{jobId}` with the same envelope it normally sends to create (type/priority/maxAttempts/payload), plus an optimistic concurrency token (below).

---

## 4. API Design

### 4.1 New endpoint

`PUT /api/queue/jobs/{jobId}`

* Auth: **user auth** (same class of dependency as create/cancel in the queue router and dashboard )
* Behavior: update **only** when job is queued and never started.

### 4.2 Request schema

Define a new Pydantic model in `moonmind/schemas/agent_queue_models.py`, intentionally mirroring `CreateJobRequest`  so the UI can reuse the existing submit serialization:

```json
{
  "type": "task",
  "priority": 0,
  "maxAttempts": 3,
  "affinityKey": "repo/MoonLadderStudios/MoonMind",
  "payload": { "... canonical task payload ..." },

  "expectedUpdatedAt": "2026-02-25T01:23:45.678Z",
  "note": "optional short reason"
}
```

* `expectedUpdatedAt` (optional but recommended for UI) is used for optimistic concurrency.
* `note` is optional; stored only in the “Job updated” event payload (not in the job row).

### 4.3 Response

Returns `JobModel` (200 OK) with the updated job payload. (This is consistent with other queue mutation endpoints returning `JobModel` in REST and schemas .)

### 4.4 Error semantics

* `404 job_not_found` if missing.
* `403 job_not_authorized` if user is not the job owner (same error family already used for user-owned queue operations).
* `409 job_state_conflict` if:

  * job is not queued
  * job has started (`started_at != null`)
  * optimistic concurrency mismatch (expectedUpdatedAt doesn’t match current job.updated_at)
* `422 invalid_queue_payload` for contract validation failures (same normalization rules as create).
* `400 claude_runtime_disabled` (and similar) if normalization rejects runtime based on server configuration (create already maps this scenario ).

---

## 5. Backend Implementation

### 5.1 Service method

Add to `moonmind/workflows/agent_queue/service.py`:

`update_queued_job(...) -> AgentJob`

Responsibilities:

1. Load job under row lock:

   * Use `AgentQueueRepository.require_job_for_update(job_id)` which uses `SELECT ... FOR UPDATE` .
2. Authorize actor:

   * Match existing “user-owned queue operations” semantics (typically `requested_by_user_id` is the owner).
3. Validate editability:

   * `job.status == QUEUED`
   * `job.started_at is None`
4. Concurrency check (recommended):

   * If `expectedUpdatedAt` provided and doesn’t match, raise `AgentJobStateError` (409).
5. Validate type immutability:

   * If request.type != job.type → reject (409 or 400).
6. Normalize payload using the same logic as create:

   * For tasks, call `normalize_task_job_payload` (used in create path) .
7. Apply updates (in-place):

   * `job.priority`, `job.payload`, `job.affinity_key`, `job.max_attempts`
   * set `job.updated_at = now`
8. Append audit event:

   * message: “Job updated”
   * payload: `{ actorUserId, note?, changedFields }`
9. Commit.

### 5.2 Repository changes

No schema changes required.

* The existing `agent_jobs` table already has mutable columns needed (priority, payload, affinity_key, max_attempts, updated_at) .
* We only rely on:

  * `require_job_for_update` lock
  * existing event append method (already used by create) .

---

## 6. Concurrency and Race Conditions

### 6.1 Update vs claim race

Worker claim:

* Scans queued jobs with `FOR UPDATE SKIP LOCKED`
* Attempts atomic transition to `RUNNING` where `status == QUEUED`

Update:

* Locks the specific job row with `FOR UPDATE`
* Verifies it’s still queued + not started

Result:

* If update acquires the lock first, claim skips the locked row and moves on.
* If claim acquires/updates first, update will observe `status=running` (or `started_at` set) and reject.

### 6.2 Optimistic concurrency

Using `expectedUpdatedAt` prevents “two tabs editing” from silently overwriting:

* UI reads `job.updatedAt` from the fetched job.
* UI sends it back as `expectedUpdatedAt`.
* Server rejects if job.updated_at has changed (409 conflict).

---

## 7. Dashboard Config and Routing

### 7.1 Config injection

Add a new endpoint template to the dashboard runtime config in `api_service/api/routers/task_dashboard_view_model.py` under `sources.queue` (where create/detail/cancel already exist) :

* `sources.queue.update = "/api/queue/jobs/{id}"`

### 7.2 UI routes

The dashboard already aliases:

* `/tasks/create` → `/tasks/queue/new`

We’ll use:

* `/tasks/queue/new?editJobId=<id>` for edit mode.

---

## 8. Dashboard JS Changes (High-Level)

In `api_service/static/task_dashboard/dashboard.js`:

1. **Detect edit mode** on `/tasks/new`:

   * Parse `editJobId` from query string.
2. If edit mode:

   * Fetch job detail: `GET /api/queue/jobs/{id}`
   * Prefill form fields from:

     * `job.priority`, `job.maxAttempts`, `job.affinityKey`
     * canonical task payload under `job.payload`
   * Store `job.updatedAt` for concurrency.
   * Change submit button label to **Update**.
3. On submit:

   * Build the same create envelope (type/priority/maxAttempts/affinityKey/payload).
   * Add `expectedUpdatedAt`.
   * Send `PUT` to queue update endpoint.
4. Handle failures:

   * `409`: show “This task already started or changed; refresh / view details”
   * `422`: show validation details
   * `403`: show authorization error

---

## 9. Testing Plan

### 9.1 Service tests

Add unit tests for `AgentQueueService.update_queued_job`:

* Success: queued + started_at None updates payload/priority/maxAttempts and appends event.
* Reject: status != queued.
* Reject: started_at != None.
* Reject: expectedUpdatedAt mismatch.
* Reject: actor not owner.

### 9.2 Router tests

Extend `tests/unit/api/routers/test_agent_queue.py` (pattern used for create ):

* `PUT /api/queue/jobs/{id}` returns 200 and updated job
* maps conflicts to 409, payload errors to 422, auth errors to 403.

### 9.3 Dashboard tests (optional but recommended)

* Add a small JS test to ensure:

  * “Edit” link appears only for queued + not-started task jobs
  * Edit mode swaps the submit label to “Update”
  * Update call uses `sources.queue.update`

---

## 10. Attachments Policy (v1 and future)

**v1:** editing does **not** modify attachments.

Reason: attachments were designed to be uploaded in the same request as job creation so the job is only claimable once attachments exist . Allowing “upload attachments later” introduces a claim race unless we add additional locking/flags.

**Future extension (v2):**

* Add `PUT /api/queue/jobs/{id}/with-attachments` (multipart) that:

  * locks the job row
  * verifies it’s editable
  * persists attachments
  * commits
* Optionally allow deletion of attachments that haven’t been downloaded by a worker yet.

---

## 11. Rollout

* Ship behind no feature flag:

  * It’s additive (new endpoint + UI affordance).
  * It only affects queued jobs and preserves existing create behavior.
* Update documentation:

  * Add the new endpoint to `docs/TaskQueueSystem.md` API surface list (which currently enumerates create/get/claim/heartbeat/etc. but no update) .
  * Add the endpoint to `docs/TaskUiArchitecture.md` “Queue endpoints” list .
