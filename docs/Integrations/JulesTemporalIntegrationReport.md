# Jules Temporal Integration Report
Integrating Jules with Temporal requires updating our documentation and implementation to leverage Temporal’s workflow model, eventing, and reliability features. Key changes include updating the **Jules Temporal External Event Contract** to explicitly use Temporal signals (ExternalEvent) and polling fallbacks, enforcing the existing runtime gate, and preserving existing Jules request/response semantics (e.g. status normalization, retry rules)【51†L95-L102】【45†L48-L53】. The **Jules Client Adapter** docs should be enhanced to describe using `idempotencyKey` and `correlationId` (e.g. using the Temporal workflow run ID) in the `integration.jules.start` payload, as well as scrubbing secrets from logs【49†L97-L105】【48†L30-L33】. The **Jules Proposal Delivery** doc should emphasize Temporal’s durable workflow guarantees, show how we register `integration.jules.send_proposal` and `sync_proposal_status` activities【45†L28-L32】, and prefer signals for callbacks while falling back to polling (citing the ExternalEvent contract)【45†L48-L53】. We recommend adding concrete examples (JSON snippets) and language/runtime notes (Go/Java/TypeScript) in each doc.

We have identified a phased implementation plan: (1) **Adapter & Schema** (medium effort) – add idempotency, callback support, status normalizer in code and docs【51†L99-L104】, (2) **Activity Registration & Workflow** (large) – implement `integration.jules.start/status/fetch_result` and `MoonMind.ProposalDelivery` workflow, (3) **Testing & Observability** (medium) – write unit/contract tests and add metrics/logging, (4) **UI/Compatibility** (small) – update dashboards to show Jules task info separately from workflow ID. Each task has clear acceptance criteria (e.g. “valid starts return `external_operation_id`, invalid setups are rejected”【50†L7-L10】【51†L95-L99】). We will enforce event-contract versioning (e.g. version field in future ExternalEvent signals) and maintain backward-compatibility with existing polling logic. Observability will include metrics for activities and signals, logs annotated with `correlation_id`, and persistent artifacts (terminal snapshots, failure summaries) stored via the Temporal artifact backend【51†L105-L109】. Security measures include keeping API keys out of workflow history and validating any future Jules callbacks.

Below we detail **specific document edits** with example snippets, **implementation tasks** (with effort and criteria), recommended **tests**, versioning strategy, observability checklist, and security considerations.

## 1. Documentation Updates

### 1.1 **docs/Integrations/JulesTemporalExternalEventContract.md**
- **Section 1–4 (Introduction)**: Clarify that Jules is a *provider-specific external-monitoring profile* for Temporal. Emphasize Temporal’s shared “ExternalEvent” contract for callbacks and the current policy of polling-first with callback readiness (align with FR-002【51†L94-L99】). For example, add a note:

  > *Note:* Jules workflows will use Temporal signals (`ExternalEvent`) for callbacks in the future, but must fall back to polling today. As with other integrations, the MoonMind hybrid model requires polling now and only uses callbacks after verified support.

- **Section 5 (Runtime State)**: Explicitly define fields. For example, add a JSON schema snippet:

  ```jsonc
  {
    "integration_name": "jules",             // canonical provider name【51†L95-L99】
    "correlation_id": "<MoonMind workflow ID>",  // stable per-run id
    "external_operation_id": "<Jules taskId>",  // the Jules task identifier【51†L95-L99】
    "provider_status": "<raw status>",          // raw status string (preserved)
    "external_url": "<task URL or null>"        // provider deep link
  }
  ```

  This aligns with FR-003/FR-006 requirements (use “jules” and map `taskId`→`external_operation_id`)【51†L95-L99】.

- **Section 6 (Runtime Gate)**: Emphasize enforcing the existing Jules enablement flags (`JULES_ENABLED`, etc.) across *all* code paths (API, workers, Temporal). Add: “If Jules is disabled or misconfigured, the Temporal activities for Jules must immediately error out (same as non-Temporal path) and not schedule any provider call”【50†L7-L10】【51†L96-L99】.

- **Section 7–9 (Request/Response Contract, Monitoring)**: Ensure wording preserves the compact, retry-safe JMS patterns. For example, note “Use the same compact JSON shapes as today. Status activities should be idempotent and retry with backoff on 5xx/429, and fail fast on other 4xx (per existing adapter rules)”【51†L95-L102】. Reinforce using a centralized status-normalizer (e.g. cite `normalize_jules_status` in code) to map raw states into `queued`, `running`, `succeeded`, etc., falling back to `unknown`【49†L8-L16】【51†L99-L100】.

- **Section 10 (Activities)**: Confirm activities run on the default queue. E.g.: “Use the standard `mm.activity.integrations` task queue (e.g. `integration.jules.start`, etc.) and do NOT introduce a new Jules-specific queue”【51†L100-L104】. Under 10.2, specify inputs/outputs of `integration.jules.start`: it should include `correlationId`, `idempotencyKey`, `title`, `description` (with any `inputRefs`, `parameters`), and optionally `callbackUrl`/`callbackCorrelationKey`. Indicate that `callback_supported` defaults to `false` unless `callbackUrl` is provided (matching `JulesIntegrationStartResult` fields【49†L119-L124】). For instance:

  ```markdown
  **Example:**
  ```
  integration.jules.start({
    "correlationId": "<workflow-id>",
    "idempotencyKey": "<stable-key>",
    "title": "...",
    "description": "...",
    "metadata": { ... }
  })
  ```
  Returns the `external_operation_id` (taskId) and `provider_status`, with `callback_supported: false` (until we enable real callbacks)【51†L95-L102】.
  ```

- **Section 11–12 (Polling vs Callback)**: Highlight that polling should use Temporal timers/backoff and avoid duplicate completions if a future callback arrives【51†L103-L106】. Clarify that **current implementation is polling-only**: “Temporal workflows should schedule periodic status checks (`integration.jules.status`) until a terminal state is reached, using bounded backoff”【51†L103-L106】. Also note the future ExternalEvent design (authenticated, deduplicated events stored as artifacts, not in history) per FR-013【51†L105-L107】.

- **Section 13–15 (Artifacts, Security, Compatibility)**: Stress compactness and artifact use. For example, add: “Terminal Jules data (like resolution details) should be saved as *artifacts* via the Temporal artifact backend, not in workflow history”【51†L105-L109】. Include a reminder that *no secrets* (API keys, tokens) go into history or logs【48†L30-L33】【51†L106-L109】. Finally, in compatibility, note that UIs should show both workflow and Jules IDs clearly and never substitute `taskId` for the workflow ID【51†L108-L109】.

### 1.2 **docs/Integrations/JulesClientAdapter.md**
*(Existing adapter documentation should be enhanced to cover Temporal-specific usage.)*

- **General Description**: Add an introductory note that the `JulesClient` now supports integration semantics (idempotency, correlation, callbacks). For example, mention that `JulesClient.start_integration()` automatically sets a `moonmind` metadata block with `correlationId` and `idempotencyKey` based on inputs【48†L119-L127】【49†L97-L105】. Recommend providing a stable `idempotencyKey` (e.g. using the Temporal runId or other workflow-unique ID) so retries don’t re-run the same Jules task.

- **Start / Status / Fetch methods**: For each API call (start task, get status, fetch result, resolve/cancel), clarify the contract:
  - **Start**: The adapter’s `start_integration` wraps the Jules `/tasks` endpoint. Emphasize its behavior: it returns a `JulesIntegrationStartResult` containing `externalOperationId` (the Jules taskId), `normalizedStatus`, `providerStatus`, and possibly `externalUrl` (task link)【49†L119-L127】【48†L141-L149】. Stress that it uses HTTP bearer auth and respects timeouts/retries (consistent with older client) and that failure messages are scrubbed (see `JulesClientError` removing sensitive info)【48†L30-L33】. Provide a code snippet of how to call it in Python (or reference common patterns in Go/Java/TS):
    ```python
    result = await jules_client.start_integration(
        JulesIntegrationStartRequest(
            correlationId=workflow_id,
            idempotencyKey=unique_key,
            title="...",
            description="...",
            metadata={...}
        )
    )
    print(result.externalOperationId, result.providerStatus, result.externalUrl)
    ```
  - **Status**: Document that `get_integration_status()` calls `/tasks/{id}` and returns `normalizedStatus`, `providerStatus`, and `terminal` flag (true if status is terminal)【48†L183-L191】【49†L139-L148】. Note it uses retries for network errors.
  - **Fetch Result**: Explain that `fetch_integration_result()` should retrieve any final output or resolution notes, returning references (`output_refs`, `summary`, etc.) and the status【49†L167-L173】. Mention that large payloads (logs, diffs) may not exist in Jules and that summary/diagnostic artifacts should be stored via the artifact system.
  - **Cancel**: Document that provider cancellation is *unsupported* for now; calling cancel yields a `JulesIntegrationCancelResult` indicating “not performed” while the workflow can still cancel locally【51†L103-L108】.

- **Error Handling**: Add text about `JulesClientError` (from `jules_client.py`) and how errors are surfaced: “Errors from the Jules API are raised as `JulesClientError`, whose string representation omits any secret (API keys)【48†L30-L33】. The adapter will retry on server/timeouts and fail fast on client errors as before.”

- **Observability**: Recommend logging context: e.g., “Ensure logs include `correlationId` and `externalOperationId` for each call for tracing.” Possibly show linking to a tracing example.

- **Language/Runtime Notes**: Since Temporal supports Go, Java, TypeScript, add remarks for each (brief): e.g. “In Go, use the Temporal SDK’s activity functions to call `JulesClient` (e.g. in `activity_runtime.go`); in TypeScript, use async/await with the `@temporalio` client library to invoke API endpoints. All languages must still abide by the contracts above.”

### 1.3 **docs/Temporal/JulesProposalDelivery.md**
*(This doc already outlines the design; we recommend small updates and clarifications.)*

- **Temporal Workflow**: Emphasize that this is a **Durable Temporal workflow**. Perhaps preface section 3.1 with: “Implemented as a long-running Temporal workflow (`MoonMind.ProposalDelivery`), this sequence will survive restarts and failures without losing the proposal payload.” Mention using `Workflow.sleep` or timers for periodic status checks if needed.

- **Activities**: In 3.2 and 4, link to the activity catalog. Possibly insert a table of inputs/outputs (or confirm with spec):
  - For `integration.jules.send_proposal`, clarify **input**: MoonMind proposal ID and payload; **output**: `externalOperationId` (Jules ID) and initial status.
  - For `integration.jules.sync_proposal_status`, clarify **input**: the `externalOperationId`; **output**: normalized status and provider status (as given by Jules)【45†L54-L60】. Use a snippet similar to [45] or [13].

- **Polling vs Callback**: Reinforce that the workflow should *prefer signals* for final status but *polls otherwise*. For example, update 3.3 to add: “Once the proposal is sent, the workflow waits either for a Jules callback signal or polls `integration.jules.sync_proposal_status` until a terminal state (`promoted` or `rejected`) is reached.” Reference the ExternalEvent design: “(See `JulesTemporalExternalEventContract.md` for the expected callback signal format【45†L48-L53】.)”

- **Correlation**: Mention carrying over the `correlation_id` from the proposal creation through the Jules payload metadata. Section 6 (Security and Correlation) notes stable correlation: cite that the activity adds it to metadata【45†L70-L74】. For example, state: “We attach a unique `correlation_id` (e.g. the MoonMind proposal ID) to the Jules task metadata to ensure idempotency and tracking【45†L70-L74】.”

- **Failure Handling**: Possibly elaborate on termination: e.g. add a bullet “If the workflow itself is canceled, it should still attempt to cancel the proposal in Jules (even though provider cancel is unsupported) and record the cancellation outcome.”

- **Code/Params**: Consider adding an example or reference to where this is implemented, e.g. “The activities correspond to entries in `moonmind/workflows/temporal/activity_catalog.py` (see code) and the workflow logic is in `moonmind/workflows/temporal/workers.py`.”

Overall, each doc should include cross-references to the shared Temporal integration guidance (implicitly via example JSON or wording) and highlight Jules-specific rules from the spec (e.g. FR-006 normalizer, FR-015 artifacts【51†L99-L100】【51†L105-L108】).

## 2. Implementation Tasks (Prioritized)

| Task | Effort | Acceptance Criteria |
| --- | :---: | --- |
| **Extend Jules adapter schema & runtime**: Add `idempotencyKey`, `correlationId`, and optional `callbackUrl` fields to the adapter’s start request; enforce adding them in code (`JulesIntegrationStartRequest`)【49†L97-L105】. Ensure `normalize_jules_status` covers all alias mappings (per FR-007)【49†L8-L16】. | M | Unit tests confirm: (a) valid `integration.jules.start` payload includes a stable idempotencyKey and correlationId; (b) retrying a start with same idempotencyKey yields the same externalOperationId; (c) all known Jules statuses map correctly, unknown ones yield `"unknown"` while preserving raw status【51†L99-L100】. |
| **Register/Implement Temporal activities**: Create activity handlers for `integration.jules.start`, `.status`, and `.fetch_result` in `moonmind/workflows/temporal/activity_runtime.py`. Activities must call the adapter and wrap results in the normalized contract (per `JulesIntegration*Result`)【49†L119-L127】【49†L139-L148】. Handle retries/exceptions according to existing policy (retry on 5xx/timeout, fail-fast on other 4xx)【51†L95-L102】. | L | Integration tests (or contract tests) show: (a) `integration.jules.start` returns correct fields on success (externalOperationId, status, callbackSupported=false)【51†L100-L104】; (b) simulated Jules errors (429, network error, 4xx) behave per policy; (c) start with invalid config (JULES_DISABLED) is rejected immediately. |
| **Workflow orchestration**: Implement `MoonMind.ProposalDelivery` workflow (in `workers.py`), wired to trigger from the API on proposal creation. It should call `send_proposal`, then loop on `sync_proposal_status` until terminal, or await a signal. | L | End-to-end tests using Temporal test server: (a) Proposal with valid Jules config triggers one workflow instance; (b) If Jules eventually `promotes` or `rejects`, the workflow marks it accordingly and records an artifact (per doc); (c) Duplicate or out-of-order signals do not cause double completions (use signal deduplication logic). |
| **Testing & QA**: Develop unit tests in `tests/unit/` and contract tests in `tests/contract/`. Include: (a) Activity topology test verifying no new queues (only `mm.activity.integrations`)【51†L100-L104】, (b) Contract tests for the documented activity inputs/outputs (use `tests/contract/test_temporal_activity_topology.py` patterns)【22†L9-L13】, (c) Security tests ensuring no secrets in failures. | M | All new tests pass. Contract tests (using e.g. JSON schema) confirm activity signatures match docs. Security tests confirm no API keys or tokens appear in any serialized exception or activity result. |
| **Observability & Artifact storage**: Instrument activities/workflows to emit metrics (counter of proposals sent, failed, etc.) and logs with correlation IDs. Ensure final Jules snapshots (task JSON, failure summaries) are saved using the Temporal artifact backend【51†L105-L109】. | M | Monitored runs produce metrics in Prometheus/Grafana (if applicable). Artifacts appear under the execution’s history (via Temporal UI), including one artifact for the final task snapshot and one for any failure details. |
| **UI/Dashboard updates**: Extend the Task Dashboard to display the Jules `external_operation_id` separately from the workflow ID, and show normalized status, with a link to `externalUrl` if present【51†L107-L109】. | S | Manual test: For a completed Jules-backed proposal, the UI shows both the MoonMind workflow ID and the Jules taskId, does not confuse them, and includes a clickable external link if given. |

## 3. Testing Strategy

- **Unit Tests:** Cover the new adapter logic (`JulesIntegrationStartRequest/Result` handling, status normalizer) and guard clauses for the runtime gate. For example, `tests/unit/jules/test_status.py` should include unknown/new status mapping to `"unknown"`【49†L8-L16】【51†L99-L100】. Adapt existing tests (`test_jules_client.py`) to use the new start/result schemas.

- **Contract Tests:** As per [22†L9-L17], use schema-validation tests (`tests/contract/`) to ensure the activity inputs/outputs in the documentation remain consistent with code. For example, verify that only `integration.jules.start/status/fetch_result` exist and are on the correct queue【51†L100-L104】.

- **Integration Tests:** Run in an isolated Temporal environment (possibly using Temporal’s testing library). Simulate a mock Jules server to return different statuses. Test full workflow execution: proposal creation → activity calls → polling loop → completion. Include scenarios for intermediate failures (e.g. 429s, then recovery).

- **Manual End-to-End:** Deploy to a staging Temporal cluster and test via the API: toggle `JULES_ENABLED`, start a Jules proposal, ensure logs and outcomes match the spec.

- **Observability Tests:** Verify that metrics (e.g. using a metrics client) and artifacts are produced. For secrets: add a test that logs any exception from the Jules client call and assert that the `JulesClientError` string matches the scrubbed format (no token content)【48†L30-L33】.

## 4. Event Contract Migration & Versioning

- **Backward Compatibility:** Since we currently do not use callbacks, no existing flows break. Once callbacks are introduced, version the event format. For instance, include a `version` field in the ExternalEvent payload or use a new **signal name** (e.g. `Jules.ExternalEvent.v2`) so old workflows ignore unknown signals. Maintain the old polling logic until all providers support callbacks.

- **Schema Evolution:** Track changes using an event schema registry (or at least documented versions in the code). For example, if adding new fields (like `error_code`) to callback events, treat them as optional and default them to maintain compatibility.

- **Upgrade Path:** If any fields in the Temporal contract must change, apply Temporal’s [versioning guidelines](https://docs.temporal.io/docs/typescript/workflows#versioning) (e.g. `workflow.Version` in Go/Java, or feature flags) to avoid history conflicts. For the Event payload, consider incrementing an `eventVersion` for significant changes.

## 5. Observability & Monitoring Checklist

- **Traceability:** Log `correlation_id` and `external_operation_id` with every activity invocation. Use structured logging so these fields can be indexed in logs (e.g. via `logger.info(..., correlationId=..., externalOperationId=...)`).

- **Metrics:** Track counters and histograms:
  - *Activity starts/completions/failures* for each Jules activity (e.g. counts of `integration.jules.start` calls, latency histograms).
  - *Workflow metrics*: number of ProposalDelivery workflows started, succeeded, failed.
  - *Status polling*: number of polls per execution.

- **Dashboards:** In the Temporal Web UI, verify:
  - The workflow list shows Jules jobs with a distinct label (maybe “Jules Proposal”).
  - The proposal entity’s status field reflects normalized state (`promoted`/`rejected`), and the UI includes a link to `externalUrl`.

- **Artifacts:** For each workflow, ensure artifacts (terminal snapshot and failure summary) are attached. Periodically scan an S3/DB location to confirm artifacts saved for Jules runs. Include an automated check (as an acceptance test) that a successful run creates at least two artifacts: one JSON snapshot of the Jules task, one text summary of outcome.

- **Alerting:** (Not code-level but ops) – Define alerts for failed activities (e.g. if a Jules call fails repeatedly), or for missing callbacks beyond a timeout.

## 6. Security Considerations

- **Secrets Management:** Jules API keys must **never** be serialized into workflow history or sent to clients. As designed, `JulesClient` only sends the API key in the HTTP `Authorization` header, and scrubs it from exceptions【48†L30-L33】. We should double-check (audit) that the new start/status/fetch code paths do not log the raw token.

- **Callback Authentication:** When callbacks are eventually implemented, they must be *authenticated*. For example, use an HMAC or a pre-shared secret on the callback URL, then verify it before signaling the workflow (as stated in the spec FR-013). Log and ignore any callback with invalid auth or unknown `external_operation_id`.

- **Idempotency & Replay:** Use the provided `idempotencyKey` in requests so that Temporal retries (or duplicate calls) do not create multiple Jules tasks. The adapter already uses this key in metadata【48†L122-L130】. Also, enable Temporal’s *context propagation* to guard against duplicate signals: for ExternalEvent signals, include a unique event ID and use `workflow.RegisterSignalWithStart` or similar to dedupe.

- **Least Privilege:** Ensure that only the worker process (with Jules credentials) can call the Jules API. The API endpoints exposed to clients should not reveal Jules details (no leaking of `external_url` beyond the intended UI).

- **Auditing:** Maintain a clear audit trail: every Jules-backed start, status check, and final result should record who initiated it (via `correlation_id` linking back to the user’s action in MoonMind). Do not log raw stack traces of errors – use the `JulesClientError` messages instead.


## 7. Design Options Comparison

| Aspect               | Polling-First (Current)         | Callback-First (Future)                         |
|----------------------|---------------------------------|-------------------------------------------------|
| **Complexity**       | Simpler to implement now.       | Requires provider support and callback endpoint.|
| **Latency**          | Higher (periodic checks).       | Lower (push updates instantly).                 |
| **Reliability**      | Guaranteed by Temporal (retries). | Also reliable if implemented correctly with ack.|
| **Design Effort**    | Moderate (reuse existing).      | High (secure callback handling, idempotency).   |
| **Temporal Features**| Uses `Workflow.sleep`/timers.   | Uses `Workflow.SignalExternalEvent`.            |
| **Backward-Compat.** | N/A (existing).                 | Must version event contracts.                   |

**Mermaid Workflow Diagram:** Jules integration workflow (polling variant):

```mermaid
flowchart TD
  subgraph Workflow
    A[Start Jules-backed task] --> B[integration.jules.start]
    B --> C[Store external_operation_id]
    C --> D[Periodic loop]
    D -->|poll| E[integration.jules.status]
    E --> F{Status = terminal?}
    F -- No --> D
    F -- Yes --> G[integration.jules.fetch_result]
    G --> H[Persist artifacts & summary] --> I[Workflow done]
  end
  subgraph AsynchronousEvents
    JB[Jules callback?]
    JB -.-> F
  end
```

**Mermaid Gantt Chart:** Implementation phases timeline (S/M/L estimated durations):

```mermaid
gantt
  title Implementation Timeline
  dateFormat  MM-DD
  section Adapter & Schemas
    Add idempotency, metadata fields :done,   des1, 03-01, 03-05
    Update Adapter code/tests       :done,   des2, 03-06, 03-10
  section Activities & Workflow
    Define activities & contract    :active, act1, 03-10, 2d
    Implement workflow logic        :         act2, after act1, 5d
  section Testing & Observability
    Unit/Contract tests             :         test1, 03-17, 3d
    Instrument logs/metrics         :         test2, after test1, 2d
  section UI & Rollout
    Dashboard updates               :         ui1, 03-22, 2d
    Final QA & documentation        :         doc, after ui1, 2d
```

**References:** Internal docs and code snippets are cited throughout, e.g. the proposal delivery architecture【45†L28-L36】 and activity contracts【45†L54-L60】【49†L119-L127】, and the specification’s requirements【51†L95-L103】【48†L30-L33】. All recommendations align with Temporal’s constraints (deterministic workflows, durable timers, signals) and leverage existing MoonMind patterns (shared status normalizer, artifact backend). The updated docs and tasks above ensure Jules integration will be robust, observable, and compatible with Temporal best practices.