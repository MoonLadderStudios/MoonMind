# RNG Implementation Review and Diagnostics

`moonmind.security.rng_assessment` records implementation review separately from optional statistical diagnostics and supplied external claims. `security.assess` consumes its `rng-assessment/v1` artifact through `retained-report`, using the existing scoped artifact authority and comparison/report owner. There is no RNG certification service or automatic source analysis.

## Review and inputs

A `rng-review-spec/v1` document identifies `gameRef`, reviewer observations, optional sample plans, criteria, and external claims. Each implementation observation has `topic`, `status` (`reviewed`, `concern`, or `unverified`), `observation`, and `evidenceRefs`. Reviewed observations require evidence references. Missing topics receive explicit Unverified rows: generator/API, entropy/seeding, restart/fork, state handling, predictable inputs/fallbacks, scaling, shuffling, rejection sampling, and other transformations.

`raw` and `mapped` plans each declare `originRef`, `transformations`, and `amount`. Amount means bytes for raw input and draws for mapped input, capped at 64 Mi units per plan. The evaluator examines that prefix; a shorter input is incomplete. Raw input is a binary file. Mapped input has one public model outcome ID per ASCII line, at most 1,025 bytes per line. It comes from the implementation being evaluated; the evaluator does not manufacture mapped observations from the raw stream. Declare transformations and sample selection accurately, including rejection, filtering, and any relationship between streams.

```json
{"schemaVersion":"rng-review-spec/v1","gameRef":"game:public-fixture",
 "implementationReview":[{"topic":"generator-api","status":"concern",
   "observation":"The public repeating byte counter is predictable.","evidenceRefs":["fixture:counter"]}],
 "raw":{"originRef":"fixture:counter","transformations":["none"],"amount":32768},
 "rawTests":["bit-frequency","pair-frequency"],
 "mapped":{"originRef":"fixture:counter-low-three-bits","transformations":["reject values >= 6, then modulo 3"],"amount":24576},
 "expectedOutcomes":{"0":"1/3","1":"1/3","2":"1/3"}}
```

`expectedOutcomes` uses positive exact probabilities summing to one, following the [game math](GameMathAssessment.md) evaluator's probability conventions. It supports up to 256 final outcomes. This is a frequency model, not a payout/settlement evaluation; game math remains the owner of RTP and settlement checks. Omit both sample plans and `rawTests` for a review-only report.

## Diagnostic meaning

The documented subset tests single-bit frequency against 1/2, disjoint two-bit pairs within each byte against 1/4, and final-outcome frequency against the declared model. It does not install or implement the complete NIST or dieharder batteries, measure entropy, establish general serial independence, or detect all predictable generators.

Each row retains sample counts, category counts, expected probabilities, method/tool identity, maximum absolute frequency deviation, uncertainty bound, significance, and decision. For N observations and K categories, the two-sided Hoeffding union bound is `sqrt(log(2 K / alpha) / (2 N))`. The declared family significance (default 0.01) is divided among selected tests, then the bound accounts for categories within each test. This controls the declared family under the independent-draw null assumption; correlated or adaptively selected samples can invalidate that interpretation. Choose tests and criteria before examining samples. The default minimum is 1,024 observations and 10 expected observations per category; these are diagnostic limits, not laboratory adequacy criteria.

Decisions are `anomaly`, `no_anomaly_detected`, `insufficient_sample`, or `incomplete`. Failure to detect an anomaly does not establish the distribution or its cryptographic suitability. A repeating public byte counter can pass these frequency checks. Biased modulo mapping of uniform three-bit values into three outcomes fails the mapped test; rejecting values six and seven first preserves the intended model. Raw-bit and mapped decisions remain independently inspectable.

The result keeps `implementationReview`, `raw`, `mapped`, and `externalClaims` separate. `cryptographicSecurity` and `gameFairness` remain `unverified`; `certification` is `not_performed`. Supplied claim text, `reportRef`, and optional `verifierRef` are provenance only: MoonMind has not authenticated the claim or the verifier. Unavailable specialist/laboratory verification stays Unverified and is not a prerequisite for running public fixtures. Statistical coverage in the security report names only performed frequency tests, never cryptographic review or certification coverage.

## Jobs, interruption and protected evidence

Use an existing [workload profile](SecurityTools.md#workload-profiles-and-admitted-scope) with a MoonMind-capable image source. An operator profile can declare:

```json
{"profileRef":"rng-review","imageSourceRef":"moonmind",
 "entrypoint":["python","-m","moonmind.security.rng_assessment"],
 "modes":[{"name":"evaluate","actionClass":"rng-review",
   "argv":["--spec={spec}","--raw={raw}","--outcomes={outcomes}","--max-seconds={maxSeconds}","--output=/outputs/rng-assessment.json"],
   "arguments":[{"name":"spec","type":"input","required":true},{"name":"raw","type":"input"},{"name":"outcomes","type":"input"},{"name":"maxSeconds","type":"integer","minimum":1,"maximum":3600}]}],
 "outputs":[{"name":"assessment","relativePath":"rng-assessment.json"}],
 "maxCpuMillis":2000,"maxMemoryMiB":1024,"maxTimeoutSeconds":600}
```

The profile selects image, entrypoint, read-only captured attachments, output, network isolation, and resource ceilings. Requests cannot raise those ceilings. No network or credential is required. Keep `maxSeconds` below the job timeout so the evaluator can write its result during the supported stop grace period. It defaults to 300 seconds and is limited to 3,600 seconds. SIGTERM/SIGINT stop cooperatively; completed counts and review observations are kept in an atomic partial result. A stopped section retains its counts and uncertainty but cannot report a clean decision. A completed earlier section remains inspectable.

Output collection validates the RNG result and binds job termination to it. Failed, timed-out or canceled jobs cannot yield complete coverage even if a completed file exists or every review-only topic has referenced observations. SIGKILL or OOM before persistence leaves a missing result and failed/incomplete job diagnostics; recovery of in-memory counts is not promised. Invalid inputs exit with status 2 without echoing their values.

Live seeds, internal state, credentials, and raw/sensitive samples belong only in authorized protected input artifacts, never in review prose or public fixtures. The review schema accepts references rather than seed/state fields. The evaluator exports aggregate counts and declared nonsensitive metadata, never raw samples, sample hashes, seeds, or generator state. The existing credential redactor also covers review metadata, with seed/state/sample assignment redaction. This cannot identify arbitrary sensitive prose: keep observations and model labels nonsensitive and use evidence references. Existing attachment access, retention, output-path limits, and report access controls still apply; no second evidence store is involved.

Automated acceptance uses deterministic public fixtures. Integration tests execute profile admission, captured-input authority, the real CLI process, artifact collection, and retained reporting with a Docker command fixture. That proves those application boundaries, not live Docker cancellation or OOM behavior. Live job checks must identify their actual resource limits and termination evidence separately.

## Sourced review references

Reviewed **2026-11-07** against the [NIST publication status table](https://csrc.nist.gov/Projects/random-bit-generation/publications) and primary GLI publication:

| Reference and status | Purpose here |
| --- | --- |
| [SP 800-90A Rev. 1](https://csrc.nist.gov/pubs/sp/800/90/a/r1/final), final, June 2015 | Deterministic generator/API and state review. Rev. 2 is a pre-draft call for comments listed September 2025, not a replacement final standard. |
| [SP 800-90B](https://csrc.nist.gov/pubs/sp/800/90/b/final), final, January 2018 | Entropy-source, seeding and health-test review. |
| [SP 800-90C](https://csrc.nist.gov/pubs/sp/800/90/c/final), final, September 2025 | Generator construction and integration review. |
| [SP 800-22 Rev. 1a](https://csrc.nist.gov/pubs/sp/800/22/r1/upd1/final), final, April 2010; [revision decision](https://csrc.nist.gov/news/2022/decision-to-revise-nist-sp-800-22-rev-1a), April 2022 | Statistical-test limitations; NIST explicitly rejects using its battery to assess cryptographic RNGs. |
| [GLI-19 v4.0](https://gaminglabs.com/wp-content/uploads/2026/11/GLI-19-v4.0-Standards-for-Interactive-Gaming-Systems.pdf), published standard, revision November 3, 2026 | Chapter 5 separates source review, final-outcome statistics and strength; chapter 6 covers game use. |

These are review references, not a claim of conformance. NIST's generator guidance does not certify a game. GLI-19 addresses interactive gaming; applicable local adoption, product configuration and additional requirements depend on the jurisdiction and regulator. Event wagering uses different standards. MoonMind's limited diagnostics do not substitute for independent laboratory evaluation or establish GLI/NIST certification.
