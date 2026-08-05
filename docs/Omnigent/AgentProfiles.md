# Omnigent Agent Profiles

Status: **Desired-State Design**  
Owners: MoonMind Engineering  
Issue: MoonLadderStudios/MoonMind#3517

## Implementation status

This document defines the target contract. The repository currently implements the
persistent profile/version/audit/usage records, lifecycle API, bounded upstream
projection, metadata and archive-content validation, an explicit snapshot-resolution
API, an operator-triggered bounded smoke-validation endpoint with lease cleanup and
secret-scanned diagnostics, and durable bootstrap materialization: the observed stock
`codex-native-ui` identity is seeded as an explicit active default bootstrap profile
after it passes structural readiness. `OMNIGENT_DEFAULT_AGENT_NAME` remains an optional
first-start override when durable state is absent; its use is recorded and durable
conflicts fail closed.

Dashboard profile management, readiness-aware workflow and schedule selectors,
transactional immutable snapshots, bounded bundle import, smoke validation, and
durable bootstrap authority are implemented. Checkpoint-branch and remediation
authoring preserve the originating immutable agent-profile and Provider Profile
selection so continuation cannot silently substitute runtime authority.

## Purpose and identities

An Omnigent agent profile is MoonMind-owned reusable configuration. It does not create a new runtime identity: dispatch remains `agentKind=external`, `agentId=omnigent`. An upstream agent id and version (or immutable bundle artifact and digest) identify provider content. A Provider Profile identifies credential and capacity materialization. An execution profile and launch policy identify host realization. These identities are separate and a display name is never identity.

Every execution resolves these references into a secret-free immutable snapshot before launch. The snapshot, rather than the mutable active profile pointer or current upstream inventory, remains the authority for retries, history, checkpoint branches, and evidence.

## Persistence and lifecycle

A stable `profileId` owns monotonically numbered immutable versions. Each version stores canonical JSON, a SHA-256 digest, parent/clone/supersedes lineage, upstream metadata at selection time, validation results, rollout metadata, actor, and timestamp. Editing always creates a version. Activation only moves the stable profile's active pointer. Disablement and deprecation block new selection without deleting versions or historical snapshots. Deletion is permitted only for an unused draft; referenced profiles and versions are retained.

The version document includes endpoint and bridge-mode refs; stable upstream or artifact-backed bundle identity; harness and capabilities; execution and allowed launch policies; credential-free Provider Profile compatibility requirements; model and effort settings; workspace mutation and capability constraints; Skills and tools; capture, retention, evidence, and RAG defaults and ceilings; continuation compatibility; publish default; and versioned policy ref.

Profiles never contain credentials, OAuth homes, registration secrets, Dockerfiles, host paths, volume names, host ids, or privileged launch settings.

## Discovery and launch safety

MoonMind synchronizes the stock `/v1/agents` built-in catalog through its authenticated bridge boundary into a bounded last-known projection keyed by endpoint plus stable upstream id and version. The stock catalog's session bindability is projected as the canonical `session.start` capability. MoonMind records harness, capabilities, health, provenance, compatibility, successful-sync time, attempt time, and redacted error state. An outage retains the prior snapshot but marks it stale. Missing or incompatible agents block new launches; historical snapshots remain readable.

The selector shown by workflow, schedule, checkpoint-branch, and remediation authoring lists active versions and fresh readiness diagnostics. Submission persists the profile id/version/digest, upstream snapshot, Provider Profile id, execution and policy refs, and effective model/workspace/capture/RAG values. Overrides are accepted only after policy validation.

## Bundle and validation boundary

Custom bundles are MoonMind artifacts with immutable content digest, content type, size, provenance, optional license, creator, schema version, and declared capabilities. Validation rejects traversal and forbidden paths, secrets, executable setup, Dockerfiles, privileged assumptions, and unsupported host capabilities. Publishing to an endpoint records its result without changing the profile-to-artifact relationship.

Smoke validation is an operator-triggered bounded preflight. It checks endpoint, exact source, capabilities, Provider Profile readiness/capacity, compiled policy, host mode, image/network/workspace constraints, Skills/tools, capture/RAG settings, and the strongest safe session-start check. Diagnostics are bounded and secret-scanned. Cancellation, failure, and timeout release only validation-owned leases and resources. A pass is readiness evidence, not a workflow-success guarantee.

## Bootstrap

The synchronized stock `codex-native-ui` identity is materialized as an explicit active bootstrap profile version after structural readiness passes. `OMNIGENT_DEFAULT_AGENT_NAME` may override that first-start selector only when durable profile state is absent in bootstrap/local development; its use is recorded. Durable state wins, and conflicts fail closed.
