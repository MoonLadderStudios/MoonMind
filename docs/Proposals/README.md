# Deferred Proposals

This directory preserves product and architecture proposals for later consideration. Adding a proposal does not schedule implementation, enable a feature, establish runtime support, or supersede the current providing contracts.

## Proposals

| Proposal | Status | Implementation posture |
| --- | --- | --- |
| [Harness-first workflow authoring](HarnessFirstWorkflowAuthoringDesign.md) | Proposed | Deferred until the relevant Omnigent paths are ready and the product-contract change is explicitly adopted. |

## Authority and lifecycle

Proposals use the existing System / Feature Design View conventions in the [Documentation Architecture Standard](../DocumentationArchitecture.md). This directory is a discovery location, not a new runtime module, contract owner, implementation backlog, or rollout system.

Each proposal identifies its intent, rationale, current-contract differences, affected owners, readiness conditions, and observable acceptance requirements. Keep its status and deferred posture explicit. Declarative requirements describe the proposed outcome, not instructions to implement it immediately.

Current module contracts, security requirements, and qualified support policies continue to govern. A proposal that differs from them must name that difference rather than silently overriding it. A merge or a closed related issue is not an implementation or qualification result.

When a proposal is selected for implementation, reconcile the affected providing documents and existing issue owners in an explicit adoption change. Keep execution plans and live progress tracking in the existing issues or `docs/tmp/`, not in this directory. Promote settled behavior into its owning module documents and mark the proposal Superseded with successor links, or remove it after preserving useful rationale and references. Do not retain a competing permanent specification.
