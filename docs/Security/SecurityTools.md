# Security Tools and Assessment Workflows

This describes MoonMind's security program, including capabilities that still need qualification. The [passive repository scan](PassiveRepoScan.md) and repository scanner adapters have implemented job/report paths. Repository tools use a separate image release workflow; offline database provisioning remains separate from image builds.

## Purpose and ownership

MoonMind helps its operator investigate and improve the security of source code, dependencies, build systems, container images, cloud resources, and running applications. An assessment explains what it examined, what it could not examine, the evidence behind each finding, and what verification supports a proposed fix. Scanner results and agent reasoning complement each other: direct source review, threat modeling, and business-logic analysis are useful even when a scanner reports nothing.

The existing Omnigent agent remains responsible for reasoning. Its selected harness and Provider Profile apply to security work just as they do to other tasks. Security utilities run on demand in separate containers; they do not require a second model provider, a new agent backend, or scanner installations in the Omnigent host.

| Responsibility | Owner |
| --- | --- |
| Assessment procedure, investigation, and remediation reasoning | Portable [Skills](../Steps/SkillSystem.md) executed by the existing agent |
| Durable steps, dependencies, budgets, retries, and cancellation | Existing Temporal workflow and execution owners |
| Admitted input, workload, credential, and network authority | Existing trusted admission, Container Job, Secrets, and restricted-egress boundaries |
| Tool execution, resource limits, logs, outputs, and cleanup | Existing [Container Jobs](../ManagedAgents/DockerBackendService.md) |
| Evidence retention and operator presentation | Existing [report artifacts](../Artifacts/ReportArtifacts.md) and [artifact presentation](../Artifacts/ArtifactPresentationContract.md) |

Adapters translate a tool's invocation and results into those interfaces. Scanner-specific interpretation belongs in adapters and Skills, rather than branches in generic Temporal orchestration. Deterministic scanner jobs do not acquire model capacity. Completed work survives a retry of an interrupted step; a new attempt consumes the existing logical step's budget.

### Repository scanner jobs

`moonmind container repository-scan --admission scan.json` submits exactly one Gitleaks, Opengrep, or Trivy job through the existing Container Job transport. For example, an explicit Gitleaks admission is:

```json
{"scanner":"gitleaks","inputPath":".","outputDirectory":"artifacts/gitleaks-run","maxFiles":4096,"maxInputBytes":134217728,"maxReportBytes":8388608,"timeoutSeconds":300}
```

Use a fresh output directory for each job. The worker refuses nonempty output directories so the writable mount cannot expose existing source files; retained output survives a refused launch. Compose registers `repository-security` for both API and worker with `ghcr.io/moonladderstudios/repository-security:latest` and pull policy `always`. `MOONMIND_REPOSITORY_SECURITY_IMAGE`, `MOONMIND_REPOSITORY_SECURITY_IMAGE_PULL_POLICY`, and `MOONMIND_REPOSITORY_SECURITY_REGISTRY_CREDENTIAL_REF` override that declaration; an explicit `MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES` list replaces all defaults. `--image-source` selects an alias; `--image` selects an explicit image containing the scanner, Git, Python, and installed runner. Rules and databases are prepared separately.

The typed `repositoryScan` request travels with the existing job spec. Admission and the worker enforce its invocation, read-only source and root filesystem, writable `/scan-output`, private `/tmp`, no network, output declarations, cache access, and timeout. Temporary storage covers `maxInputBytes`, the 2 GiB database allowance when selected, and 1 GiB for reports and tool scratch. Default job memory includes that tmpfs capacity plus 2 GiB for scanner processes, subject to deployment resource ceilings. The image uses UID/GID `1000:1000`, matching the Compose worker that creates output directories. The runner uses a bounded private copy, omits output storage and admitted `exclusions`, ignores repository configuration and ignore files, and disables inline suppressions unless `allowInlineSuppressions` was explicitly admitted. Gitleaks performs separate available-history and directory scans with full redaction; its evidence records refs, shallow/missing history, and available untracked files. Exclusions also apply to Git history pathspecs.

Opengrep requires `rules` entries containing `id`, an image-owned `path` under `/opt/moonmind-scanner-rules/`, `sha256`, and `license`. The base image ships no external rule corpus; include selected licensed rules in an operator-chosen derived image. Gitleaks may select one trusted configuration; otherwise it uses built-in rules through a private configuration. Rule bytes must match their admitted identities. Remote and repository-local rule paths are rejected. Trivy explicitly selects `trivyScanners` (default `vuln,misconfig`); adding `secret` requires `gitleaksOwnsSecrets: false`. Vulnerability scanning requires a read-only `databaseCacheRef` and `databaseDigest`: SHA-256 of sorted compact JSON pairs `[relative_path, file_sha256]` for the bounded database tree. The runner verifies and privately copies the admitted cache before offline scanning.

The existing artifact manifest retains sanitized native JSON and a `repository-scanner/v1` summary. Console content is withheld because arbitrary scanner diagnostics can contain source or secret metadata. Sanitization covers native records, execution inventories, and admission metadata before publication; sensitive paths use stable redacted identities. Missing, invalid, oversized, or unsanitizable native output leaves the secret inventory uncertain, so the summary withholds admission metadata (`admission: null`) and execution file lists, and manifest paths use opaque identities. Independently sanitized sibling evidence remains available. Recorded operation outcomes survive an aggregate job failure, which is disclosed separately as a limitation. Findings, execution, coverage, and verification remain separate: malformed records, parser errors, unsupported inputs, missing reports, timeouts, cancellation, and sanitization failures remain incomplete even when useful findings survive.

Pass summary artifact refs to `security.assess` using its existing `retained-report` tool and explicit file scope. The consumer authorizes native refs and publishes through the existing report bundle without an LLM. Opengrep coverage follows native examined paths, excluding skipped and parser-failed inputs; Trivy requires target and analysis evidence. Admission must match scope exclusions and suppression exceptions; withheld admission permits only partial coverage. Complete scans cover configured rules even without positive findings; comparison identity includes scanner version and effective configuration, excluding report destinations. Combined findings are deduplicated and capped at 4096 with explicit partial coverage; native evidence remains retained. Comparisons retain prior findings outside examined scope. Comparable absence does not verify a fix.

## Packaging and recommended tools

The recommended default is a small MoonMind-maintained repository-security image containing **Opengrep, Gitleaks, Trivy, Git, CA certificates, and only the helper runtime needed to invoke tools and prepare reports**. The same image can serve independent jobs with separate inputs, limits, results, and retries. A single invocation does not have to run every installed tool.

Cloud and application scanners use their maintained upstream images. Their larger Python, Java, and browser dependencies remain outside the repository toolbox and the Omnigent host. The image is a collection of executable utilities; it contains no agent loop or model credentials. Container execution goes through MoonMind's job service, without exposing a Docker socket, nested Docker, host networking, or privileged mode to the agent or scanner.

### Default capabilities

| Capability | Recommended tool and packaging | Intended use and qualification |
| --- | --- | --- |
| Source security analysis | [Opengrep](https://github.com/opengrep/opengrep), official release binaries in the curated image | Selected source-pattern and taint rules. Coverage depends on the language, framework, rules, and analysis mode; syntax support alone does not establish whole-application or cross-file coverage. |
| Exposed secrets | [Gitleaks](https://github.com/gitleaks/gitleaks), in the curated image | Separate working-tree and available Git-history scans, with full secret redaction. Gitleaks is in security-maintenance mode; keep the adapter replaceable and evaluate Betterleaks separately before adopting its interface. |
| Dependencies, basic IaC, images, and SBOMs | [Trivy](https://trivy.dev/docs/latest/), in the curated image | Select scanners explicitly. Gitleaks owns default repository secret scanning to avoid duplicate work. An ordinary findings report is not automatically an SBOM; inventory generation is an explicit operation. |
| Cloud posture | [Prowler CLI](https://hub.docker.com/r/prowlercloud/prowler), `prowlercloud/prowler:stable` | Scoped provider/account/region assessments with native JSON-OCSF evidence. The upstream `stable` tag follows stable releases; `latest` follows development. MoonMind does not need Prowler's server, database, UI, or MCP stack for CLI jobs. |
| Running web applications and APIs | [ZAP](https://www.zaproxy.org/docs/docker/about/), `ghcr.io/zaproxy/zaproxy:stable` | Bounded [Automation Framework](https://www.zaproxy.org/docs/automate/automation-framework/) plans for the admitted target, authentication, and passive or active mode. A self-contained job can run its browser and proxy subprocesses without a cross-turn service lease. |
| Contextual review | Existing Omnigent agent and security Skills | Architecture, trust boundaries, authorization and business logic, evidence validation, prioritization, proposed fixes, and retesting. Tool findings are inputs, not a prerequisite for investigation. |

Opengrep packaging uses its [official installer and release assets](https://github.com/opengrep/opengrep/blob/main/install.sh); the design does not assume a maintained upstream Docker image. Verify the selected platform/libc artifact and enable required upstream verification rather than silently accepting a missing verifier.

Engine, rule, template, and adapted Skill licenses are separate concerns. Ship only material whose terms permit the intended distribution and use. Deployment-provided rules retain their own terms; mounting them does not grant additional rights. In particular, adopting Opengrep does not remove restrictions on a separately obtained [Semgrep rule corpus](https://semgrep.dev/legal/rules-license).

### Conditional additions

These tools are alternatives or extensions for a demonstrated coverage gap, not requirements to install or run the entire catalog.

| Candidate | Selection reason |
| --- | --- |
| [OSV-Scanner](https://google.github.io/osv-scanner/usage/), `ghcr.io/google/osv-scanner` | Dependency or ecosystem coverage that improves on the selected Trivy profile. Source call analysis is language-specific; do not promise universal application reachability. Build-assisted analysis needs separately admitted execution of untrusted build inputs. |
| [Checkov](https://www.checkov.io/), `bridgecrew/checkov` | Deeper IaC policy or graph checks for the operator's actual infrastructure. |
| [Nuclei](https://docs.projectdiscovery.io/opensource/nuclei/running), `projectdiscovery/nuclei` | Selected known-issue templates against admitted application targets. Ordinary scans use the existing agent for interpretation; optional model-assisted features are not required. Template signatures establish origin, not safe impact. |
| [zizmor](https://docs.zizmor.sh/), `ghcr.io/zizmorcore/zizmor` | Focused CI/CD configuration review, especially GitHub Actions. |
| [Syft and Grype](https://oss.anchore.com/docs/), `anchore/syft` and `anchore/grype` | An alternative inventory and vulnerability workflow when independently retained SBOMs or coverage justify it. |
| [Cloudsplaining](https://github.com/salesforce/cloudsplaining) | Deeper AWS IAM-policy analysis, including analysis of an authorized exported snapshot. Verify packaging when selected; no upstream image contract is assumed. |
| [Kubescape](https://kubescape.io/docs/) | Kubernetes-specific manifest or cluster checks. Ephemeral CLI assessment is distinct from deploying a cluster operator or runtime agent. |
| [Trail of Bits Skills](https://github.com/trailofbits/skills) | Selected review procedures adapted to MoonMind's harness, authority, and evidence contracts, with attribution and applicable redistribution obligations. |
| Existing GitHub security findings | Import authorized CodeQL, dependency, and secret-alert evidence through the existing GitHub connection. Preserve the source's identity and limitations. Importing evidence neither enables a paid service nor claims MoonMind executed its scanner. |

[Strix](https://github.com/usestrix/strix), [HexStrike](https://github.com/0x4m4/hexstrike-ai), and similar agentic systems remain optional integrations. HexStrike can expose ordinary tools to an existing agent without inherently requiring another model. Systems with their own agent loop add a separate runtime contract; replacing that loop with Omnigent is not equivalent to configuring a model endpoint. Neither integration is required for these workflows.

Tool selection considers measurable coverage, output quality, maintenance, licensing, runtime cost, and overlap. A considered tool can be adopted, deferred until a named need appears, or omitted because existing coverage suffices. Continuous cloud and runtime detection remains complementary; an ephemeral scan does not claim continuous protection.

## Assessment authority and execution

An assessment starts from an admitted objective, target set, permitted operations, and limits. It can concern a repository snapshot, Git history, an image or SBOM, identified cloud accounts and regions, or application origins and routes. Scope also includes exclusions, credential references, permitted source/configuration inputs, output destinations, network access, and resource, request, and time budgets where applicable.

Skills can compose and adapt procedures within that authority. A prompt, scanner image, fixed entrypoint, or CLI convenience command is not an enforcement boundary. The trusted service and worker check the admitted workload through the CLI, MCP, and API paths, including generic job submission. A scoped assessment cannot escape its policy by changing the image, entrypoint, arguments, mounted input, configuration, credentials, or network selection. Ordinary development jobs retain their own explicitly admitted flexibility.

Tool configuration is part of the evidence. Record effective rules, exclusions, ignore files, allowlists, selected severities and scanners, and relevant environment options. Repository content cannot silently disable a required check or widen an operation. Source, imported alerts, web pages, and scanner output are untrusted data; embedded instructions do not become workflow authority.

### Inputs, outputs, and caches

Assessment source snapshots and supplied artifacts are mounted read-only. Reports and temporary working data use separate, bounded writable locations. Input/output resolution uses logical references owned by the existing workspace and artifact systems; it does not accept arbitrary host paths or mounts. Output collection rejects traversal and symlink escapes. Native reports remain accessible through retained artifact references after the container is removed.

An agent that is also authorized to edit code receives that authority through the normal working-copy path. The scanner still examines an identified snapshot so the result can be attributed to a specific input. Repository history coverage states which refs and depth were available; scanning a shallow checkout is not a full-history assessment.

Existing job caches and artifact retention provide reusable tool data. A bounded, source-free refresh job obtains approved vulnerability databases, rules, and templates. It has the required feed access but no repository contents or assessment credentials. Scanners consume an admitted read-only snapshot of trusted data; mutable per-assessment analysis caches are separate. An untrusted scan cannot poison a shared rule/database cache used by later assessments.

Record data source, resolved identity, acquisition time, upstream publication/update time when available, required components, and whether the assessment's freshness requirement was met. Acquisition time alone does not establish data freshness. Missing or stale required data is reported as a coverage limitation or failure, never silently replaced by a clean result. Offline behavior covers every enabled component and remote dependency lookup, rather than only a single database-update switch; see [Trivy's air-gap guidance](https://trivy.dev/docs/latest/advanced/air-gap/). Feed refresh does not require rebuilding the tool image or introducing a permanent feed service.

### Credentials and networking

Workload credentials use the existing [Secrets System](SecretsSystem.md). Trusted execution resolves admitted references at job start and delivers the minimum required environment or temporary credential files. Prefer short-lived sessions or roles when supported. Values do not pass through agent prompts, Temporal payloads, ordinary logs, or reports, and materialized credentials are removed on completion, failure, timeout, or cancellation.

Model credentials, source-repository credentials, scanner-image pull credentials, private target-image credentials, cloud assessment credentials, and application authentication are distinct uses. Possession of one does not authorize another. Cloud identity and resource permissions enforce account/resource scope as well as the network policy; access to a cloud API hostname alone cannot confine a scan to an account.

Passive source analysis runs with `networkMode: none`, using prepared rule/data snapshots. Feed refresh, image acquisition, cloud APIs, and live-target assessment receive only their admitted destinations through the existing [restricted-egress owner](RestrictedEgress.md). Docker bridge mode alone is insufficient. Target enforcement addresses DNS resolution, redirects, alternate routes, private destinations, and control-plane/metadata access. Internal applications may be admitted explicitly without opening unrelated private networks.

Cloud assessment defaults to inspection permissions appropriate to its selected checks. Unsupported providers, expired credentials, denied API calls, skipped regions, and absent services remain visible in coverage. Prowler cloud results use its native JSON-OCSF representation; its IaC SARIF support is not a general cloud-report contract.

Application assessment distinguishes passive analysis, crawling, authenticated coverage, and active tests. A [ZAP baseline scan](https://www.zaproxy.org/docs/docker/baseline-scan/) still sends crawl requests and needs authorized target access. ZAP's [Quick Start scan](https://www.zaproxy.org/docs/desktop/addons/quick-start/cmdline/) performs active testing. Authentication is verified against relevant protected routes, not inferred solely from reaching a login page. Active checks have explicit impact, traffic, timeout, and exclusion limits. API descriptions and imported targets are validated against the same scope.

Scanner jobs are bounded CLI executions. A persistent, cross-turn browser/proxy/tool endpoint is a separate capability only when a concrete workflow needs it; its readiness, scoped consumers, expiration, cancellation, and cleanup remain with existing runtime owners.

## Evidence, findings, and remediation

Each assessment retains a compact summary and sanitized native structured evidence through existing artifacts and report links. Use SARIF, JSON-OCSF, CycloneDX, or SPDX where the selected tool actually supports the relevant workload; no single export format is assumed for every scanner. A small versioned assessment summary references tool-native records instead of creating a universal vulnerability database.

The summary separates four questions:

- **Execution:** did each requested operation run, terminate, and publish usable evidence? Tool-specific finding exit codes are different from crashes or failed invocation.
- **Coverage:** which inputs, languages, dependencies, cloud resources, routes, rules, and data snapshots were examined, skipped, unavailable, or incomplete?
- **Findings:** what did the tool or agent observe, with native identifiers, locations, evidence, confidence, and contextual priority?
- **Verification:** which hypotheses or fixes were checked, against what revision or resource state, and with what result?

A successful process can report findings and incomplete coverage simultaneously. Missing or truncated reports, unknown schemas, denied reads, stale required feeds, unsupported inputs, and timeouts cannot become empty findings or a claim that the target is secure. The human-readable report remains useful without an LLM and identifies unperformed checks as Unverified with the reason and a feasible next verification step.

Sanitization occurs before output reaches ordinary persisted logs, model-visible evidence, or published reports. Gitleaks uses explicit full redaction. Native tool masking is useful but does not cover all surrounding source, request headers, cookies, environment data, or error text. Exercise every retained output path with synthetic secret markers. If sanitization or bounded parsing fails, retain safe diagnostics and report incomplete evidence instead of publishing the unsafe content. Native evidence means the native format with required sanitization, not unrestricted raw secret capture.

Agent-created findings identify the source or runtime evidence and distinguish a hypothesis from a reproduced defect. Review includes trust boundaries, authentication/authorization, sensitive data paths, cloud/IaC relationships, CI/CD permissions, and business logic where relevant. Cross-file reasoning may be supplied by the agent, but its conclusion is supported by cited code, configuration, tests, or observed behavior rather than attributed to a scanner that did not perform that analysis.

Prioritization uses exposure, asset importance, affected execution paths, exploit prerequisites, compensating controls, and available fixes. [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) and [FIRST EPSS](https://www.first.org/epss/) can enrich known-vulnerability triage; they do not replace local impact and applicability evidence. Record exclusions, accepted risk, and their reason and review/expiry conditions without erasing the underlying finding.

Remediation and publication follow the operator's admitted request. An assessment-only task does not implicitly permit source edits or cloud changes. A task already authorized to investigate, fix, test, and publish can compose those operations in one workflow without redundant approval at each step. Cloud auto-fix behavior is an explicit operation, not an incidental consequence of posture scanning.

Retesting links before/after inputs, findings, configuration, coverage, and relevant regression checks. A finding disappearing is not sufficient evidence of a fix if a file, route, rule, account, or feed disappeared too. Reports distinguish verified remediation from no-longer-observed findings, suppressed results, and unverified changes.

## Composable workflows

Skills express procedures that can be selected, combined, and refined using MoonMind's existing workflow, tool, artifact, and scheduling interfaces.

| Workflow | Composition and result |
| --- | --- |
| Repository assessment | Identify assets and trust boundaries; run applicable source, secret, dependency, and IaC checks as bounded jobs; perform targeted agent review; produce evidence, coverage, and prioritized recommendations. |
| Image and supply-chain assessment | Inspect an admitted image, supported image archive or OCI layout, or existing SBOM without a Docker socket; retain the actual image/inventory identity and generate an SBOM when requested; assess vulnerabilities and configuration with stated coverage. |
| Cloud assessment | Select provider/accounts/regions/checks; run Prowler with scoped credentials; explain permission and resource gaps; relate findings to IaC or code when evidence supports the connection. |
| Web/API assessment | Resolve target and mode; establish and verify allowed authentication; run a bounded ZAP plan; investigate selected findings and retain route/spec coverage. |
| Investigate and repair | Combine scanner or imported findings with direct review, produce an authorized fix, run focused regression/retest work, and publish through the normal delivery path when requested. |
| Recurring review and advisory response | Reuse existing schedules and saved Skill definitions; reassess retained authorized inputs or SBOMs with refreshed data; compare compatible evidence and explain scope or coverage changes. |

GitHub ingestion preserves upstream alert IDs, scanner/source identity, repository and revision scope, timestamps, and source links. Permission-denied, unavailable, and disabled products are explicit coverage states. Repeated imports are identifiable as the same upstream observation. Secret-alert payloads receive the same sanitization boundary as local secret findings.

Comparisons use retained assessment artifacts and explicit baseline references. They distinguish new, unchanged, resolved, accepted, and no-longer-observed findings without a new findings service, scheduler, dashboard, or tenant model. Updated feeds can change a vulnerability result even when source is unchanged; record that cause rather than attributing every difference to a code change.

## Maintenance and qualification

Repository maintainers own `.github/workflows/docker-publish-repository-security.yml`. It rebuilds current stable releases and the Python/glibc base weekly and on relevant main-branch changes. Pull requests qualify both native architectures without registry publication. For an immediate rebuild, dispatch that workflow; optional `opengrep_version`, `gitleaks_version`, `trivy_version`, and `base_image` inputs select explicit upstream releases and a compatible base. No tool-version equality gate constrains later updates or operator image choices.

Acquisition requires Opengrep's Cosign signature/certificate, Trivy's Sigstore bundle or legacy signature/certificate, and Gitleaks's HTTPS release checksums; Gitleaks currently publishes no release signatures. Public release metadata can be fetched anonymously if GitHub refuses the optional CI token; bounded failures identify the upstream URL, status, and redacted diagnostic. Missing architecture assets, absent verification material, verification errors, or failed qualification stop publication. Both native builds exercise independent version/finding/clean checks, the admitted runner, read-only input/output permissions, external offline Trivy data, SPDX SBOM generation, and isolated update promotion. Only successful architecture candidates reach GHCR; one manifest update promotes `latest` after both jobs pass. Failed runs retain diagnostics and leave the working default unchanged.

The image exposes `/usr/local/share/repository-security/inventory.json` and adjacent `notices/`; base/Python package notices remain in their normal locations. Its bounded private `/tmp` permits execution because the official Opengrep release extracts a child executable into its cache there; the root filesystem and source inputs remain read-only. Actions retains each architecture's tool assets/signatures, corresponding Opengrep source, resolved inventory/base identity, image digest, SBOM, and qualification evidence for 90 days. Rules and database identities remain separate. Scanner updates use this workflow without rebuilding the Omnigent host; changes to its baked MoonMind CLI follow the existing host deployment lifecycle.

Qualification exercises the actual admitted Skill/tool-to-job-to-artifact/report journey with known vulnerable, repaired, and clean fixtures. Relevant cases include history versus worktree coverage, unsupported inputs, configuration overrides, missing/stale feeds, denied credentials/network targets, secret redaction, malformed reports, cancellation, cleanup, and resuming after one scanner has already completed. Cloud/API transports and local applications provide deterministic fixtures without mandatory paid providers or live production accounts. Optional external qualification is recorded as Unverified when unavailable; observed defects remain blocking.

Measure useful coverage and investigation quality, including findings supported by direct review that default scanners miss. Do not require discovery of an unknown vulnerability, arbitrary detection percentages, or a broad cross-product benchmark to complete an implementation. Add tools when their demonstrated benefit warrants their maintenance cost.
