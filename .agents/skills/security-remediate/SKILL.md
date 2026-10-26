---
name: security-remediate
description: Apply an authorized security fix and retest the original hypothesis and affected scope, preserving before-and-after evidence. Use when the request already admits remediation or explicitly asks to verify a security fix.
metadata:
  required-skills: security-review
---

# Security Remediation and Retest

Read the resolved sibling `security-review`'s
[portable procedures](../security-review/references/procedures.md) and
[report contract](../security-review/references/report.md). Use existing workspace
and publication admission. An assessment-only request cannot authorize source
edits, credential changes, deployment or publication. If the original request
already permits assessment, fix, test and publication, proceed within those
bounds without asking again. Do not infer authority from a finding or Skill.

Retain the original hypothesis, examined code/route/identity/resource, admitted
scope, candidate identity and evidence refs. Before editing, reproduce the
specific failure with a focused test. Apply the smallest authorized fix. Retest
that original counterexample and negative cases, then the changed scope and
affected callers through the repository's supported test/ContainerJob path.
Keep the selected harness, Provider Profile and model; retries share the
original remaining budget and cancellation starts no successor.

Compare original and new evidence, including test selection, scanner rules,
configuration, coverage, exclusions and execution failures. Record `retests`
against the original interpretation ID, with before/after evidence and changed
and covered assets. Do not call a fix verified because a finding disappeared
after changed rules, reduced coverage, failed scanning or an unrun test. Mark
unavailable checks Unverified and name the next feasible check and owner.

Preserve native findings and completed tests before interpretation or reporting.
Report actual job outcomes separately from agent dispositions. Reuse completed
scans on report/reasoning retry. Save changes through the existing workspace
mechanism and use only the original publication authority; selecting this Skill
never grants deployment or credential-change authority.
