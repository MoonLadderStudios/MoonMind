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

For a selected web/API methodology, retain a
[`security-plan/v1`](../../../../docs/Security/SecurityPlans.md) before testing
and pass `methodology: {planRef, authorityRef, resultsRef?, baselineRef?}` to
`security.assess`; `authorityRef` names the readable rules of engagement. The
plan selects the retained [procedure pack](web-api.json) as a standard. Its
`methodology` section names that standard and records `threatModel`,
`interfaces` (id, asset, protocol, locator), `identities` (id, available; no
credentials), `sourceRefs`, `apiContextRefs` and `allowedMethods`. Every pack
requirement row records applicability, reason and selected procedures with
assets, interface IDs, identity IDs, existing authorization `caseRefs`, and
their pre-execution `caseSpecDigest` from the existing application-access spec
digest function. Record unavailable context explicitly; no plan field grants
execution authority or changes a case oracle. Retained `web-api-test-plan/v1`
plans stay readable.

Retain `web-api-procedure-results/v1` with that exact `planRef` and `runs` naming
objective/procedure IDs, actual status, supporting evidence, evidence kind and
outcome. Incomplete runs need a reason. Native ZAP/application-access methods
name `nativeReportRef`, or `nativeReportRefs` to combine reports selected in the
same assessment's `reportRefs`; access runs also retain planned `caseRefs`.
Each report covers only its actual target, identity and endpoints; every selected
asset, identity and interface needs matching evidence. Anonymous ZAP scans cannot
complete authenticated selections.
Uncovered selections remain partial with a specific limitation and any confirmed
findings retained. Verified native coverage and authentication override optimistic
claims. Keep tool matches, reproductions, cited review
judgments and hypotheses distinct; a hypothesis cannot complete coverage.
Record limitations, review status, reviewer attribution when reviewed, reviewer
notes and follow-up tasks. Automated coverage does not imply an independent
professional engagement. Missing methods, identities or evidence remain visible;
comparable retests require the original cases, methods, oracle and coverage.
Declaring a reproduced fix also needs a passing current reproduction of the
corresponding method; scanner-only absence remains `absent_comparable`.
The primary report and bundle show overall selected coverage; native comparison
coverage stays separate. Public objective states remain readable after credential
redaction, and reporting retries refresh changed presentations while retaining
completed native work.
