# Game Math Assessment

A game math assessment checks a game's return to player (RTP) and settlement against an explicit mathematical specification. It reports exact theoretical expectations apart from simulated observations, and it says when a simulation was too imprecise to support a conclusion. It never certifies a game or confirms a PAR sheet. That remains the role of the operator's approval process or an accredited laboratory.

The evaluator is `moonmind/security/game_math.py`. It runs as an ordinary [workload-profile container job](SecurityTools.md#workload-profiles-and-admitted-scope), and `security.assess` reads its result through `retained-report`. No wagering service, findings store, or math scheduler is involved.

## Specification

A `game-math-spec/v1` document states everything the evaluation may assume. Probabilities, multipliers, stakes, and amounts are exact: integers or decimal or fraction strings such as `"0.25"` or `"1/6"`. Binary floats are refused because they cannot state a probability exactly.

| Field | Meaning |
| --- | --- |
| `gameRef` | The assessed game; it must be an asset in the `security.assess` scope. |
| `provenance` | `sourceRef`, `contentDigest` (`sha256:...`), and an optional `approvalRef`. These are copied verbatim into the result. An approval reference records the operator's external approval; it is not MoonMind approval. |
| `game` | An `outcome-table` (outcomes with `probability`, gross `multiplier` per unit stake, and an optional `bonus` of independent draws whose multipliers add to the outcome's), or a `crash` game. |
| `wagers` | Each has an `id` and a `stake`. Crash wagers also carry the cashout `target` the strategy assumes. |
| `strategies` | The bets placed together in every round, with an optional written `assumption`. |
| `settlement` | `quantum` (smallest currency unit), `rounding` (`floor`, `ceiling`, `half_up`, `half_even`), and optional `multiplierCap`, `winCap` (`amount` on a `payout` or `profit` basis), `roundCap`, and `fee` (`rate` on a `payout` or `winnings` basis). |
| `declared` | Values the specification publishes per strategy, such as a PAR sheet's `rtp` and, for crash games, `immediateBust`. `resolution` is the published figure's rounding unit; 0 means exact. |
| `criteria` | `tolerance` (the equivalence margin around the target RTP), `confidence`, and `minExpectedEvents` (default 10). |

Probabilities must sum to exactly 1. Stakes and cap amounts must be multiples of the quantum. Malformed specifications are refused before any evaluation.

The criteria belong to the specification, and its digest identifies the result. A run cannot choose or relax its precision after seeing its data.

Each bet settles in a fixed order: multiplier cap, fee, win cap, then rounding to the quantum. The round cap then limits the round's total. Per-bet amounts are each bet's settlement before the round cap.

### Crash games

A crash game declares a multiplier grid `precision`, a `boundary`, a `minTarget` and optional `maxTarget`, and a survival model for the crash point M:

- `inverse`: P(M ≥ t) = (1 − `houseEdge`) / t on the grid above 1. An optional `maxMultiplier` caps the crash point itself.
- `table`: explicit grid points and their probabilities.

A stake cashing out at t pays t when M ≥ t (`inclusive`) or only when M > t (`exclusive`). For a unit stake, RTP(t) = t · P(M reaches the paying threshold). The inverse model with inclusive cashout is the constant-RTP case. Exclusive cashout, a table model, caps, and rounding make RTP vary with the target, and each strategy is judged against its own declared value. No flat-RTP invariant applies to crash games in general.

The immediate-bust probability is P(M < 1 + precision). For the inverse model this exceeds the house edge, because grid flooring adds mass at 1.00x. A specification declares the value it means.

A model `maxMultiplier` and a settlement `multiplierCap` are different implementations. Under the first, a target above the cap never pays. Under the second, the target pays the capped multiplier.

## Evaluation

**Theoretical expectations** are exact rationals computed from the specification alone. Outcome tables are enumerated, including bonus draws. Crash games use the survival function at each strategy's paying thresholds. When the bonus expansion would exceed 100,000 settled outcomes, the strategy is `intractable` and needs a simulated decision instead. A declared value that disagrees with the exact one, beyond half its resolution, is a violation.

**Implementation behavior** arrives as recorded rounds in JSON Lines: `{strategy, outcome | multiplier, bonus, payouts?, total}`. Each round is settled again under the specification. A different amount is a `settlement` violation that lists the rules engaged, such as `boundary`, `rounding`, `win_cap`, or `immediate_bust`. A round the specification cannot produce is an `outcome-support` violation. Settlement cases (`--settlements`) are targeted rounds checked this way.

**Simulated observations** come from a seeded Monte Carlo of the specification (`--samples`, `--seed`), or from a recorded implementation run (`--records`) whose rounds are also re-settled. The estimator is the mean return per unit stake. The interval is a two-sided empirical-Bernstein bound (Maurer and Pontil, 2009), valid for returns bounded by the strategy's largest settled return. Its target is the exact RTP, or the declared RTP when theory is intractable.

| Decision | Condition |
| --- | --- |
| `equivalent` | The whole interval lies within target ± tolerance, and every paying event was expected at least `minExpectedEvents` times. |
| `different` | The whole interval lies outside target ± tolerance. This is reported as a `simulated-rtp` violation. |
| `insufficient_precision` | Anything else, including a wide interval that contains the target or a sparse paying event. |
| `incomplete` | The run was cancelled or exhausted its time budget. |
| `not_compared` | Theory is intractable and nothing was declared. |

Each simulated row records the sample count, estimator, interval method, confidence, bounds, tolerance, target and its source, and each paying event's probability, expected count, and observed count.

## Result

`game-math-assessment/v1` holds `theoretical`, `settlementCases`, `simulation`, `violations`, `status`, `outcome`, `certification: "not_performed"`, and `limitations`. The outcome is `violations`, `incomplete`, `insufficient_precision`, or `consistent`.

A stopped run, unusable records, an intractable strategy without a decided simulation, or truncated violations make the status `partial`. Partial evidence keeps its observations but cannot report a consistent outcome. The contract refuses a document that claims consistency over partial or imprecise evidence, so `security.assess` cannot promote one.

`security.assess` maps violations to `game-math` findings on `gameRef`. Coverage is complete only when every check reached `equivalent` or `different`. A finding's absence is comparable only between results for the same specification digest.

## Running as a job

Deployments add a profile to `MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES` whose image source contains MoonMind, for example:

```json
{"profileRef":"game-math","imageSourceRef":"moonmind","entrypoint":["python","-m","moonmind.security.game_math"],
 "modes":[{"name":"evaluate","actionClass":"game-math",
   "argv":["--spec={spec}","--settlements={settlements}","--records={records}","--samples={samples}","--seed={seed}","--max-seconds={maxSeconds}","--output=/outputs/game-math-assessment.json"],
   "arguments":[{"name":"spec","type":"input","required":true},{"name":"settlements","type":"input"},{"name":"records","type":"input"},
     {"name":"samples","type":"integer","minimum":1},{"name":"seed","type":"integer","minimum":0},{"name":"maxSeconds","type":"integer","minimum":1}]}],
 "outputs":[{"name":"assessment","relativePath":"game-math-assessment.json"}],
 "maxCpuMillis":2000,"maxMemoryMiB":1024,"maxTimeoutSeconds":3600}
```

Each option is a single `--name={value}` entry, so omitting an optional argument drops it cleanly. The specification and records are captured input attachments. The job needs no network or credentials.

Cancelling the job sends SIGTERM. The CLI then stops sampling and still writes its partial result before exiting, and that result is collected as the job's declared output. An invalid specification exits with status 2 and writes nothing.

## Limits of the evidence

Synthetic and reference games exercise the evaluator completely. Comparing a real game with its private PAR sheet, or with an accredited laboratory's evaluation of the same game, needs material only the operator holds. It stays Unverified until the operator supplies it. The result is a retained artifact; control mapping and report packages consume it from their own owners.
