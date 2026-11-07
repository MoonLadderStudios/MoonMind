# RGS Transaction Assessment

`moonmind.security.rgs_assessment` checks recorded game, wallet and audit behavior against an explicit scenario specification. It adds assertions to the existing workload-profile and `security.assess` retained-report paths. The evaluator reads captured inputs and sends no requests or financial operations. Mathematical payout modeling remains in [game math assessment](GameMathAssessment.md).

## Inputs and expectations

A `rgs-scenario-spec/v1` document identifies `gameRef`, `sourceRef`, `integrationRulesRef`, and `walletUnit`. Amounts are exact integers in that declared unit. Each wager states its stake, gross cashout credit and ordinary settlement credit; the evaluator does not infer crash-game payouts.

The contract declares round states, initial state, event transitions, allowed states for each operation, betting-close and cashout-close events, and interruption policy (`resume` or `rollback`). Each close boundary chooses recognition at `received` or `processed` time and whether an exact tie is allowed. At an allowed tie, the operation sees the state before the closing event.

Each scenario provides initial balances, wagers, timed events, evidence references, and operations in processing order. Operations have distinct attempt IDs and stable transaction IDs across retries. Receive times may be reordered; equal processing times express competing requests in their selected serial order. Include both serial orders when assessing a race. Reusing a successful transaction ID with a different operation or wager is denied. A duplicate of the same successful request returns its original decision without another wallet effect, even after a close boundary.

A `rgs-traces/v1` input supplies receipts by attempt ID, confirmed wallet effects, balances, wager outcomes, final round state, and corresponding audit effects. Unknown acknowledgements remain incomplete unless a later accepted receipt with the same transaction and operation reconciles them. Final wallet and audit reconciliation still checks every effect independently. An incomplete snapshot cannot establish the absence of a financial effect; confirmed extra effects remain violations.

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

The `rgs-assessment/v1` result retains the specification, canonical specification/trace digests, exact input-byte digests, input references, expected and observed checks, coverage, limitations and a repeatable CLI invocation. The collector binds the actual terminal job outcome and captured artifact references. `security.assess` authorizes those input references, scopes findings to `gameRef`, and compares only matching scenario specifications and trace sources.

SIGTERM or exhausted budgets retain completed checks with incomplete coverage. The CLI atomically saves an initial partial report and replaces it after each operation, so a killed process can retain its latest report. Collection makes failed, canceled or timed-out jobs partial even if a complete file exists. Confirmed violations survive that failure. Retesting the same captured inputs is safe because evaluation performs no target effects.

## Coverage limits

`consistent` means that the supplied traces agree with the declared scenarios. It does not establish transport confinement, target authentication or authorization, or certification. Reports explicitly retain `transport_not_exercised` and `authorization_matrix_not_exercised`.

The shared target authorization matrix and real-time assessment adapters are not yet available. Cross-account, cross-session and cross-integration execution and live HTTP/real-time scope enforcement remain incomplete until those shared owners supply their evidence. `integrationRulesRef` records the specification source; it grants no authority and cannot substitute for those checks. Synthetic fixtures require no real money or private customer execution.
