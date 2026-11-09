# SOC 2 readiness

Select **SOC 2 Readiness** (`security-soc2-readiness`) from the existing security
preset catalog, or submit `security.assess` with `framework.packId` set to
`soc2-readiness`. This is a contextual preset, not a new Create wizard. Ordinary
Create remains Steps-first. Save populated inputs as a normal instance preset
for reuse; catalog expansion and the direct API use the same evaluator.

`tests/fixtures/security/soc2_readiness.json` is a runnable, entirely synthetic
assessment input with no external account or network requirement. It deliberately
has missing documentary evidence. The integration journey demonstrates catalog
expansion, retained evidence, a repaired technical finding with remaining process
gaps, immutable reassessment after a period change, and readable report export.

## Published source and permitted content

The selected edition is `TSC-2017-POF-2022_DC-2018-IG-2022`:

- [AICPA 2017 Trust Services Criteria with revised 2022 points of focus](https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022).
- [AICPA 2018 description criteria with revised 2022 implementation guidance](https://www.aicpa-cima.com/resources/download/get-description-criteria-for-your-organizations-soc-2-r-report).

Public AICPA sources were reviewed on November 9, 2026. Guidance revisions do
not replace the underlying published criteria. The pack inventories 61 TSC
identifiers and DC1–DC9 using short original topic summaries, not licensed
standard wording or a reproduction of points of focus. Common criteria remain
included for every selected category. Availability, confidentiality, processing
integrity and privacy criteria appear as unselected when their categories are
outside scope. DC9 is not applicable to Type 1.

[AICPA's October 2026 announcement](https://www.aicpa-cima.com/news/article/aicpa-proposes-update-of-soc-2-r-trust-services-criteria-to-enhance-the-relevance-usability-and-value-of-cpas-reports)
describes the proposed 2027 criteria as an exposure draft. They are not operative
in this pack and cannot be silently selected by an alias such as “latest.” A
future published edition requires an explicit implementation and sourced review.

Obtain any licensed source material through authorized AICPA access. The optional
`licensedSourceContext` records an operator's source/version and permitted-use
context; it does not change the chosen criteria or authorize redistribution.
Private licensed documents can be retained through the ordinary evidence import
owner and referenced by documentary requests. MoonMind does not bypass access
controls, retrieve paywalled text, or bundle licensed standards.

## Service inputs and scoped choices

`framework.systemInputs` contains:

- `entityName`, `systems`, `serviceBoundary` and `serviceCommitments` describing
  the assessed service and promised outcomes. Systems must stay within admitted
  `scope.assets`.
- `categories`: one or more of `security`, `availability`, `confidentiality`,
  `processing_integrity`, `privacy`.
- `assessmentType`: `type1` for a single date, or `type2` for a period.
- `subserviceOrganisations`: name, services, method (`inclusive` or `carve_out`),
  responsibilities and `evidenceRequirementIds`. Carve-out does not eliminate
  oversight or complementary responsibility gaps.
- `complementaryResponsibilities`: applicable customer/provider responsibilities.
  Set `boundaryReviewed` only after reviewing these records, including an
  explicit conclusion when no subservices or complementary duties apply.
- `requirementEvidence` and `requirementSamples`, described below.

`framework.period.start` and `.end` are ISO dates. Type 1 requires equal dates;
Type 2 requires start before end. `requirementChoices` is keyed by criterion,
for example `CC6.1`. It supports systems, period, explicit exclusion reason and
partner responsibility. Choices cannot widen system scope or the service period.
A narrower period stays visible as a gap against the overall selected period.
An exclusion is an attributed scope decision, not an assessor's conclusion.

Missing scope, documentary evidence and supplier responsibilities generate
specific `evidenceRequests`. The matrix retains every identifier, including
`not_applicable`, `unassessed`, and `evidence_recorded` rows. Evidence-recorded
means the supplied records meet these readiness checks, not that the criterion
has been independently examined.

## Documentary requests and populations

Use the shared organisational evidence contract from
[Framework Packs](FrameworkPacks.md). `requirementEvidence` maps each criterion
to requests with an owner, category, purpose (`design`, `adoption`, `occurrence`)
and immutable revisions. Each revision identifies its retained artifact, source,
collection/expiry dates, service population, period, sensitivity and attributed
review. Only the current revision is used. Superseded identities remain in the
report history without regaining access. Missing, denied, unreadable, expired or
unreviewed current records remain gaps.

All active criteria need accepted design evidence. Type 2 controls additionally
need accepted occurrence evidence and population/sample context. The same is
true for Type 2 incident and period-change disclosures (DC4 and DC9). A policy,
management assertion, agent draft or current configuration snapshot cannot
establish historical events or period operation.

For a criterion, `requirementSamples` records:

- `populationRequestId`: the occurrence request containing the population record;
- `populationSize`: the stated number of events or records;
- `samplingRationale`: the attributed reason for the selection;
- `selectedRecordIds`: identifiers within that retained population;
- `exceptions` and `managementResponse`, where applicable.

The current population revision's existing attributed `review` must also contain
`samplingContext`: `requirementId`, `populationArtifactRef`, `populationSize`,
`samplingRationale`, `selectedRecordIds` and `exceptions`. These must match the
criterion, current artifact and selected sampling facts exactly. Changing the
selection, zero-event assertion, rationale, exceptions or population revision
requires a matching review; old occurrence acceptance alone is insufficient.
The existing review still must cover the purpose, service population and period,
with valid attribution and dates. This records what was reviewed, not independent
validation of document contents or a new artifact access grant.

Record identifiers are operator-supplied, not generated audit samples. A
non-empty population needs selected records. A zero-event population still needs
an accepted period occurrence record supporting that conclusion; an empty list
alone is not evidence that no events occurred. This readiness evaluator does not
perform statistical sample selection or attest to population completeness.
Exceptions stay gaps even after a management response. Review the underlying
record, correct the control and retain a new reviewed revision for reassessment.

Technical `evidenceRefs` may hold ordinary scoped `FrameworkEvidence` records,
while native scanners/comparison findings remain in the same assessment report.
Technical references are labeled context and never satisfy documentary checks.
The ordinary scanner comparison can show a fixed technical issue while process
evidence remains missing. All reviews remain attributed evidence reviews rather
than independent assessor opinions.

## Reports, reassessment and export

The existing report owner publishes the requirements/evidence matrix, periods,
responsibilities, sample context, exceptions, limitations and actionable requests
in the private readable primary report, with a structured framework artifact.
Export that primary report through `evidenceExport.artifactRefs`; the existing
bounded export owner produces a redacted presentation copy. Exporting an
individual supporting document still follows that owner's supported formats and
access checks. No report or download grants additional artifact access.

Supply `previousAssessmentRef` to retain the earlier report and record changed
scope or period. Sufficiency is always reevaluated against the new request;
changing the period cannot carry forward a prior accepted result unexamined.
Readable current evidence and the previous report become sealed dependencies.
Missing evidence remains visible rather than being silently omitted.

Every output is **SOC 2 readiness**, not certification or an issued SOC 2
examination report. Actual CPA examination, independence decisions and
organisation-specific qualification remain **Unverified**. Synthetic automated
acceptance demonstrates implementation behavior, not those external activities.
