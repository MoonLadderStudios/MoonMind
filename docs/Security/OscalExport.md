# OSCAL assessment exports

`security.assess` can optionally export its native framework and comparison
records as OSCAL **1.2.3 JSON**. Native assessments, evidence admission, reports
and artifact access remain authoritative. This adapter does not run assessments,
import OSCAL into execution, or establish regulatory approval.

Add `oscalExport` to an existing assessment request that selects `framework`:

```json
{
  "oscalExport": {
    "models": ["catalog", "profile"],
    "contextRef": "art_export_context"
  }
}
```

Omit `oscalExport` for the ordinary native report journey. `contextRef` is
optional for catalogs and profiles. Other models require the context below.
Select any of `catalog`, `profile`, `assessment-plan`, `assessment-results`, and
`poam`; exports include their preceding catalog/profile/plan dependencies so
references resolve. The output's `oscalExport.artifacts` maps model names to
restricted `report.export` artifacts. Existing authorized artifact reads and
downloads apply. Artifact references use `urn:moonmind:artifact:<artifact-id>`;
consumers resolve these through the authorized artifact owner, not public URLs.

## Supported subset

| Model | Mapping and limits |
| --- | --- |
| Catalog | Native framework requirements, statements, source links, applicability, and recorded coverage/exclusions. Control IDs start with `control.` because OSCAL names cannot begin with a digit. Original requirement and assessment rows remain in namespaced properties. |
| Profile | Explicit control selection over the exported catalog. Native non-applicable or excluded controls remain visible in the catalog rather than becoming satisfied controls. |
| Assessment plan | An explicitly identified system/SSP, component subjects, responsible parties, control objectives, methods, procedure steps and activities/tasks. The plan covers the supplied procedures, not an invented procedure for every selected control. |
| Assessment results | Native technical coverage and retained evidence as observations; explicit `findingTargets` supply objective judgments. Scanner signals alone do not become control findings. Native states, partial coverage and attributed triage decisions, including accepted risks, remain visible. |
| POA&M | Supplied risks, remediation status, actual action records and their planned/completed lifecycle, responsible parties, and available milestone dates. Entries link to their native finding and evidence. Comparison history alone cannot supply a remediation plan. |

Properties in `https://moonmind.dev/ns/assessment` preserve native fields as
strings or compact JSON. Consumers that ignore those extensions lose native
applicability, partial-coverage and triage detail; the document metadata and
output limitations disclose this. Framework evidence methods map enquiry to
`INTERVIEW` and document evidence/recorded observation to `EXAMINE`. Technical
report inspection uses `EXAMINE`; it does not claim a new test was executed.
Calendar-only evidence dates use midnight UTC in `collected`, with the original
recorded date retained in `native-evidence`. Historical finding states remain
explicit and do not assert that an old signal is currently present.

Control IDs retain ASCII letters, digits, dots and hyphens after the prefix;
other characters, including underscores, become `_x<Unicode-code-point>_`.
This preserves distinct identities without collisions: native `5.15` becomes
`control.5.15`, and `GLI-19:1.1` becomes `control.GLI-19_x3a_1.1`. Supplied SSP
control-implementation references must use these exported IDs for the assessed
controls. POA&M objective references explicitly link back to the exported plan.

## Reference context

Upload an `oscal-export-context/v1` JSON artifact through the existing artifact
owner and pass its ID as `contextRef`. The typed contract is
[`OscalContext`](../../moonmind/security/oscal_export.py). It accepts:

- `system`: `identifier` and `sspRef`. The SSP must identify that system and
  provide component and control-implementation references for the assessment.
  MoonMind checks the supplied reference graph; it does not certify the entire
  imported SSP or generate missing implementation statements.
- `subjects`: a map from each admitted assessment asset to its SSP component
  UUID. Context cannot add operational targets.
- `parties`: UUID, name and `type` (`person` or `organization`). These are
  descriptive parties, not MoonMind accounts or permission grants.
- `procedures`: `id`, native `controlId`, `objective`, `methods` (`EXAMINE`,
  `INTERVIEW`, `TEST`), nonempty `steps`, admitted `assets`, and
  `responsibleParty` UUID. Full objectives stay in prose and activity
  descriptions; task titles collapse whitespace to one line.
- `findingTargets`: a map from native finding ID to `procedureId`, explicit
  `status` (`satisfied` or `not-satisfied`) and recorded `reason`.
- `remediations`: `id`, `findingId`, `riskStatement`, `description`, explicit
  risk `status`, `responsibleParty`, readable `evidenceRefs`, and nonempty
  `actions` (`id`, `description`, `lifecycle`: `planned` or `completed`). Optional
  `milestones` contain `id`, `description` and timezone-aware `due` timestamps.
  Supported risk statuses are `open`, `investigating`, `remediating`,
  `deviation-requested`, `deviation-approved`, and `closed`. A supplied risk
  disposition does not overwrite the scanner state or native triage record.

UUIDs must be version 4 or 5. Imported context is untrusted data: unknown fields,
execution instructions, credentials and network grants are rejected. Descriptive
records cannot expand scope or authorize tools. Evidence-backed context must be
readable through the same artifact access checks as explicitly supplied context.

## Diagnostics and retention

Missing system/SSP context, unresolved relationships, unavailable evidence and
unsupported states return an `unsupported` export or a `partial` result with
model-specific diagnostics. Storage/publication failure reports `failed`.
Native reports and confirmed scan/comparison work remain usable. Retry the same
request to reuse saved native work and completed export documents.

Every published export has a verified dependency seal retaining the native
reports, context, SSP and cited evidence. Pinning an export retains those exact
bytes through ordinary lifecycle cleanup. A retained historical dependency is
not a new read grant: exports recheck current evidence access before publication.
Imported SSP references outside this supported subset are not traversed or
claimed to be complete. No broad OSCAL import or federal authorization workflow
is supported.

The checked-in official schemas and their original source URLs/digests live in
`tests/fixtures/security/oscal/1.2.3/`. Unit checks validate every model and compare
supported semantics; integration checks exercise the real Skill Activity,
restricted artifact reads, denied access, failure/retry and retention. External
auditor/tool acceptance remains **Unverified** unless actually exercised; it is
not a mandatory signoff.

References: [NIST assessment plans](https://pages.nist.gov/OSCAL/learn/concepts/layer/assessment/assessment-plan/),
[assessment results](https://pages.nist.gov/OSCAL/learn/concepts/layer/assessment/assessment-results/),
[POA&M](https://pages.nist.gov/OSCAL/learn/concepts/layer/assessment/poam/),
and [OSCAL 1.2.3 release](https://github.com/usnistgov/OSCAL/releases/tag/v1.2.3).
