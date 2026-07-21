# Required Capabilities

Status: Desired State
Owners: MoonMind Engineering
Last Updated: 2026-06-17
Canonical for: `requiredCapabilities` declaration, derivation, normalization, readiness checks, and failure semantics
Related: `docs/Workflows/WorkflowArchitecture.md`, `docs/Steps/StepTypes.md`, `docs/Steps/SkillSystem.md`, `docs/Workflows/SkillAndPlanContracts.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`

---

## 1. Purpose

This document defines the declarative design of MoonMind **Required Capabilities**.

Required Capabilities are the normalized execution contract that says what the platform must be able to provide before a workflow, task, step, Tool, Skill, preset-derived step, or managed runtime session may start.

They answer questions such as:

1. Does the run need a repository checkout and `git`?
2. Does it need authenticated GitHub CLI / PR access through `gh`?
3. Does it need trusted Jira issue access or prefetched Jira artifacts?
4. Does it need a specific managed runtime such as `codex_cli`?
5. Does it need Docker, a sandbox, or another worker-side runtime feature?

Required Capabilities are declarative. They describe requirements. They do not grant authorization, contain credentials, execute tools, or replace policy checks.

---

## 2. Desired-state summary

MoonMind has one Required Capabilities contract:

```ts
type RequiredCapabilities = string[];
```

The normalized execution payload carries top-level `requiredCapabilities` after control-plane compilation and backend normalization. Those top-level capabilities are the execution-facing requirements consumed by worker routing, runtime preparation, integration readiness checks, and pre-launch blocking.

Capability declarations may originate from multiple authoring sources:

1. runtime mode,
2. publish mode,
3. workflow-level Skill selection,
4. step-level Skill selection,
5. Tool definitions or Tool steps,
6. preset metadata and preset-expanded child steps,
7. Skill metadata such as `metadata.required-capabilities`,
8. explicit advanced authoring fields.

The backend merges all sources into a single normalized, deduplicated, ordered top-level list before launch.

If any required capability cannot be satisfied under policy and authorization, the workflow or agent step must fail before runtime launch with a structured blocker.

---

## 3. Terminology

| Term | Meaning |
| --- | --- |
| **Required Capability** | A declarative token naming a runtime, integration, worker, or materialization requirement that must be satisfied before launch. |
| **Capability token** | A normalized string such as `git`, `gh`, `jira`, `docker`, or `codex_cli`. |
| **Capability source** | The authoring or compilation source that contributed a token, such as publish mode, a preset, a selected Skill, or a Tool step. |
| **Satisfied capability** | A required capability whose readiness check has succeeded for the requested target, policy, and user/deployment authorization. |
| **Unsatisfied capability** | A required capability that cannot be prepared or verified. Unsatisfied required capabilities block before launch. |
| **Readiness check** | A pre-launch platform check that proves the capability can be provided safely enough for the run to start. |
| **Materialization** | The act of preparing concrete runtime-visible state, such as a checkout, sanitized Jira artifacts, a `GH_TOKEN`, a Tool result artifact, or an active Skill bundle. |

The word **Capability** is also used in Step Type docs for selectable catalog items. That usage means "a selectable Tool, Skill, or Preset." `requiredCapabilities` are different: they are execution requirements derived from selected work, not user-facing Step Types.

---

## 4. Non-goals and boundaries

Required Capabilities are not:

1. a new GitHub/Jira access system;
2. an authorization grant;
3. a place to store secrets;
4. a Tool invocation;
5. a Skill or SkillSet selector;
6. a runtime command;
7. a replacement for provider profiles, approval policy, or user permissions;
8. the only validation a workflow needs.

A run that declares `jira` still needs valid Jira authorization or prefetched trusted Jira artifacts. A run that declares `gh` still needs a GitHub identity/token with appropriate repository permissions. A run that declares `git` still needs a repository target that policy allows the worker to prepare.

---

## 5. Contract surfaces

### 5.1 Top-level execution contract

The normalized execution payload carries the final, flattened list:

```json
{
  "requiredCapabilities": ["codex_cli", "git", "gh", "jira"],
  "task": {
    "runtime": { "mode": "codex_cli" },
    "publish": { "mode": "none" }
  }
}
```

This list is execution-facing and authoritative after backend normalization.

### 5.2 Workflow or task Skill field

A selected workflow-level Skill may declare explicit requirements:

```json
{
  "task": {
    "skill": {
      "id": "jira-pr-verify",
      "requiredCapabilities": ["jira", "git", "gh"]
    }
  }
}
```

Explicit authoring fields are additive with Skill metadata defaults.

### 5.3 Step Skill field

A step-level Skill may declare requirements:

```json
{
  "id": "verify-pr-against-jira",
  "type": "skill",
  "skill": {
    "id": "jira-pr-verify",
    "requiredCapabilities": ["jira", "git", "gh"]
  }
}
```

Step-level declarations contribute to the top-level execution payload because worker/runtimes must be prepared for the whole executable workflow.

### 5.4 Tool field

Tool definitions and Tool steps may contribute required worker or integration capabilities:

```json
{
  "id": "fetch-jira-issue",
  "type": "tool",
  "tool": {
    "id": "jira.get_issue",
    "requiredCapabilities": ["jira"]
  }
}
```

Tool capability requirements do not convert a Tool into a Skill. They describe what the execution environment must satisfy to run the Tool.

### 5.5 Preset metadata and expansion

Presets may declare capability requirements directly, and preset expansion may generate Tool or Skill steps that declare additional requirements.

Preset-derived capabilities must be compiled into the resolved workflow payload before runtime execution. Runtime workers must not depend on live preset catalog lookup to discover missing capabilities for an already submitted workflow.

### 5.6 Agent Skill metadata

Agent Skills may declare default capabilities in `SKILL.md` frontmatter:

```yaml
---
name: jira-pr-verify
description: Verify a GitHub pull request against a Jira issue and post a PR comment.
metadata:
  required-capabilities:
    - jira
    - git
    - gh
---
```

Deployment-stored Skill versions should preserve the same data in metadata as:

```json
{
  "required_capabilities": ["jira", "git", "gh"]
}
```

When a Skill requires supporting Skills through `metadata.required-skills`, the resolved Skill closure contributes the required capabilities of every selected and required Skill.

### 5.7 Runtime and publish derivation

The control plane may derive capabilities from runtime and publish choices:

1. runtime mode `codex_cli` contributes `codex_cli`;
2. repository-backed execution contributes `git`;
3. `publish.mode = "pr"` contributes `gh`;
4. container-enabled execution contributes `docker`.

These derived tokens use the same `requiredCapabilities` list as Skill, Tool, and preset declarations.

---

## 6. Normalization and merge rules

Required Capability normalization is deterministic.

Rules:

1. Capability tokens are strings.
2. Tokens are trimmed.
3. Tokens are normalized to lowercase unless a future capability registry explicitly marks a token as case-sensitive.
4. Blank tokens are invalid.
5. Non-string tokens are invalid.
6. Duplicate tokens are removed while preserving first-seen order.
7. The backend is authoritative. The frontend may derive and preview capabilities, but backend normalization must not trust the browser as the only source of required capabilities.
8. Capability sources are additive. A user or preset may add requirements, but the authoring UI must not silently remove requirements declared by selected Skills, Tools, runtime mode, or publish mode.
9. Explicit removal, if ever supported, must be policy-checked and visibly represented as an override with audit provenance.

Representative merge order:

1. incoming top-level `requiredCapabilities`,
2. runtime-mode-derived capabilities,
3. publish-mode-derived capabilities,
4. preset/template-derived capabilities,
5. selected Skill metadata capabilities,
6. explicit Skill field capabilities,
7. selected Tool capabilities,
8. container/runtime adapter capabilities.

The exact internal order may vary, but the resulting list must be stable, deduplicated, and explainable through source provenance.

---

## 7. Capability semantics

### 7.1 `git`

`git` means the platform must be able to prepare a repository workspace for the run.

Minimum readiness:

1. the target repository is known when required;
2. repository policy permits checkout or workspace reuse;
3. `git` is available to the runtime or to the workspace preparation layer;
4. branch/base/head state required by the workflow can be prepared;
5. publish mode constraints can be honored.

`git` does not imply permission to push unless the workflow's publish mode, Tool, or Skill requires and authorizes mutation.

### 7.2 `gh`

`gh` means the platform must be able to provide GitHub PR/repository operations through GitHub CLI or an equivalent runtime path.

Minimum readiness:

1. GitHub authentication is available under the selected policy;
2. target repository access can be verified when a repository is known;
3. target PR access can be verified when a PR is known;
4. mutation permissions are verified before mutation-capable work starts;
5. PR comment permission is verified before Skills that must post PR comments start.

A GitHub connector 404 is not, by itself, proof that runtime `gh` access is unavailable. Readiness should check the canonical runtime GitHub path for the selected execution mode.

### 7.3 `jira`

`jira` means the platform must provide trusted Jira issue/project access or materialized trusted Jira artifacts.

Minimum readiness:

1. trusted Jira Tool/connector access is available in the control plane, or required Jira artifacts are already attached to the run;
2. requested Jira issue keys or URLs are authorized when known;
3. sanitized Jira outputs can be materialized into artifacts or prepared workspace paths when a managed runtime needs issue content;
4. raw Atlassian credentials are not exposed in managed agent shells or Skill files.

For managed agent Skills, Jira readiness should prefer prefetched normalized artifacts or trusted control-plane Tool output over placing Jira credentials into the runtime environment.

### 7.4 `docker`

`docker` means the worker or runtime preparation layer can run containerized operations under policy.

Minimum readiness:

1. Docker or the approved container runtime is available;
2. container execution is allowed for the deployment, repository, and workflow;
3. resource and network policy are enforceable;
4. publishable workspace contents are protected from container-only side effects unless explicitly intended.

### 7.5 Runtime-mode capabilities

Runtime tokens such as `codex_cli` or `claude_code` mean the selected runtime adapter is available and compatible with the workflow's Skill, Tool, policy, and provider profile requirements.

Minimum readiness:

1. the adapter is enabled;
2. the target worker can launch it;
3. required model/provider profile constraints are satisfied;
4. runtime-specific materialization, prompt, Skill bundle, and artifact contracts can be honored.

### 7.6 Scoped future capabilities

Coarse names remain valid. Future scoped aliases may refine semantics:

| Coarse token | Possible scoped tokens |
| --- | --- |
| `jira` | `jira.read`, `jira.issue.read`, `jira.comment.write` |
| `gh` | `github.repo.read`, `github.pr.read`, `github.pr.comment`, `github.pr.merge` |
| `git` | `repo.read`, `repo.write`, `repo.branch.write` |

Scoped tokens must map into the same Required Capabilities contract. They must not introduce a parallel integration access model.

---

## 8. Readiness and blocking model

Required Capabilities are hard pre-launch requirements.

Readiness checks happen after:

1. preset expansion;
2. Skill resolution;
3. backend execution contract normalization;
4. repository/runtime/publish target resolution sufficient for the check.

Readiness checks happen before:

1. managed agent launch;
2. Tool step execution;
3. runtime session creation;
4. any action that assumes the capability is present.

If a required capability cannot be satisfied, the platform must block before launch with a structured diagnostic.

Representative diagnostic:

```json
{
  "status": "blocked",
  "capability": "jira",
  "source": ["skill:jira-pr-verify"],
  "target": {
    "issueKey": "KANDY-2558"
  },
  "check": "trusted_jira_readiness",
  "reason": "No trusted Jira connection or prefetched Jira artifact is available for this run.",
  "remediation": "Connect Jira or add a trusted Jira fetch/import step before the agent Skill step."
}
```

Diagnostics must be safe to show in logs and UI. They must not include raw tokens, auth headers, cookies, API keys, environment dumps, or private Jira/GitHub content beyond necessary identifiers.

---

## 9. Authorization and policy boundary

Declaring a capability is necessary but not sufficient.

The platform may satisfy a required capability only when all relevant gates pass:

1. deployment policy permits the capability;
2. repository/project policy permits the target;
3. the user or service identity has the required authorization;
4. approval/autonomy policy permits the action;
5. the worker or runtime adapter can prepare the capability safely;
6. secret handling policy can be honored.

A required capability never grants broader access than the user, deployment, or runtime policy allows. It only makes the requirement explicit so the platform can prepare or reject the run deterministically.

---

## 10. Skill interaction rules

Skill selection may require capabilities, but Skill identity and Tool access remain separate.

Rules:

1. Selecting a Skill contributes the Skill's declared `metadata.required-capabilities`.
2. Supporting Skills included through Skill resolution contribute their own declared capabilities.
3. A Skill-declared capability does not automatically allow every Tool in that integration.
4. Allowed Tools remain governed by Skill policy, runtime policy, user authorization, and approval rules.
5. Runtime adapters must not rediscover Skill files after launch to add new capabilities.
6. If an active Skill requires a capability that cannot be satisfied, Skill resolution or runtime preparation must fail before the agent starts.

Example: `jira-pr-verify` declaring `jira`, `git`, and `gh` means the platform must prepare Jira issue access/artifacts, repository state, and GitHub PR/comment access. It does not grant the Skill arbitrary Jira mutation Tools or GitHub merge permission.

---

## 11. Preset interaction rules

Presets are authoring-time composition objects. They may contribute required capabilities in two ways:

1. direct preset metadata;
2. generated Tool and Skill steps after expansion.

Rules:

1. Preset expansion must happen before execution by default.
2. Expanded steps carry their own capability declarations and provenance.
3. Preset-derived capability metadata is flattened into top-level `requiredCapabilities`.
4. Submitted execution payloads must not need live preset catalog lookup to discover required capabilities.
5. Rerun and detail views should preserve enough provenance to explain preset-derived capability requirements.

---

## 12. Replay, rerun, and Resume semantics

Required Capabilities participate in execution durability.

Rules:

1. The normalized `requiredCapabilities` list is part of the execution contract.
2. Exact rerun reuses the original normalized requirements unless explicit re-resolution is requested.
3. Edited full retry may recompute requirements from the edited workflow input.
4. Resume from failed step uses the original workflow input and preserved execution contract unless the Resume design explicitly permits re-resolution in the future.
5. A Resume attempt must not silently drop a capability required by the original failed step.
6. If a capability was satisfiable in the source run but is no longer satisfiable for Resume, the Resume attempt must fail explicitly before executing the failed step.

---

## 13. Observability

The dashboard and operator diagnostics should expose Required Capabilities as first-class execution context.

At minimum, detail/debug surfaces should be able to show:

1. normalized top-level `requiredCapabilities`;
2. source provenance for each token;
3. readiness status for each token;
4. materialized artifact refs or safe summaries where applicable;
5. pre-launch blockers and remediation guidance;
6. policy denials without leaking secrets;
7. whether a capability came from runtime mode, publish mode, preset, Skill, Tool, or explicit advanced authoring.

User-facing views may collapse details, but operator/debug views must make capability-driven launch failures explainable without parsing raw workflow history.

---

## 14. Security invariants

1. Required Capabilities do not contain secrets.
2. Skill files and preset files do not contain secrets.
3. Capability readiness diagnostics do not print environment variables or token values.
4. Runtime adapters do not broaden capabilities after launch.
5. Agents do not receive raw integration credentials merely because a capability is required.
6. Trusted control-plane Tool output and sanitized artifacts are preferred for private integration data needed by managed agents.
7. Capability metadata from repo or local Skill sources is untrusted input until parsed, normalized, validated, and policy-checked.
8. Capability satisfaction must be idempotent or safely retryable because workflow preparation may retry.

---

## 15. Core invariants

1. MoonMind has one Required Capabilities contract: normalized `requiredCapabilities`.
2. Required Capabilities are declarative launch requirements, not authorization grants.
3. Backend normalization is authoritative.
4. Frontend derivation is a preview and submit convenience, not the only enforcement layer.
5. Presets, Tools, Skills, runtime mode, publish mode, and advanced authoring all contribute to the same top-level list.
6. Direct Skill selection and preset-backed Skill selection must produce equivalent capability requirements when they select equivalent work.
7. Unsatisfied required capabilities block before launch.
8. Runtime adapters must not discover or add undeclared capabilities after launch.
9. Capability source provenance must be available for diagnostics.
10. Required Capabilities must remain safe to store in workflow histories, manifests, and logs.

---

## 16. Validation and test requirements

Minimum coverage:

1. top-level `requiredCapabilities` normalization trims, lowercases, deduplicates, and rejects invalid entries;
2. runtime mode contributes the selected runtime token;
3. PR publish mode contributes `gh`;
4. container-enabled execution contributes `docker`;
5. Tool-declared capabilities contribute to the normalized top-level list;
6. preset-declared and preset-expanded capabilities contribute to the normalized top-level list;
7. Skill `metadata.required-capabilities` contributes when a Skill is selected directly;
8. supporting Skills selected through required-Skill closure contribute their capabilities;
9. frontend omission of Skill metadata capabilities is corrected by backend normalization;
10. readiness blocks before launch when `jira`, `gh`, `git`, `docker`, or runtime-mode requirements cannot be satisfied;
11. readiness diagnostics include capability, source, check, reason, and remediation;
12. readiness diagnostics do not include secrets or raw environment dumps;
13. exact rerun preserves the original normalized requirements by default.

---

## 17. Documentation boundaries

Use this document for the Required Capabilities system.

Use related documents for adjacent systems:

- `docs/Workflows/WorkflowArchitecture.md` for the broader workflow control-plane contract.
- `docs/Steps/StepTypes.md` for Tool, Skill, and Preset Step Type taxonomy and selectable capability terminology.
- `docs/Steps/SkillSystem.md` for Skill resolution, required supporting Skills, and runtime Skill materialization.
- `docs/Workflows/SkillAndPlanContracts.md` for executable Tool and plan contracts.
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` for managed and external agent runtime execution.

---

## 18. Summary

Required Capabilities are MoonMind's declarative execution requirement layer.

They make hidden runtime and integration prerequisites explicit, merge them into one normalized execution contract, verify them before launch, and block early when the platform cannot safely satisfy them.

The correct design is not a separate GitHub/Jira access system. It is a single `requiredCapabilities` contract used consistently by presets, directly selected Skills, Tools, runtime mode, publish mode, backend normalization, runtime preparation, and operator diagnostics.
