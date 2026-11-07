# Security Tools and Assessment Workflows

[Passive scans](PassiveRepoScan.md), repository scanners, Trivy feeds, image/SBOM, cloud and web/API jobs, [game math](GameMathAssessment.md), [RNG](RngAssessment.md), [RGS](RgsAssessment.md), GitHub alerts, [identity lifecycle evidence](IdentityLifecycleEvidence.md), security Skills, and recurring `security.assess` comparisons are implemented. Conditional tools are not installed.

## Purpose and ownership

Assessments explain examined source, dependencies, images, cloud resources and applications, their coverage gaps, finding evidence and fix verification.

Omnigent owns reasoning. Security tools run separately on demand, never installed in the agent host.

| Responsibility | Owner |
| --- | --- |
| Assessment procedure, investigation, and remediation reasoning | Portable [Skills](../Steps/SkillSystem.md) executed by the existing agent |
| Durable steps, dependencies, budgets, retries, and cancellation | Existing Temporal workflow and execution owners |
| Admitted input, workload, credential, and network authority | Existing trusted admission, Container Job, Secrets, and restricted-egress boundaries |
| Tool execution, resource limits, logs, outputs, and cleanup | Existing [Container Jobs](../ManagedAgents/DockerBackendService.md) |
| Evidence retention and operator presentation | Existing [report artifacts](../Artifacts/ReportArtifacts.md) and [artifact presentation](../Artifacts/ArtifactPresentationContract.md) |

Adapters and Skills interpret scanner results; deterministic jobs do not acquire model capacity.

### Repository scanner jobs

`moonmind container repository-scan --scanner gitleaks` (or `opengrep`, `trivy`) submits one selected scanner without a JSON file. `--snapshot`, `--output`, `--database-cache` and `--database-digest` override inputs. Alternatively, `--admission scan.json` preserves explicit rules, exclusions, suppressions, scanners and resource bounds; admission files cannot be mixed with input flags. Each scanner produces separate evidence. Omitted scanners have not run.

Settings Operations and Create disclose configured images, rule coverage, cache aliases and matching refresh policies. Missing image/cache/refresh authority has distinct actionable diagnostics. This read-only view starts nothing: actual image availability, rules, feed freshness and connectivity remain **Unverified** until execution evidence exists.

Compose's `repository-security` image source defaults to `ghcr.io/moonladderstudios/repository-security:latest`, pull policy `always`. Override with `MOONMIND_REPOSITORY_SECURITY_IMAGE`, `MOONMIND_REPOSITORY_SECURITY_IMAGE_PULL_POLICY`, and `MOONMIND_REPOSITORY_SECURITY_REGISTRY_CREDENTIAL_REF`; an explicit `MOONMIND_CONTAINER_BACKEND_IMAGE_SOURCES` list replaces defaults. CLI `--image-source` selects another declared alias; `--image` selects an explicit scanner image. Actual image identity is provenance, not a launch compatibility gate.

Admission and the worker enforce one captured read-only source (`/inputs/source`, with Git metadata only for Gitleaks), a read-only image, no network, declared outputs, caches and timeout. UID/GID `1000:1000` writes only job-owned outputs and executable `/tmp`. `outputDirectory` is inside output storage, never source. The runner uses a bounded private copy, omits exclusions and outputs, ignores repository configuration/ignore files and disables inline suppressions unless admitted. Capture omissions remain limitations. Gitleaks separately scans available history and worktree, redacts secrets, records refs/shallow or missing history/untracked files, and applies exclusions to history pathspecs.

Omitted Opengrep `rules` select `/opt/moonmind-scanner-rules/default/manifest.json` from the running image. The maintained image ships three original Apache-2.0 rules: Python `eval` and shell-enabled subprocess calls, and JavaScript/TypeScript `eval`. This narrow baseline is not framework, cross-file or whole-application coverage. Its manifest records source, license, notice, languages and SHA-256 integrity; CI tests vulnerable/repaired fixtures. Custom images can provide their own manifest. Explicit `rules` (`id`, image-owned `path`, `sha256`, `license`, optional provenance) remain authoritative; empty Opengrep selections, remote paths and repository-local rules are refused. Missing rules and changed bytes produce distinct failed-prerequisite diagnostics. Gitleaks uses built-in rules unless one trusted configuration is selected.

Trivy defaults to `trivyScanners: ["vuln","misconfig"]`; `secret` requires `gitleaksOwnsSecrets: false`. Vulnerability scans default to read-only `scanner-db`. Omitted `databaseDigest` resolves its current immutable snapshot once, verifies the private copy and records the identity. Explicit cache/snapshot selections remain effective. Every database, Java-index, checks and version update is disabled during scanning.

#### Trivy feed snapshots

`moonmind container feed-refresh --egress-policy <authorized-policy>` prepares vulnerability/check feeds. Repeat `--component vulnerability --component java` to select different components, `--cache` to choose a writer, or `--max-age-hours` for freshness. `--archive art_...` instead imports a retained Trivy-cache tar offline. `--admission refresh.json` preserves full explicit choices. Downloads require existing deployment authority, never source credentials. Settings identifies matching policies and their workflow grants.

The source-free job declares empty `inputAttachments`, mounts no workspace, and carries no credentials/environment. For scheduling, use existing `container.run_job` with `spec: {"feedRefresh": {...}, "resources": {...}}`; the owner derives the workload without an agent. Fixed upstreams are `ghcr.io/aquasecurity/trivy-db:2`, and optionally `trivy-java-db:1` and `trivy-checks:1`. Egress permits only HTTPS 443 to `ghcr.io` and `pkg-containers.githubusercontent.com`. Trivy's database-only downloads and empty-directory checks scan acquire components. Because checks downloads silently fall back to embedded checks, refresh verifies the bundle exists. Offline import accepts bounded regular files/directories under component directories, counting implied parents toward bounds.

Compose declares writable `scanner-feeds` at `/opt/moonmind-scanner-feeds` and read-only `scanner-db` at `/opt/moonmind-scanner-db` on one owner-scoped volume. `MOONMIND_SCANNER_FEEDS_VOLUME_NAME` changes its base name; an explicit cache-source list replaces defaults. Both image mountpoints initialize UID 1000 ownership. Staging validates component layout, metadata versions (vulnerability DB 2, Java DB 1), required files and no links. Atomic publication creates `snapshots/<id>/`, then replaces `current.json`. The ID hashes sorted compact JSON `[relative_path, file_sha256]` pairs. Manifests retain upstream/artifact, schema, content digest, retrieval/update times and validation. Readers verify their private copy; concurrent writers serialize publication/retention. Retention keeps current plus two snapshots and renames retired snapshots before deletion, so racing readers see absence rather than partial data.

Freshness uses upstream `UpdatedAt` (checks: `DownloadedAt`), not image age or reacquisition. `feedMaxAgeHours` (refresh: `maxAgeHours`) defaults to 72. Stale feeds still scan with `feed_snapshot_stale`; absent, incomplete, corrupt or incompatible snapshots stop scanning distinctly. Java archives without their index report `java_db_unavailable`. Evidence distinguishes refreshed `snapshot` checks from `embedded` fallback. Failed refresh publishes nothing and preserves the previous snapshot: `refresh.json` records fallback identity, usability, missing components and freshness limitations.

Collection retains sanitized native JSON and a `repository-scanner/v1` summary with actual image/tool/rule/license/notice/feed provenance. Console output is withheld. Missing, malformed, oversized or unsanitizable reports keep coverage partial and withhold uncertain admission/file metadata. Valid findings survive aggregate failure; unavailable checks never become clean results.

Feed summary refs into `security.assess` using `retained-report` and explicit file scope. It authorizes native refs and publishes without an LLM. Opengrep coverage uses examined paths, excluding skipped/parser-failed inputs; Trivy needs target/analysis evidence. Exclusions and suppressions must match scope. Comparisons include effective rules/tool version, exclude output destinations, preserve findings outside examined scope and cap deduplicated findings at 4096 with partial coverage. Composed reports retain the summary reference and exact corpus identity.
#### Image, archive and SBOM jobs

`moonmind container image-assessment --admission image.json` submits one typed `imageAssessment` job, separate from `repositoryScan` so target-image coverage is requested and reported apart from source scanning. `operation` is `assess` (target `registry` or `archive`), `sbom-generate` (`registry`, `archive`, or `source`), or `sbom-reassess` (`sbom`). For example, `{"operation":"assess","target":"registry","imageReference":"registry.example.com/team/app:stable","platform":"linux/arm64","egressPolicyRef":"registry","databaseCacheRef":"scanner-db","databaseDigest":"<snapshot id>"}`. Matching requires a feed snapshot; generation reads none.

Trivy never consults a Docker, containerd, or Podman daemon: registry images use `--image-src remote`, archives (Docker save or OCI layout tar) use `--input`, and SBOMs (CycloneDX 1.2–1.7 or SPDX 2.2/2.3 JSON) use `trivy sbom`. Archives and SBOMs arrive as `inputArtifactRef` attachments with `networkMode: none`. Registry jobs need an egress policy whose destinations are all `registry-api` traffic and include the target registry. Admission and the worker reject any other mount, environment, network, or image. The scanner image is a deployment image source, so its pull authority stays separate from `targetCredentialRef`. That credential is authorized at admission by the same `MOONMIND_REGISTRY_CREDENTIAL_GRANTS` that govern private job images, matched against the target's registry, repository, tag/digest, and principal. The worker resolves it only under that decision and mounts it read-only as a Docker config in job-owned storage, never in argv, environment, the request, or evidence. It is removed when the container exits and on release. Output containing the credential is withheld (`credential_material_withheld`). Registry failures are classified from private stderr (`target_registry_auth_failed`, `target_registry_access_denied`, `target_not_found`, `target_platform_unavailable`) without retaining its text.

Evidence names the examined `subject`: a registry tag is recorded as requested and resolved to the manifest digest and OS/architecture Trivy used (`target_digest_unresolved`, `target_platform_unrecorded`, or `target_digest_mismatch` otherwise; digests describe evidence and are not compatibility gates). Archive, SBOM, and source subjects carry the digest of the captured bytes. Generated CycloneDX SBOMs are retained unmodified as the `sbom` output, and the `image-assessment/v1` summary records their artifact ref, digest, generator and version, package count, and packages without identifiers. The operating-system component and identifier-less applications that only group packages are context, not packages. Reassessing a retained SBOM against a newer snapshot reads only the SBOM. Malformed, unsupported, empty, or self-declared incomplete SBOMs and packages without identifiers are explicit limitations. Embedded signatures or attestation claims are recorded as unverified provenance, never as trusted attestation. `security.assess` accepts assess and reassess summaries as `image` coverage over the subject asset; each summary states that matching covers only the inventory and does not establish exploitability, malware absence, or runtime safety.

#### Web and API assessment jobs

`moonmind container web-assessment --admission web.json` submits one typed `webAssessment` job. It runs the `web-assessment` image source (Compose default `ghcr.io/zaproxy/zaproxy:stable`, override `MOONMIND_WEB_ASSESSMENT_IMAGE`) against one `origin`. The worker renders its Automation Framework plan from the admitted scope, identity, `apiDefinition`, `discovery`, and limits into the job's outputs. ZAP's network rate limiter applies `maxRequestsPerSecond` to every request, including crawling, imports, and attacks. ZAP contexts scope URLs but not methods, and crawlers, browsers, and scan rules choose their own, so the plan first installs a MoonMind HTTP sender script that redirects any request to the origin host outside the admitted routes, methods, and exclusions to an unresolvable host the egress proxy also refuses. A plan error, such as an image whose ZAP cannot load that script, stops the plan before it sends anything further. `baseline` crawls without posting forms, `api` imports the definition, and both check only passively. Only `active` runs the active scanner, at exactly `activeStrength`, after writing the passive reports so they survive a timeout or cancellation; its crawl posts forms only when a route admits POST.

Web jobs need an admitted scope: the mode's action class (`web-passive`, `web-api`, `web-active`), an egress policy of `live-target` destinations including the origin, and a `webOrigin` target whose `webRoutes` cover every route and method and whose `webExclusions` the job repeats. An identity's `credentialRef` needs an `application_auth` grant for the identity name and `web-assessment`. ZAP reads it from a read-only file, never the plan or argv. The verification route must show the identity's `loggedInMarker`, and hide it from an anonymous request for form logins. A bearer token goes on every request, so its marker must be identity-specific, such as the account name. The worker rejects definitions naming unadmitted servers, paths, or methods before ZAP imports them.

Collection redacts credentials, cookies, and login bodies from the native report and HAR, then publishes them with the plan and a `web-assessment/v1` summary whose findings carry route, method, identity, rule, phase, excerpts, and a reproduction reference. The identity secret leaves the job's mount when the container exits but stays worker-only until cleanup, so a retried publication redacts it again. Coverage lists tested, untested, unreachable, and inaccessible routes, denied requests, and the authentication outcome. ZAP exit codes 1 and 2 are plan errors and warnings; any other code is a job failure. Plan errors or warnings, an early stop, failures, missing or malformed output, unverified authentication, and unreachable or untested routes make the result `partial`. Verification answers count for the whole run, so a session lost during active testing is unverified authentication. `security.assess` reads the summary through `retained-report` as `web` coverage of the origin; a finding's absence is comparable only between runs that tested the same routes. Alerts never change the target or publish anything.

## Packaging and recommended tools

The maintained image contains **Opengrep, Gitleaks, Trivy, Git, CA certificates and the report runner**.

Cloud/application scanners use maintained upstream images. Jobs contain no agent/model credentials and have no Docker socket, nested Docker, host networking or privilege.

### Default capabilities

| Capability | Recommended tool and packaging | Intended use and qualification |
| --- | --- | --- |
| Source security analysis | [Opengrep](https://github.com/opengrep/opengrep), official release binaries in the curated image | Selected source-pattern and taint rules. Coverage depends on the language, framework, rules, and analysis mode; syntax support alone does not establish whole-application or cross-file coverage. |
| Exposed secrets | [Gitleaks](https://github.com/gitleaks/gitleaks), in the curated image | Separate working-tree and available Git-history scans, with full secret redaction. Gitleaks is in security-maintenance mode; keep the adapter replaceable and evaluate Betterleaks separately before adopting its interface. |
| Dependencies, basic IaC, images, and SBOMs | [Trivy](https://trivy.dev/docs/latest/), in the curated image | Select scanners explicitly. Gitleaks owns default repository secret scanning to avoid duplicate work. An ordinary findings report is not automatically an SBOM; inventory generation is an explicit operation. |
| Cloud posture | [Prowler CLI](https://hub.docker.com/r/prowlercloud/prowler), `prowlercloud/prowler:stable` | Typed `cloudAssessment` jobs with native JSON-OCSF evidence; see [cloud posture assessment jobs](#cloud-posture-assessment-jobs). Prowler's server, database, UI, and MCP stack are not needed. |
| Running web applications and APIs | [ZAP](https://www.zaproxy.org/docs/docker/about/), `ghcr.io/zaproxy/zaproxy:stable` | Typed `webAssessment` jobs running a rendered [Automation Framework](https://www.zaproxy.org/docs/automate/automation-framework/) plan; see [web and API assessment jobs](#web-and-api-assessment-jobs). |
| Contextual review | Existing Omnigent agent and security Skills | Architecture, trust boundaries, authorization and business logic, evidence validation, prioritization, proposed fixes, and retesting. Tool findings are inputs, not a prerequisite for investigation. |

Engine, rule, template, and Skill licenses are separate: ship only material whose terms permit the intended distribution and use, and deployment-provided rules keep their own terms. Adopting Opengrep does not remove restrictions on a separately obtained [Semgrep rule corpus](https://semgrep.dev/legal/rules-license).

### Conditional additions

These candidates extend coverage only when their trigger occurs; until then, gaps are reported as coverage limitations.

| Candidate | Disposition and trigger |
| --- | --- |
| [OSV-Scanner](https://google.github.io/osv-scanner/usage/), `ghcr.io/google/osv-scanner` | Deferred: Trivy covers manifests and lockfiles offline. Adopt for an ecosystem Trivy reports as unsupported, or for call analysis; build-assisted analysis needs separately admitted execution of untrusted build inputs. |
| [Checkov](https://www.checkov.io/), `bridgecrew/checkov` | Deferred: Trivy `misconfig` covers common IaC checks. Adopt for a framework or cross-resource graph policy Trivy lacks. |
| [Nuclei](https://docs.projectdiscovery.io/opensource/nuclei/running), `projectdiscovery/nuclei` | Deferred: ZAP covers admitted routes. Adopt for a known-issue template no ZAP rule covers. Template signatures establish origin, not safe impact. |
| [zizmor](https://docs.zizmor.sh/), `ghcr.io/zizmorcore/zizmor` | Deferred: the security-review Skill reviews CI/CD configuration directly. Adopt when GitHub Actions need repeatable scanning. |
| [Syft and Grype](https://oss.anchore.com/docs/), `anchore/syft` and `anchore/grype` | Not needed: image jobs generate and reassess SBOMs with Trivy. Reconsider to cross-check a disputed match. |
| [Cloudsplaining](https://github.com/salesforce/cloudsplaining) | Deferred: Prowler runs IAM checks. Adopt for an IAM-policy question they leave open. No upstream image contract is assumed. |
| [Kubescape](https://kubescape.io/docs/) | Deferred: Trivy covers Kubernetes manifests and admission has no cluster target. Adopt with one, as an ephemeral CLI rather than a cluster operator. |
| [Trail of Bits Skills](https://github.com/trailofbits/skills) | Deferred: the security Skills cover threat modeling, logic review, investigation, and retesting. Adapt a missing procedure with attribution and redistribution obligations. |
| [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) and [FIRST EPSS](https://www.first.org/epss/) | Deferred: triage uses local exposure evidence. Adopt as a feed snapshot when exploitation data would change decisions. |
| Deeper commercial analysis | Deferred until a licensed tool shows a measured gap; `security-observation/v1` results already compare through `retained-report`. |

[Strix](https://github.com/usestrix/strix), [HexStrike](https://github.com/0x4m4/hexstrike-ai), and similar agentic systems remain optional: their own agent loops would add a separate runtime contract, not just a model endpoint.

An ephemeral scan does not claim continuous protection.

#### Cloud posture assessment jobs

`moonmind container cloud-assessment --admission cloud.json` submits one typed `cloudAssessment` job (`provider`, `account`, `credentialRef`, `regions`, `checks` or `services`, `egressPolicyRef`, limits). Regions plus checks or services are limited to 110 so the rendered argv fits a job command. It runs the `cloud-posture` image source (Compose default `prowlercloud/prowler:stable`, override `MOONMIND_CLOUD_POSTURE_IMAGE`) through the image's entrypoint with an argv rendered from the admission. That argv has no remediation, export, notification, role, or profile options, and admission and the worker refuse any other entrypoint, argv, environment, mount, or network. AWS is the only provider because `aws-sts` is the only authorized cloud credential issuer; other providers are refused as unsupported.

The scope must admit the `cloud-posture` profile ref and action class, a `cloudAccount` target whose `cloudProvider` and `cloudRegions` cover the job, an egress policy of HTTPS `provider-api` hosts under `amazonaws.com`, and a `cloud_inventory` grant for the account and `cloud-posture`. Prowler reads credentials once, so one [workload credential](#credentials-and-networking) session, bounded by the grant's `maxSessionSeconds`, must cover the job timeout.

Collection publishes the native report and log tail, both redacted, with a `cloud-posture/v1` summary. It records the image that ran, Prowler's version, timestamps, findings with upstream identifiers, resource and region coverage, and checks with and without results. Prowler omits what it could not collect and only logs why, so log errors become value-free diagnostics; not-found answers are not errors. Any diagnostic, missing log, `MANUAL` result, unadmitted account, unfinished run, or unusable report makes coverage `partial`. A resource is compliant only when all its results pass. Outcomes are `findings`, `clean` (complete coverage only), `incomplete`, or `execution_failed`. A summary that contradicts its observations fails validation, so `security.assess` cannot promote it.

## Assessment authority and execution

An assessment starts from an admitted objective, targets (a repository snapshot or history, an image or SBOM, cloud accounts and regions, or application origins and routes), permitted operations, exclusions, credentials, network access, and budgets.

Skills can compose and adapt procedures within that authority. A prompt, scanner image, fixed entrypoint, or CLI convenience command is not an enforcement boundary; the trusted service and worker are.

### Workload profiles and admitted scope

A deployment declares approved invocations in `MOONMIND_CONTAINER_BACKEND_WORKLOAD_PROFILES`, a JSON array of profiles. Each profile binds an image source alias, an entrypoint, invocation modes (an `actionClass` plus an argv template), typed arguments (`string` with a required pattern, bounded `integer`, `enum`, or `input` naming a captured attachment path), fixed environment, credential slots, read-only rule/data caches, network mode and egress policy, declared outputs, and CPU/memory/timeout ceilings. Settings refuse a profile that names an undeclared image source or a writable cache, or uses bridge networking without an egress policy. An argv entry that references an omitted optional argument is dropped, so one mode admits approved variations instead of a frozen command each. Values replace placeholders inside argv entries and never pass through a shell.

A job selects `workloadProfile: {profileRef, mode, arguments, configurationExceptions, credentials}` with captured `inputAttachments` and resources; it never supplies the image, entrypoint, argv, environment, caches, workdir, network, or outputs. Admission renders the profile into the persisted spec. The worker renders it again at container creation and refuses content that differs, including after a profile edit or removal, rather than launching the old content or choosing another profile. Retries and exact replays pass the same checks.

Repository rules, plugins, ignore files, inline suppressions, and environment overrides are effective configuration. The deployment disables them in each mode's argv and fixed environment, and caller environment is refused. A request can name only a `configurationException` the profile defines as fixed argv text. The persisted spec records it, so it cannot select another mode, target, or credential.

The run's `assessmentScope` parameter is the operator's admission: `assessmentRef`, `profileRefs`, `actionClasses`, `targets` (workspace paths, artifact refs, web origins with routes, or cloud accounts with regions), `exclusions`, `configurationExceptions`, `networkRefs` (egress policies), `grants` (workload credentials), `maxJobs`, `maxJobSeconds`, and `evidenceIntent`. The operator submits it as `payload.assessmentScope` on a task-shaped `POST /api/executions` (or `initialParameters.assessmentScope`), and a malformed scope is rejected before any execution or schedule exists. Workflow and fan-out callers cannot set it. The run workflow copies it only into trusted step policy. Each launch boundary signs it into the session's container-job capability, and a malformed scope fails the launch. Step inputs, Skills, repository text, and model output cannot set or widen it. Child runs do not inherit the scope, so a scoped step gets no execution fan-out capability, and a step that requires fan-out fails at launch.

A scoped session's CLI, MCP, and replayed submissions must be profile, web, or cloud assessment jobs inside that scope. Generic image/entrypoint jobs, repository-scanner jobs, other assessments' refs, unadmitted profiles, action classes, exceptions, or networks are refused before any job record exists. So are inputs outside the targets and inputs at, inside, or containing an exclusion (a capture cannot omit a subtree). Scoped jobs cannot select pull credentials. `maxJobs` counts the owner's jobs for the assessment, and exact replays consume nothing. Unscoped sessions cannot claim an `assessmentRef`.

Tool configuration is part of the evidence: record effective rules, exclusions, ignore files, allowlists, severities, scanners, and environment options. Source, imported alerts, web pages, and scanner output are untrusted data; embedded instructions do not become workflow authority.

### Inputs, outputs, and caches

Container Job `inputAttachments` capture source snapshots and supplied artifacts once and mount them read-only, so later working-copy edits and retries cannot change what a job examines. Reports and temporary data use separate job-owned writable locations. Inputs are logical workspace or artifact references, never host paths. Native reports remain readable through retained artifact references after cleanup.

A bounded, source-free refresh job obtains Trivy's vulnerability databases and checks bundle without repository contents or assessment credentials. Opengrep rules ship in the image, and ZAP and Prowler use the rules in their own images. Scanners read an admitted read-only snapshot, so an untrusted scan cannot poison data later assessments use.

Record data source, resolved identity, acquisition and upstream update times, required components, and whether freshness was met. Missing or stale required data is reported as a coverage limitation or failure, never silently replaced by a clean result. Offline behavior covers every enabled component and remote dependency lookup, rather than only a single database-update switch; see [Trivy's air-gap guidance](https://trivy.dev/docs/latest/advanced/air-gap/).

### Credentials and networking

Workload credentials use the existing [Secrets System](SecretsSystem.md). A profile slot declares a `purpose` (`cloud_inventory` or `application_auth`), `env` or `file` delivery, and an issuer. A request names `{slot, credentialRef, account}`. Admission accepts it only if a scope grant `{ref, purpose, account, profileRefs, expiresAt, revision}` covers the job budget, and binds a value-free decision to that job, principal, assessment, and profile. An exact replay of admitted work is checked as of its admission, so a shortened or expired grant still returns the existing job; new jobs and start-failure restarts need a grant covering a fresh budget. The worker rechecks the decision and resolves the secret at the granted revision, so adoption by another job, rotation, revocation, and expiry fail closed (`credential_unresolved`, `credential_expired`). Values reach the container as read-only files under `/run/moonmind-credentials` or a worker-only `--env-file`, never argv. Docker keeps environment values in container configuration, readable by daemon administrators until removal, so prefer files. For cloud grants the worker exchanges the stored key for an STS session in the granted role, limited by read-only session policies (active access is not grantable), and renews it within the grant and budget only when it would expire before the budget ends; when the grant is too close to its end for another session, the current one serves until it expires. Issuance is recorded first, so a lost answer is counted and left to expire; failures keep the provider code (`credential_issuance_failed`). Every value delivered to the job, including sessions replaced by renewal or a restart, is redacted from logs and outputs, also on publication retries. Values leave the job's mount at container exit, stay worker-only for that redaction, and are deleted at cleanup; the stored credential is never rotated or deleted.

Model credentials, source-repository credentials, scanner-image pull credentials, private target-image credentials, cloud assessment credentials, and application authentication are distinct uses. Possession of one does not authorize another. Cloud identity and resource permissions enforce account/resource scope as well as the network policy; access to a cloud API hostname alone cannot confine a scan to an account.

Passive source analysis runs with `networkMode: none`, using prepared rule/data snapshots. Feed refresh, image acquisition, cloud APIs, and live-target assessment receive only their admitted destinations through the existing [restricted-egress owner](RestrictedEgress.md). Docker bridge mode alone is insufficient. Target enforcement addresses DNS resolution, redirects, alternate routes, private destinations, and control-plane/metadata access. Internal applications may be admitted explicitly without opening unrelated private networks.

Scanner jobs are bounded CLI executions, not persistent browser or proxy endpoints.

## Evidence, findings, and remediation

Each assessment retains sanitized native evidence (SARIF, JSON-OCSF, CycloneDX, SPDX, or the tool's own JSON, as each tool supports) through existing artifacts and report links. A small versioned summary references those records instead of creating a universal vulnerability database.

The summary separates four questions:

- **Execution:** did each requested operation run, terminate, and publish usable evidence? Tool-specific finding exit codes are different from crashes or failed invocation.
- **Coverage:** which inputs, languages, dependencies, cloud resources, routes, rules, and data snapshots were examined, skipped, unavailable, or incomplete?
- **Findings:** what did the tool or agent observe, with native identifiers, locations, evidence, confidence, and contextual priority?
- **Verification:** which hypotheses or fixes were checked, against what revision or resource state, and with what result?

A successful process can report findings and incomplete coverage together. Missing or truncated reports, unknown schemas, denied reads, stale feeds, unsupported inputs, and timeouts never become empty findings or a secure verdict. The human-readable report remains useful without an LLM and identifies unperformed checks as Unverified with the reason and a feasible next verification step.

Sanitization occurs before output reaches ordinary persisted logs, model-visible evidence, or published reports. Gitleaks uses explicit full redaction. Native tool masking misses surrounding source, headers, cookies, environment data, and error text, so tests exercise every retained output path with synthetic secret markers. If sanitization or bounded parsing fails, retain safe diagnostics and report incomplete evidence instead of publishing the unsafe content. Native evidence means the native format with required sanitization, not unrestricted raw secret capture.

Agent-created findings cite their source or runtime evidence and distinguish a hypothesis from a reproduced defect. An agent's cross-file conclusion rests on cited code, configuration, tests, or observed behavior, never on a scanner that did not perform that analysis.

Prioritization uses exposure, asset importance, affected execution paths, exploit prerequisites, compensating controls, and available fixes. Record exclusions, accepted risk, and their reason and review/expiry conditions without erasing the underlying finding.

Remediation and publication follow the operator's admitted request: a task authorized to fix, test, and publish needs no per-step approval. An assessment-only task (`publishMode: none`) gets no publication operations and its Skills make no source edits; its workspace is read-only only when the selected profile's `workspace.mutation` is. Cloud posture jobs are read-only.

Retesting links before/after inputs, findings, configuration, coverage, and regression checks. A disappearing finding is no evidence of a fix if a file, route, rule, account, or feed disappeared too; verified remediation needs comparable absence plus a passing `fix_retest` decision, or the source's own fixed state.

## Composable workflows

| Workflow | Composition and result |
| --- | --- |
| Repository assessment | Identify assets and trust boundaries; run applicable source, secret, dependency, and IaC checks as bounded jobs; perform targeted agent review; produce evidence, coverage, and prioritized recommendations. |
| Image and supply-chain assessment | Inspect an admitted image, supported image archive or OCI layout, or existing SBOM without a Docker socket; retain the actual image/inventory identity and generate an SBOM when requested; assess vulnerabilities and configuration with stated coverage. |
| Cloud assessment | Admit account, regions, and checks or services; run one `cloudAssessment` job; explain permission and resource gaps; relate findings to IaC or code when evidence supports the connection. |
| Web/API assessment | Admit origin, routes, mode, and identity; run one `webAssessment` job per identity; investigate selected findings from its retained route, identity, and coverage evidence. |
| Investigate and repair | Combine scanner or imported findings with direct review, produce an authorized fix, run focused regression/retest work, and publish through the normal delivery path when requested. |
| Recurring review and advisory response | Reuse existing schedules and saved Skill definitions; reassess retained authorized inputs or SBOMs with refreshed data; compare compatible evidence and explain scope or coverage changes. |

GitHub ingestion (the `github-alerts` tool of `security.assess`, described in the [passive scan document](PassiveRepoScan.md#imported-github-alerts)) preserves upstream alert IDs, scanner/source identity, repository and revision scope, timestamps, and source links. Permission-denied, rate-limited, unavailable, and disabled products are explicit coverage states. Repeated imports are identifiable as the same upstream observation. Secret-alert payloads receive the same sanitization boundary as local secret findings.

Comparisons use retained assessment artifacts and explicit baseline references, without a new findings service, scheduler, dashboard, or tenant model. They separate comparable absence, verified remediation, upstream closure, reduced coverage, and decisions needing review, and attribute differences to refreshed feed, rule, or tool identity rather than to a source change; see [evidence contracts](PassiveRepoScan.md#evidence-contracts).

## Maintenance and qualification

Repository maintainers own `.github/workflows/docker-publish-repository-security.yml`, which rebuilds current stable releases and the Python/glibc base weekly and on relevant main-branch changes; pull requests qualify both native architectures without publishing. Dispatch it for an immediate rebuild; optional `opengrep_version`, `gitleaks_version`, `trivy_version`, and `base_image` inputs select explicit releases and a compatible base. No tool-version equality gate constrains later updates or operator image choices.

Acquisition requires Opengrep's Cosign signature/certificate, Trivy's Sigstore bundle or legacy signature/certificate, and Gitleaks's HTTPS release checksums (Gitleaks publishes no release signatures). Release metadata falls back to anonymous access if GitHub refuses the optional CI token; failures report the upstream URL, status, and a redacted diagnostic. Missing assets or verification material, verification errors, or failed qualification stop publication. Each native build checks versions, findings and clean runs, the admitted runner, read-only permissions, offline Trivy data, SPDX SBOM generation, and isolated update promotion. One manifest update promotes `latest` only after both architectures pass, so failed runs leave the working default unchanged.

The image exposes `/usr/local/share/repository-security/inventory.json` and adjacent `notices/`. Its job-owned `/tmp` stays executable because the official Opengrep release extracts a child executable there. Actions retains each architecture's tool assets/signatures, corresponding Opengrep source, inventory/base identity, image digest, SBOM, and qualification evidence for 90 days. Scanner updates use this workflow without rebuilding the Omnigent host, whose baked MoonMind CLI follows the host deployment lifecycle.

`.github/workflows/upstream-assessment-images.yml` qualifies the default ZAP and Prowler `stable` images weekly, on relevant changes, and on dispatch with an optional operator image: the rendered ZAP plan against the local fixture application, and the rendered Prowler argv against moto. Production summaries must report findings for vulnerable fixtures and clean, complete results for repaired ones. It records the digests that ran and pins nothing, so a failure means the current `stable` release no longer fits the adapters.

Hermetic journeys use fake scanners, controlled transports, and local applications instead of paid providers or production accounts. They submit through the admitted CLI and MCP paths with vulnerable, repaired, and clean fixtures, and read native reports back after cleanup. Optional external qualification is recorded as Unverified when unavailable; observed defects remain blocking.

Measure useful coverage and investigation quality.
