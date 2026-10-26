# Security interpretation artifact

Write `artifacts/security/<candidate>/review.json` and a readable summary, and
retain both through the existing step artifact mechanism. Record the returned
artifact refs. A later native `security.assess` step can consume the returned
`reviewRef` alongside native `reportRefs`; with no scanner, use `tools: []` and
the explicit `reviewRef`. Supply the same admitted scope, empty offline
credential/network refs and the remaining budget. Direct review does not imply
completed scanner coverage.

The bundled [closed JSON schema](security-review.schema.json), produced from
`SecurityReview` in `moonmind/security/assessments.py`, has:

- `schemaVersion: security-review/v1`, `scope` (assets, exclusions and repository
  exceptions) and `candidateRef` naming the examined code/artifact revision.
- `threatModel`: explicit `entryPoints`, `trustBoundaries` and `invariants` lists.
- `interpretations`: list of `id`, `subject` (`asset`, precise `locator`, actual
  `identity` and `resource`), `hypothesis`, `disposition`
  (`confirmed` / `likely` / `unresolved`), `evidenceStatus`
  (`supported` / `contradicted` / `unsupported`), completed `evidenceRefs`, optional
  `nativeFindingId`, and nonempty `limitations`. Explain inapplicable identity or
  resource fields explicitly. Evidence refs must be readable; a native finding
  ID must exist in the native comparison. Contradicted or unsupported assertions
  remain unresolved, with the contradiction or missing proof in limitations.
- Nonempty overall `limitations`, including scanner absence, skipped coverage,
  unavailable test/deployment checks and reasoning uncertainty.
- Optional `retests`: `hypothesisId` of a retained interpretation,
  `originalEvidenceRefs`, `currentEvidenceRefs`, `changedAssets`, `coveredAssets`,
  `coverage` (`complete` / `partial` / `unavailable`) and nonempty `limitations`.
  Retain the original hypothesis and both evidence sets. Complete coverage must
  include the changed assets and original reproduction scope. These are agent
  coverage descriptions, not authoritative test outcomes or resolution verdicts.

Do not add confinement attestations, test-pass flags, mutation authority or raw
scanner replacements. Link native job/test results instead of claiming that
unavailable checks passed. `security.assess` checks current artifact read access,
scope and native identities, labels this material as agent interpretation and
retains its native scan/comparison even if interpretation is unavailable. Raw
scanner bytes remain unchanged. Native comparable absence remains distinct from
a verified fix; changed rules, partial scanning or reduced scope cannot prove one.
