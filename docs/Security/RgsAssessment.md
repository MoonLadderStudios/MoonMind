# RGS Transaction Assessment

`moonmind.security.rgs_assessment` checks game, wallet and audit traces against an explicit scenario specification through existing workload profiles and `security.assess` retained reports. Evaluation sends no financial operations. `moonmind.security.rgs_target` can collect traces through the shared scoped HTTP/WebSocket client before evaluation. Mathematical payout modeling remains in [game math assessment](GameMathAssessment.md).

## Inputs and expectations

A `rgs-scenario-spec/v1` document identifies `gameRef`, `sourceRef`, `integrationRulesRef`, and `walletUnit`. Amounts are exact integers in that declared unit. Each wager states its stake, gross cashout credit and ordinary settlement credit; the evaluator does not infer crash-game payouts.

The contract declares round states, initial state, event transitions, allowed states for each operation, betting-close and cashout-close events, and interruption policy (`resume` or `rollback`). Each close boundary chooses recognition at `received` or `processed` time and whether an exact tie is allowed. At an allowed tie, the operation sees the state before the closing event.

Each scenario provides initial balances, wagers, timed events, evidence references, and operations in processing order. Operations have distinct attempt IDs and stable transaction IDs across retries. Receive times may be reordered; equal processing times express competing requests in their selected serial order. Include both serial orders when assessing a race. Reusing a successful transaction ID with a different operation or wager is denied. A duplicate of the same successful request returns its original decision without another wallet effect, even after a close boundary.

A `rgs-traces/v1` input supplies receipts by attempt ID, confirmed wallet effects, balances, wager outcomes, final round state, and corresponding audit effects. Unknown acknowledgements remain incomplete unless a later accepted receipt with the same transaction and operation reconciles them. Final wallet and audit reconciliation still checks every effect independently. An incomplete snapshot cannot establish the absence of a financial effect; confirmed extra effects remain violations.

For target access checks, `authorizationMatrix` uses the shared target contract: `identities` name an `id`, `accountRef`, `sessionRef`, `integrationRef` and opaque `credentialRef`; `cases` name an `id`, `identityRef`, target account/session/integration, `action`, and expected `allow` or `deny`. Each operation binds an `accessCaseRef` whose action and account match its wager. Duplicate identities, unknown references and contradictory cases are rejected. Expectations can permit delegated access; same-account or same-integration access is not assumed universally. Denied access is checked before transaction replay and produces no expected wallet effect. This matrix concerns the assessed application, not MoonMind operator roles.

The synthetic [scenario input](../../tests/fixtures/security/rgs-scenarios.json) and independent [game/wallet fixture](../../tests/fixtures/security/rgs_reference.py) exercise wagers before/at/after closing, cashout timing, replay, reordered delivery, competing cashouts, lost settlement acknowledgements, reconnect and rollback. Deliberate double-debit, double-credit, audit, balance and outcome defects demonstrate detection.

## Jobs and evidence

Register an ordinary profile in `MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES` with an image source containing MoonMind:

```json
{"profileRef":"rgs-scenarios","imageSourceRef":"moonmind",
 "entrypoint":["python","-m","moonmind.security.rgs_assessment"],
 "modes":[{"name":"evaluate","actionClass":"rgs-scenarios",
   "argv":["--spec={spec}","--traces={traces}","--max-operations={maxOperations}","--max-seconds={maxSeconds}","--output=/outputs/rgs-assessment.json"],
   "arguments":[{"name":"spec","type":"input","required":true},{"name":"traces","type":"input","required":true},
     {"name":"maxOperations","type":"integer","minimum":1,"maximum":4096},{"name":"maxSeconds","type":"integer","minimum":1,"maximum":300}]}],
 "outputs":[{"name":"assessment","relativePath":"rgs-assessment.json"}],
 "maxCpuMillis":2000,"maxMemoryMiB":1024,"maxTimeoutSeconds":600}
```

Pass the two documents as captured input attachments. No network or credentials are needed. The CLI defaults to 4,096 operations and 300 seconds; each input is limited to 8 MiB. The schema bounds scenarios, events, operations and evidence independently.

The `rgs-assessment/v1` result retains the specification, canonical specification/trace digests, exact input-byte digests, input references, expected and observed checks, coverage, limitations and a repeatable CLI invocation. The collector binds the actual terminal outcome and captured artifact references, publishing workspace inputs as captured archives before the report. `security.assess` authorizes those references, scopes findings to `gameRef`, and compares matching specifications and trace sources. It imports at most 4,096 violations per report; larger reports remain retained in full, with partial imported coverage and `finding_import_limit` recorded.

SIGTERM or exhausted budgets retain completed checks with incomplete coverage. The CLI atomically saves an initial partial report and replaces it after each operation, so a killed process can retain its latest report. Collection makes failed, canceled or timed-out jobs partial even if a complete file exists. Confirmed violations survive that failure. Retesting the same captured inputs is safe because evaluation performs no target effects.

## Scoped target observations

`rgs-target-plan/v1` binds a `sourceRef`, ordered `operations` (`scenario`, `operation`, `request`) and one read-only `snapshots` request per scenario. Requests use the shared target adapter's `id`, `protocol` (`http` or `websocket`), `path`, `method`, optional `messageType` and `credentialRef`, and JSON `payload`. The RGS payload carries the exact declared `operation`, `action`, `accountRef`, `sessionRef` and `integrationRef`. The observer validates the whole plan before sending anything. The target replies with `decision` (`accepted`, `denied`, `unknown`) and `access` (`allow`, `deny`, `unknown`); HTTP 401/403 establishes denial, including WebSocket upgrades rejected before sending a message. Snapshot responses use the `Trace` contract above.

An observing workload profile runs `python -m moonmind.security.rgs_target` with captured `--spec` and `--plan` inputs, `--capture=/outputs/rgs-traces.json`, and `--output=/outputs/rgs-assessment.json`. Declare both outputs, naming the trace output `capture`. An optional `--resume` names a previously collected capture input. The collector publishes that capture before the assessment, binds its artifact reference and the actual terminal outcome into the report, and retains the dependency through `security.assess`.

The deployment profile fixes `MOONMIND_TARGET_ASSESSMENT_SCOPE` in its `environment`. Its value contains:

- `web`: the existing `WebAssessment` origin, routes, methods, exclusions, active mode, rate, timeout and egress policy;
- `messages`: exact WebSocket paths mapped to permitted message types;
- `credentialSlots`: opaque credential references mapped to existing file-delivered `application_auth` profile slots;
- `maxRequests`: the shared total bound, counting HTTP requests, WebSocket handshakes and messages.

The profile uses `networkMode: bridge` and the same `egressPolicyRef`. Existing signed assessment admission must allow its web action class, origin, routes and exclusions; `webMessages` on that admitted web target lists the permitted channel/message pairs. Selected credentials must match the profile's slots as well as the existing credential grants. The client requires the worker's matching egress policy/proxy and reads bearer values only from `/run/moonmind-credentials/<slot>`. Redirects are refused, sessions do not share cookies, and both protocols share rate/time/request budgets. Request and response messages are bounded to 256 KiB; reflected credentials are refused rather than retained.

The observer reconciles a read-only snapshot, then atomically saves each uncertain operation before transport. Every request, including initial and final snapshots, durably reserves its cost before sending. Captures stay within the 8 MiB recovery limit: collection stops with `capture_budget` before another request when space for its response and reconciliation is unavailable. Resuming preserves the specification, plan, admitted scope, elapsed deadline and spent budget. Attempted operations are skipped; persisted target receipts can reconcile a lost acknowledgement without another send. If the capture is unavailable but the target shows prior effects, collection retains that evidence and persistently stops because the predecessor's spent budget is unknown. Unknown access remains incomplete. Explicit replay operations remain declared test inputs. Retain captures and reports as job artifacts for recovery on another host.

## Interpretation

`consistent` means that the evidence agrees with the declared scenarios. Recorded traces without adapter observations retain `transport_not_exercised`; missing matrix observations retain `authorization_matrix_not_exercised`. Target observations retain response evidence, expected versus observed access checks and final financial reconciliation. They do not establish regulatory certification or coverage of undeclared target operations.

Synthetic HTTP/WebSocket fixtures with persisted accounts, sessions, integration delegation, wallets and audit records cover correct and permissive targets, scope refusal, shared budgets and interrupted recovery. No real money or private customer execution is required. `integrationRulesRef` records the source of the integration expectations; it grants no authority.
