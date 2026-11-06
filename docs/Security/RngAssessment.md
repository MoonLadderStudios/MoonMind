# RNG Assessment

An RNG assessment reviews how a game's random number generator is built and used. It can also run statistical diagnostics on the generator's raw output and test the final game outcomes against the intended model. It keeps all of this apart from what an outside specialist or laboratory reported. It never certifies a generator, and it never establishes cryptographic strength or game fairness.

The evaluator is `moonmind/security/rng_assessment.py`. It runs as an ordinary [workload-profile container job](SecurityTools.md#workload-profiles-and-admitted-scope), and `security.assess` reads its result through `retained-report`, as it does for [game math assessments](GameMathAssessment.md).

## Why the evidence stays separate

- **A battery can miss a predictable generator.** MT19937 passes these tests, yet anyone who sees 624 consecutive outputs can predict the rest. Statistical results therefore never set cryptographic strength, certification, or fairness.
- **Uniform bits can still deal biased outcomes.** Taking a byte modulo 52 deals 48 cards with probability 5/256 and four cards with probability 4/256. Final outcomes are tested against the intended model as a separate section, and declared transformations are analyzed exactly.
- **External verification is a claim, not a check.** MoonMind records what a verifier reported and where the report is. It does not read or verify the report.

## Request

An `rng-assessment-request/v1` document names the assessed generator in `subjectRef`, which must be an asset in the `security.assess` scope. It has at least one of `review`, `diagnostics`, or `outcomes`, plus optional `externalClaims`. Test selection and significance belong to the request, so a run cannot choose them after seeing its data.

### Implementation review

`review` records what a reviewer found in the code and configuration, as implementation observations.

| Dimension | What it covers |
| --- | --- |
| `generator` | Generator and API selection, such as an SP 800-90A DRBG, an OS interface, or a general-purpose PRNG. |
| `seeding` | Entropy sources and seeding assumptions. |
| `restartFork` | Behavior across process restart and fork, such as state duplicated into forked workers. |
| `stateHandling` | Where generator state lives, who can read it, and how it is reseeded. |
| `predictableInputs` | Predictable inputs or fallbacks, such as time-based seeds or a non-cryptographic fallback path. |

Each dimension has a `status` (`concern`, `no_concern_noted`, or `not_reviewed`), a `summary`, and up to 16 `evidenceRefs`. A reviewed dimension must state what was observed. An unreviewed dimension leaves the review partial.

`transformations` lists the steps that turn generator output into game values. For an `inputBits`-bit value k:

- `modulo` returns k mod `range`.
- `scale` returns floor(k · `range` / 2^`inputBits`).
- `rejection` redraws k at or above the largest multiple of `range`, then takes k mod `range`.

The evaluator computes the exact output distribution of these three. Modulo and scale are biased unless `range` divides 2^`inputBits`. Rejection is uniform, and its result records the share of draws it discards. `shuffle` and `other` steps carry only their review. A `range` larger than the input values is refused; record such a step as `other` with a concern.

### Raw-bit diagnostics

`diagnostics` declares the sample and the tests:

| Field | Meaning |
| --- | --- |
| `origin` | `fixture` with a public fixture `name` and `seed`, or `recorded` with a `description` of where the sample was captured and its `transformations`. |
| `bits` | Bits to analyze, at most 2^24. A recorded sample may hold fewer; the result records both amounts. |
| `tests` | Any of `frequency`, `block-frequency`, `runs`, `serial`, and `cumulative-sums` (all by default). |
| `alpha` | The family significance level, default `1/100`. |
| `blockSize`, `patternLength` | Optional settings. When omitted they are derived from the sample size, following the SP 800-22 recommendations (M ≥ 20, M > n/100, fewer than 100 blocks; m < log₂ n − 2). |

The tests are the SP 800-22 Rev. 1a frequency, block frequency, runs, serial, and cumulative sums tests. Their statistics reproduce the publication's worked examples. Bits are read from each byte most significant first. Each test needs at least 100 bits; a declared block size or pattern length can raise that. A test below its minimum is `insufficient_sample`, never a pass. A runs test whose frequency prerequisite fails reports a p-value of 0, as SP 800-22 specifies.

The five tests report seven p-values. With the Bonferroni correction, each is compared with `alpha` divided by the number of p-values the selection reports. That divisor is fixed by the selection, before any data is read.

The fixtures are public and deterministic, so any fixture output can be predicted from its seed:

| Fixture | Purpose |
| --- | --- |
| `sha256-counter` | Healthy reference: SHA-256 of the seed and a counter. |
| `mt19937` | Python's Mersenne Twister: statistically plausible and predictable. |
| `biased` | Each bit is one with probability 17/32: a known anomaly. |

These tests are a diagnostic subset, not a certification battery. Dieharder, the NIST statistical test suite, and other batteries are not installed.

### Mapped outcomes

`outcomes` states the intended model: final outcome identities with exact probabilities that sum to 1, plus a `description` and `transformations` of how outcomes were recorded, `alpha`, and `minExpected` (default 5). Recorded outcomes arrive as JSON Lines, `{"outcome": "<id>"}`. A malformed line is unusable and makes the section partial.

The section reports a chi-square goodness-of-fit test with its statistic, degrees of freedom, and p-value. Each outcome keeps its expected and observed counts, a Wilson interval at 1 − `alpha`, and its adjusted residual. When any expected count is below `minExpected`, no p-value is computed and the decision is `insufficient_sample`. An outcome the model cannot produce is a deviation at any sample size. Finding no deviation is limited by the test's power and does not establish fairness.

### External claims

Each claim names a `claim` (`cryptographic_strength`, `rng_certification`, or `game_fairness`), a `verifier`, an optional `reportRef` to the operator's copy of the report or certificate, and an optional `standard` with its version and final or draft status. A claim with a report reference is `reported`; otherwise it is `unverified`. MoonMind verifies neither.

## Result

`rng-assessment/v1` holds separate sections:

- `implementation`: the review dimensions and the analyzed transformations.
- `rawBits`: the sample record (origin, fixture, description, transformations, requested and analyzed bits, and digest), the tool and suite, the expected distribution (independent uniform bits), `alpha`, the correction, and each test's settings, minimum, statistics, p-values, threshold, and decision.
- `mappedOutcomes`: the outcome test described above.
- `externalClaims` and `assurance`.

`assurance` holds `cryptographicStrength`, `certification`, and `gameFairness`. Their defaults are `not_established`, `not_performed`, and `not_established`, and only a reported external claim of the matching kind changes one to `externally_reported`. The contract refuses any other value, so no statistical result can promote one.

| Outcome | Condition |
| --- | --- |
| `findings` | A review concern, a biased transformation, a raw-bit anomaly, or an outcome deviation. |
| `incomplete` | A partial section: an unreviewed dimension, a stopped run, or unusable outcome rows. |
| `insufficient_sample` | A test or the outcome model lacked its minimum sample. |
| `no_findings` | Every requested section is complete and decided. |

A partial run keeps what it collected, but it reports only `findings` or `incomplete`. The contract recomputes the status and outcome from every section and refuses a document that disagrees, so an incomplete run cannot be presented as a clean one. Each result lists its limitations, such as `statistics_do_not_establish_unpredictability`, `public_fixture_sample`, `below_minimum_sample`, and `certification_not_performed`.

`security.assess` gives each section its own coverage row under source `rng`:

| Tool | Rules | Comparable when |
| --- | --- | --- |
| `rng-review` | `review:<dimension>`, `review:transformation`, `transformation-bias` | The declared transformations match. |
| `rng-diagnostics` | `raw-bits:<test>` | Origin kind, fixture name, requested bits, tests, settings, and `alpha` match. A fixture run therefore cannot clear a recorded sample's anomaly. |
| `rng-outcomes` | `mapped-outcome:distribution`, `mapped-outcome:support` | The model, `alpha`, and `minExpected` match. |

A row covers only rules it decided. A statistical row never covers a review rule. The assurance values are recorded as provenance and in the report's interpretation line, never as coverage.

## Protected material

The result carries a recorded sample only as its SHA-256 digest and size, and recorded outcomes only as counts. It has no field for a recorded generator's seed or state, and a cancelled run writes only the statistics it finished. Fixture seeds are public by design.

Recorded samples and outcomes enter the job as captured input attachments under the existing artifact authority. The job runs without network access or credentials, and its declared output passes through the container job's existing redaction. Review summaries are free text, so cite evidence by reference instead of pasting live seeds, keys, or samples.

## Running as a job

Deployments add a profile to `MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES` whose image source contains MoonMind, for example:

```json
{"profileRef":"rng-assessment","imageSourceRef":"moonmind","entrypoint":["python","-m","moonmind.security.rng_assessment"],
 "modes":[{"name":"evaluate","actionClass":"rng-assessment",
   "argv":["--request={request}","--sample={sample}","--outcomes={outcomes}","--max-seconds={maxSeconds}","--output=/outputs/rng-assessment.json"],
   "arguments":[{"name":"request","type":"input","required":true},{"name":"sample","type":"input"},{"name":"outcomes","type":"input"},
     {"name":"maxSeconds","type":"integer","minimum":1}]}],
 "outputs":[{"name":"assessment","relativePath":"rng-assessment.json"}],
 "maxCpuMillis":2000,"maxMemoryMiB":1024,"maxTimeoutSeconds":3600}
```

The sample is raw bytes. `--max-seconds` (default 300) bounds the whole run, and a 2^24-bit sample takes a few seconds. Cancelling the job sends SIGTERM. The CLI stops between tests and within long ones, then still writes its partial result, which is collected as the job's declared output. Unrun tests stay listed as `not_run`. An invalid request, a recorded origin without its sample, or an outcome model without its records exits with status 2 and writes nothing.

## References

| Reference | Status | Use here |
| --- | --- | --- |
| [NIST SP 800-22 Rev. 1a](https://csrc.nist.gov/pubs/sp/800/22/r1/upd1/final), statistical tests for random number generators | Final (2010). NIST [decided in 2022 to revise it](https://csrc.nist.gov/news/2022/decision-to-revise-nist-sp-800-22-rev-1a). | Test definitions, minimums, and worked examples. |
| NIST SP 800-90A Rev. 1, deterministic random bit generators | Final (2015) | Reference for reviewing `generator`. |
| NIST SP 800-90B, entropy sources | Final (2018) | Reference for reviewing `seeding`. |
| NIST SP 800-90C, RBG constructions | Published as drafts; confirm on the [RBG publications page](https://csrc.nist.gov/Projects/random-bit-generation/publications) whether a final version has replaced the 2024 fourth public draft. | Reference for reviewing construction, restart, and fork behavior. |
| [GLI-19 v4.0](https://gaminglabs.com/wp-content/uploads/2026/11/GLI-19-v4.0-Standards-for-Interactive-Gaming-Systems.pdf), standards for interactive gaming systems | Cited version; its text was not reviewed for this design. | The operator's laboratory applies its RNG requirements. MoonMind maps no result to its clauses. |

## Limits of the evidence

Public fixtures exercise the evaluator completely. Reviewing a real generator's code, sampling its live output, and laboratory or specialist verification all need material only the operator holds. They stay Unverified until the operator supplies it. The result is a retained artifact; control assessment, framework mapping, sensitive-evidence handling, and reports consume it from their own owners.
