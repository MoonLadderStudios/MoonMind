# Security plans

A `security-plan/v1` artifact records what an assessment will check before any
budget is spent or authority granted: which requirements of the selected
standards apply, how each would be evidenced, what MoonMind cannot evidence,
and the authority each planned job would need. It generalizes the web/API test
plan, which it replaces as the one owner of "what will be tested and how".

A plan grants nothing. It carries no credentials and cannot widen targets,
actions, credentials, network or budget. Work still launches under a saved
authorization and the existing rules-of-engagement review.

## Contents

| Section | Records |
| --- | --- |
| `subject` | `type` (`application`, `endpoint`, `network`), the saved project snapshot it was drafted from (`projectId`, `environmentId`, `sourceVersion`, `digest`), `candidateRef`, `systemBoundary`, `scope` (assets, exclusions, repository exceptions) and cited `sources` |
| `standards` | Each selected pack by `packId`, `edition`, `revision` and `sha256`, its `period` and `requirementChoices`. Installed packs omit `artifactRef` and carry their installed digest |
| `requirements` | One row per requirement of every standard: applicability, reason, evidence methods, planned `jobIds`, web/API `procedures` and `gaps` |
| `jobs` | Planned work as inputs to the typed job contracts, with derived `authority` and `readiness` |
| `methodology` | Optional web/API procedure context for the standard that declares procedures |
| `openPoints` | Assumptions, unknowns and operator-only questions |
| `regulatoryFacts` | Jurisdiction adoption, certification, laboratory or regulator approval |

## Derived and proposed content

Trusted code derives a standard's `title` and `distribution`; a requirement's
`title`, `allowedMethods`, `externalParty` and `evidenceRequestIds`; and each
job's `authority` and `readiness`. They come from the pack, its requirement
choice, the native job contracts and supplied readiness results. Restricted
packs leave titles out, so licensed text stays in the pack artifact.

A model or operator proposes the subject, applicability, reasons, methods,
evidence paths, job inputs and gaps. The subject, each requirement and each job
cite at least one source: a `file`, `document`, `environment_field` or
`operator_input`. A plan must contain the derived content exactly, so a
portable plan shows its allowed methods and authority without recomputation,
and a relabelled requirement or widened authority is detectable.

## Evidence methods

Plans use the control evaluator's methods plus `external_only`:

| Method | Allowed when the requirement declares |
| --- | --- |
| `deterministic_check` | Objectives, a `deterministic_check` evidence type or method, or a ZAP or application-access procedure |
| `agent_interpretation` | An `observation` method, or a review, investigation or observation procedure |
| `document_review` | Organisational evidence requests, documentary evidence types, an `enquiry` or `evidence` method, or a pack with profile inputs |
| `external_only` | An external claim, or a laboratory, QSA, auditor or regulator evidence type |

A requirement with `notAssessedReason` allows no method and carries a
`no_evidence_path` gap. Only `deterministic_check` rows may name jobs, so
external-only and organisational requirements are never presented as a scan.
Every applicable or undetermined row needs a planned evidence path or a gap.

## Jobs and authority

Plan job kinds are `repository-scan`, `image-assessment`, `cloud-assessment`,
`web-assessment` and `tls-assessment`, whose inputs are their typed contracts,
and `dns-assessment`, `service-inventory` and `application-access`, whose inputs
are a workload-profile selection plus the profile facts admission checks:
`egressPolicyRef`, and for application access the mode's `actionClass` and the
`targetScope`. Unknown kinds are refused.

Inputs never name `credentialRef`, `targetCredentialRef`, `assessmentRef`,
`credentials` or `credentialSlots`. Those are bound at launch from a saved
authorization; a plan names only the credential purpose. Contract checks use
placeholders for them and retain nothing.

Derived `authority` lists what an admitted assessment scope must name for the
job: `profileRefs`, `actionClasses`, `targets`, `credentialPurposes`,
`networkRefs`, `maxJobSeconds` and `maxRequestsPerSecond`. `maxJobSeconds` is
the timeout the native job builder renders, including its overhead, because
admission budgets the rendered job. Repository and image jobs are unscoped.
Targets must be candidates of the saved environment: origins from
`originCandidates`, artifacts from `artifactRefs`, registry images, DNS names
and native hosts from `assets` or origin hosts, and cloud accounts and regions
from `cloudAccountCandidates`.

## Readiness and gaps

Readiness comes from the existing readiness checks, never from the plan. A
`readinessRef` artifact maps planned job IDs to readiness results in their
existing `{status, checks, diagnostics}` shape. A job without one is
`unverified`: absent evidence is not readiness. Each requirement that cites a
blocked or unavailable job carries one gap per diagnostic, with `nextAction`
taken from the readiness diagnostics. Blocked feed checks report
`feed_cache_unavailable` and blocked scanner checks `scanner_image_unavailable`.
A methodology procedure naming an unavailable test identity carries an
`identity_unavailable` gap with the `credential_unavailable` action.

| Readiness code | Gap kind |
| --- | --- |
| `feed_cache_unavailable` | `feed_unavailable` |
| `scanner_image_unavailable`, `scanner_probe_unavailable`, `worker_unavailable` | `tool_unavailable` |
| `credential_*` | `identity_unavailable` |
| `invalid_assessment_input`, `assessment_authority_unavailable` | `missing_input` |

## Validation

Validation is deterministic and uses no model. It rejects a plan that alters
or drops derived content, omits, duplicates or relabels a requirement, selects
a method the pack does not allow, conflicts with an explicit requirement
choice, names an unknown job kind or an invalid job input, names a target
outside the saved environment's candidates, drops a saved exclusion, or
contains secret material. Diagnostics name the field, such as
`requirements[3].allowedMethods`, and never echo values or unknown field names.

Regulatory facts are never inferred. A fact stays `undetermined` unless it
names `recordRef` and cites that record as an `operator_input` or `document`
source. Derivation sets an uncited fact to `undetermined`; validation rejects
one that claims otherwise.

## Retaining a plan

Launch `security.assess` from the saved project the plan was drafted from, with
the plan's scope, no `credentialRefs` or `networkRefs`, and
`securityPlan: {planRef, readinessRef?}`. The tool validates the plan and
retains it as a `security.plan` artifact, sealed with the selected pack
artifacts (installed packs are retained as their bytes), the project snapshot,
the drafted plan and any readiness results. Outputs add `securityPlanRef`,
`securityPlanIdentity` and `securityPlanGaps`; the primary report adds the
readable plan and its gap list.

The identity is the content digest of the validated plan, so a changed pack
edition, requirement choice, subject asset or job input is a new plan, and an
unchanged retry reuses the retained one. A plan holds at most 16 standards,
1,024 requirements and 32 jobs in 8 MiB, the bounds of the combined control
plan and retained reports.

## Web/API methodology

The `methodology` section names the standard that declares procedures and
holds the threat model, `interfaces`, value-free `identities` (`id`,
`available`), `sourceRefs`, `apiContextRefs` and `allowedMethods`. That
standard's requirement rows carry their selected `procedures`. Pass
`methodology: {planRef, authorityRef}` to `security.assess`: the launch, not
the plan, names the readable rules of engagement. Coverage reports keep the
[web/API coverage contract](../../.agents/skills/security-review/references/report.md).

Retained `web-api-test-plan/v1` plans stay readable through
`security_plan.read_methodology_plan`, and their coverage reports stay
comparable baselines because both plan versions produce the same coverage
contract. Its consumers are saved `methodology.planRef` inputs, reruns and
schedules, and the procedure results and coverage reports that cite those
plans; remove it when none name a v1 plan.

## OSCAL

Plan rows map onto the existing [OSCAL assessment-plan](OscalExport.md)
procedures: the requirement ID is the control, `deterministic_check` is
`TEST`, `agent_interpretation` and `document_review` are `EXAMINE`, planned
jobs, procedures and evidence requests are steps, and job assets are subjects.
External-only requirements are not MoonMind activities. There is no system
security plan model; the export still imports an operator-supplied SSP.
