# Repository Security Tool Pack (Bounded First Slice)

**Owners:** MoonMind Engineering (Container Job / executable-tool owners)
**Related:** [MoonMind Roadmap](../MoonMindRoadmap.md) Milestone 2 §§1–2, 5–6 and sequencing;
[Execution Tool and Plan Contracts](../Workflows/SkillAndPlanContracts.md);
[Repository Access and Workspace Design](../RepositoryAccessAndWorkspaceDesign.md);
[Workspace Locators](../Workflows/WorkspaceLocators.md);
[Secrets System](SecretsSystem.md);
[Restricted Egress](RestrictedEgress.md);
GitHub issues MoonLadderStudios/MoonMind #3930 (parent), #3965 (roadmap),
#809 (export safety), #3969 (evidence reporting), #2615 (source admission).
**Status:** Canonical desired state for issue #3970.

This document owns the concise declarative contract for the bounded first
implementation and qualification slice of repository security. It defines exact
first-slice scope, input authority, resource and network restrictions, and
output and evidence semantics, plus the remaining gaps that later slices own.
Run-local sequencing, rollout status, and per-PR disposition live in
`docs/tmp/` or gitignored handoffs, never here.

## 1. First-slice scope

The first slice is passive repository and artifact analysis only, which is
capability level 1 of the roadmap graduated levels. It covers one narrow
combination over an authorized immutable repository or artifact snapshot:

- dependency and inventory analysis with explicit coverage (supported
  lockfiles and languages declared per result), or secret-exposure triage with
  explicit coverage, or both from the same single portable tool pack;
- no assessment-target network access, no Docker socket, no repository hooks,
  install scripts, arbitrary plugins, or package builds merely to inventory
  dependencies.

The slice owns exactly one portable tool pack on the existing generic
Container Job and executable-tool substrate. MoonMind adds no new service, no
permanent container, no worker fleet, and no first-class runtime for the
scanner. The main MoonMind application image does not become a security-tool
distribution: scanner binaries live in separate immutable tool images and run
through real Container Job wiring.

Network-active assessment (roadmap levels 2–3) is outside this slice. It is
gated by its own explicit target authority and restricted-egress enforcement
evidence, not by this document.

## 2. Tool-pack contract

A repository-security tool pack declares the same contract shape as the
roadmap generic security tool-pack contract, bounded for this slice:

- immutable image and tool versions (exact digest-pinned image recorded at
  qualification; the required-CI path uses controlled fixture images and
  feeds, while exact-image and update qualification are recorded separately);
- portable entrypoint and invocation contract with fixed read-only arguments
  derived from the admitted scope (no shell hooks, no build steps);
- input and output schemas (native structured inventory and finding output is
  preserved; optional interoperable report exports and a human-readable
  summary travel through existing artifacts rather than forcing every output
  into one finding schema);
- required filesystem and process capabilities (read-only snapshot mount,
  no privilege escalation, no Docker socket);
- required network and egress policy (`none` for the scan itself; rule and
  feed acquisition is a separate bounded-egress concern below);
- secret slots and credential class (none required by the scan; the scan
  resolves no ambient credentials);
- CPU, memory, time, concurrency, and output limits (bounded; callers cannot
  override them upward);
- expected artifacts, evidence, and finding types;
- cancellation, failure, and cleanup behavior (scan failure preserves
  evidence and never authorizes mutation);
- license, SBOM, provenance, and conformance checks for the selected tool.

The concrete scanner is selected only after checking its maintained
interface, immutable image, licensing, output schema, update requirements,
and fit with the existing execution boundary. The reference implementation
(`moonmind/security/repo_security_toolpack.py`) encodes the contract,
provenance binding, completeness rules, and redaction semantics; it performs
no network access, no repository mutation, and no publication.

## 3. Input authority

The scan reads an already-authorized immutable snapshot through the same
contained workspace and import authority as other jobs (canonical workspace
locators). Every result binds the input revision or content digest that was
actually read.

Reading an authorized snapshot implies no further authority: it does not
permit contacting repository remotes, uploading source, resolving ambient
credentials, or widening the admitted scope. Where complete analysis would
require execution, credentials, or unresolved dependencies, the result
reports unsupported or incomplete rather than silently broadening authority.

Admitted scope (paths, exclusions, file and byte budgets) is recorded in the
result. Scope entries are normalized relative paths; absolute paths,
traversal segments, backslash escapes, and percent-encoding are rejected
before execution.

## 4. Resource and network restrictions

The scan executes read-only with no assessment-target network access, no
Docker socket, and bounded CPU, memory, time, and output. It runs no
repository hooks, install scripts, arbitrary plugins, or package builds to
inventory dependencies. Feed and rule acquisition is separated from target
scanning: it uses the existing approved acquisition, cache, and job
mechanisms with bounded egress and recorded immutable inputs.

A current tool image is not proof that vulnerability data is current. Source
files and findings are not sent to external services without explicit
policy. Offline or stale-feed behavior is stated honestly in the result: a
stale or missing feed makes the analysis incomplete, never clean.

## 5. Output and evidence semantics

Three outcomes stay distinct: job completion, analysis completeness, and
finding disposition.

- Job completion records what the container job did (terminal state, exit
  code, failure class, log and artifact references).
- Analysis completeness records what the analysis actually covered
  (`complete`, `incomplete`, `unsupported`, or `failed`). Parser failure,
  timeout, cancellation, skipped paths, unsupported languages or lockfiles,
  stale or missing data, and truncated output cannot become clean. Zero
  findings is meaningful only for the declared successful coverage with
  fresh feeds.
- Finding disposition records triage state. Every finding produced by this
  slice is `open`. A generated summary cannot upgrade a finding to verified
  or resolved. A fix is a separate authorized workflow with before-and-after
  scan evidence. This slice performs no automatic source mutation,
  publication, or finding suppression, and scan failure never authorizes
  mutation or discards useful evidence.

Every result binds input revision and content digest, tool and image
version (including the resolved image digest observed at execution),
configuration and rules digest, database and feed versions with freshness,
admitted scope, and the output contract version. Result artifacts retain
provenance and access controls after cleanup: findings and scanner output
are untrusted and confidential, paths are validated before rendering, raw
secret matches are suppressed from ordinary reports (restricted raw evidence
travels only through artifact policy), and partial outputs and diagnostics
survive failure.

## 6. Skill portability

The resolved Skill bundle owns the scan selection and interpretation rules
it declares. MoonMind supplies credentials, isolation, durable scheduling,
timeout and cancellation enforcement, logs, artifacts, approvals, and
validation of declared terminal contracts; it does not operate a parallel
scanner workflow engine with duplicated selection or interpretation logic.

Dependencies of this slice are the actually needed source, job, artifact,
and enforcement capabilities, not mandatory application installation,
command-line distribution, or demonstration surfaces owned elsewhere.

## 7. Remaining gaps owned by later slices

The following are explicitly out of the first slice and remain open:

- network-active assessment at graduated levels 2–3 with explicit target
  authority and restricted-egress enforcement evidence;
- exact-image pinning record and feed-update qualification cadence beyond
  the separately recorded qualification noted above;
- SBOM comparison, static application security testing beyond declared
  inventory coverage, infrastructure-as-code and deployment review,
  container and image analysis beyond the snapshot, authentication and
  authorization review, supply-chain and CI configuration review, threat
  modeling, and secure code review as full workflows;
- the remediation and verification loop (remediation planning, patch and
  publication workflows, before-and-after evidence, final disposition),
  which consumes this slice's evidence but is not part of it;
- finding correlation, hypothesis validation, and report coordination
  layers, which enter as Skills and presets on the same substrate when
  their own scope and authority are defined.

## 8. Sequencing

This bounded passive slice proceeds under the roadmap sequencing that
permits repository-security tool-pack, finding, and report work during
runtime convergence, while network-active security work remains gated on
proven target authorization and restricted-egress enforcement. This slice
introduces no blanket deferral of passive work and no new permanent
infrastructure.
