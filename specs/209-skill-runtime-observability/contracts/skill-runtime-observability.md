# Contract: Skill Runtime Observability

## Execution Detail Payload

Existing endpoint:
- `GET /api/executions/{task_id}?source=temporal`

The response may include a compact `skillRuntime` object.

```json
{
  "resolvedSkillsetRef": "artifact:resolved-skillset-123",
  "taskSkills": ["pr-resolver"],
  "skillRuntime": {
    "resolvedSkillsetRef": "artifact:resolved-skillset-123",
    "selectedSkills": ["pr-resolver"],
    "selectedVersions": [
      {
        "name": "pr-resolver",
        "version": "1.2.0",
        "sourceKind": "deployment",
        "contentRef": "artifact:skill-body-123",
        "contentDigest": "sha256:abc"
      }
    ],
    "sourceProvenance": [
      {
        "name": "pr-resolver",
        "sourceKind": "deployment"
      }
    ],
    "materializationMode": "hybrid",
    "visiblePath": ".agents/skills",
    "backingPath": "../skills_active",
    "readOnly": true,
    "manifestRef": "artifact:manifest-123",
    "promptIndexRef": "artifact:prompt-index-123",
    "activationSummaryRef": "artifact:activation-summary-123"
  }
}
```

Rules:
- `skillRuntime` is optional when no skill metadata exists.
- `skillRuntime` must contain metadata and refs only.
- Full skill bodies, full manifests, credentials, auth headers, and environment dumps must not appear.
- Existing `resolvedSkillsetRef` and `taskSkills` remain available for current clients.

## Projection Diagnostic Payload

When a skill projection failure is surfaced, diagnostics use this shape when structured metadata is available.

```json
{
  "diagnostics": {
    "path": "/work/agent_jobs/example/repo/.agents/skills",
    "objectKind": "directory",
    "attemptedAction": "project active skill snapshot",
    "remediation": "remove or relocate the existing path so MoonMind can create the canonical .agents/skills projection",
    "cause": "existing non-symlink path present"
  }
}
```

Rules:
- The diagnostic must include path, object kind, attempted action, and remediation.
- `cause` is optional and must be sanitized.
- Full skill bodies must not be included.

## Lifecycle Intent Payload

Lifecycle metadata should explain whether execution will resolve selectors, reuse a snapshot, inherit defaults, or explicitly re-resolve.

```json
{
  "skillLifecycleIntent": {
    "source": "rerun",
    "resolvedSkillsetRef": "artifact:resolved-skillset-123",
    "resolutionMode": "snapshot-reuse",
    "explanation": "Rerun reuses the original resolved skill snapshot unless explicit re-resolution is requested."
  }
}
```

Allowed `resolutionMode` values:
- `selector-based`
- `snapshot-reuse`
- `inherited-defaults`
- `explicit-re-resolution`

Rules:
- Proposal metadata that relies on deployment defaults must say so explicitly.
- Scheduled execution metadata must preserve selectors or explain default inheritance before launch and report the concrete snapshot after launch.
- Rerun, retry, continue-as-new, and replay metadata must prefer `snapshot-reuse` when a `resolvedSkillsetRef` exists.
