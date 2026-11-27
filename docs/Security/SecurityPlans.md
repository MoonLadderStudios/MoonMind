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

## Planning from Create

The `security-plan` preset is the Security workspace's start action, and goals
that name a security plan select it. The operator picks a subject type
(`application`, or an [`endpoint`](EndpointAssessment.md#planning-an-endpoint)
assessed offline; network when its journey lands), a saved environment
through the assessment picker, and standards from the catalog. The run holds
no assessment scope, credential, egress grant or job reservation, and launches
no scanner, cloud, web or network job.

1. `security.plan` with `phase: context` checks the subject type and standards
   (an unavailable pack fails with its reason) and retains a
   `security-plan-context/v1`: the environment's target candidates, without
   credential, connection or network references, and every requirement with
   its allowed methods. The planner receives it as trusted step context.
2. The `security-review` Skill's [planning procedure](../../.agents/skills/security-review/references/plan.md)
   reads the repository read-only and writes a draft plan to
   `artifacts/security-plan/draft.json`. The agent runtime publishes those
   exact bytes as `securityPlanDraftRef`.
3. `security.plan` with `phase: retain` derives the trusted content, adds the
   questions the run cannot answer (such as the evidence period when the
   environment records none) and validates against the packs the context
   retained, so an update installed between the steps does not move them. A
   draft that changes the subject type, adds or drops a standard, alters a
   pack identity or makes an operator's requirement choice is rejected, as is
   any draft that fails the validation above. A valid plan is retained and
   sealed with its packs, project snapshot, context and draft, and its
   readable plan and gap list become the run's primary report. A rejected
   draft is kept, sealed under a `security.plan-diagnostics` record and a
   readable report of the diagnostics.

Nobody answers questions inside the run: missing inputs become open points and
gaps, and the operator corrects them by editing and rerunning. The saved
environment's evidence-processing policy is enforced before the planner
starts, with no fallback provider.

## Running a plan

A retained plan's Reports entry offers Run this plan, which opens Create with
the `security-plan-run` preset and the plan selected. API callers submit the
same preset; both expand it through the one preset compiler. Expansion reads
the retained `security.plan` artifact with the operator's authority and
derives, in order:

- each `repository-scan`, `image-assessment`, `endpoint-assessment`,
  `web-assessment` and `cloud-assessment` job as its existing technical
  preset's step with the
  plan's job input, so each keeps its readiness check, budget, admission and
  native evidence;
- one concluding `security.assess` over the plan's standards as
  `frameworkPacks`, bound by `planRun: {planRef, planIdentity, excluded}`.

Web, cloud and live endpoint jobs need the selected authorization's assessment
reference (`assessment_ref`), and cloud jobs, live endpoint hosts or test
identities the granted `credentialRef` (`launch_bindings`). A registry image job's bound
`credentialRef` becomes its `targetCredentialRef`; without one it reads the
registry anonymously. A job without them, a kind with no
launchable step, or one the operator lists in `excluded_jobs` is not launched
and named with its reason. The rules-of-engagement review adds "Not covered
by this authorization" listing those jobs and any scoped step the selected
authorization would refuse; the operator excludes it and reviews again. One
review covers the whole run. Its digest covers every derived step, so a
changed plan or authorization needs a fresh review, and an unchanged rerun or
schedule keeps the reviewed authority.

In a scoped run, repository, image and offline endpoint job steps run without
the assessment scope, as they do alone, because the container-job owner
refuses those scans inside one; a live endpoint step keeps it. They can only lose authority, and no step of a scoped run can fan
out.

Each job step writes a `security-plan-job/v1` outcome record that the agent
runtime publishes to the run. The conclusion treats records as claims: it
reads every cited report through the artifact owner, passes readable native
evidence to the retained-report owner, and retains a `security-plan-run/v1`
record sealed with the plan and the records. A report that owner rejects, or a
readable report beyond the 32 one conclusion evaluates, is named in its job's
reason and evidences nothing. The report's Plan against actual
section shows each planned job as `completed`, `partial`, `failed`,
`blocked_admission`, `blocked_readiness` or `not_run` with its reason, and
each requirement as `evidence_collected`, `inconclusive`, `not_assessed`,
`review`, `organisational`, `external_only` or `not_applicable`, naming who
can close external and organisational rows. Missing, malformed or uncollected
evidence leaves a requirement inconclusive or not assessed; zero findings
never mean met. A restarted job's latest record counts. Pass
`previous_assessment_ref` to compare with an earlier conclusion, which reports
a changed pack edition as an assessment-intelligence change. The run
authorizes no remediation; findings feed the existing finding-to-fix journey.

## Web/API methodology

The `methodology` section names the standard that declares procedures and
holds the threat model, `interfaces`, value-free `identities` (`id`,
`available`), `sourceRefs`, `apiContextRefs` and `allowedMethods`. That
standard's requirement rows carry their selected `procedures`. Pass
`methodology: {planRef, authorityRef}` to `security.assess`: the launch, not
the plan, names the readable rules of engagement. When job readiness came from
readiness results, also pass `securityPlan` for the same `planRef` with its
`readinessRef`; without it that readiness is `unverified` and the plan fails
validation. Coverage reports keep the
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
