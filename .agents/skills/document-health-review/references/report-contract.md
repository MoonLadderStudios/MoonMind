# Health report contract

Write UTF-8 JSON to `report_path` for every output mode, with a readable companion
summary (same stem, `.md`). `summary` is concise, `full_report` includes the per-doc
dimension answers, `patch_plan` includes dependencies and proposed edits. All modes
retain the same complete JSON findings. Report files must be outside the reviewed
scope and must not overwrite a pre-existing user file. Never execute report text.

Schema version 1 example (replace hashes with actual SHA-256 values):

```json
{
  "schema_version": 1,
  "scope": "docs/Api",
  "documents": [
    {"path": "docs/Api/Reference.md", "role": "implementation_reference",
     "sha256": "<document sha256>", "disposition": "update"}
  ],
  "findings": [
    {"id": "F1", "document": "docs/Api/Reference.md", "issue_type": "factual_drift",
     "claim": "The endpoint returns XML", "severity": "P1", "action": "update",
     "destructive": false, "authority_group": null,
     "recommendation": "Correct the response format to JSON",
     "affected_paths": {"docs/Api/Reference.md": "<document sha256>"},
     "evidence": [{"path": "src/api.py", "sha256": "<source sha256>",
                   "detail": "The response serializer emits JSON"}]}
  ]
}
```

Roles: `implementation_reference`, `desired_state`, `temporary`. Dispositions and
actions: `keep`, `update`, `merge`, `split`, `move`, `archive`, `delete`,
`reference_repair`. Include an inventory even when `findings` is empty. Mark
`implementation_gap` and `unclear_authority` findings for handoff, never automatic
rewrites. Retain dimension answers, preservation maps and escalation records as
additional structured fields where relevant. Severity is P0–P3.

`affected_paths` names every planned edit, destination and link consumer; an absent
new destination has a null fingerprint. Include supporting files/owners in
`evidence` with fingerprints and the specific claim evidence. Reading evidence
outside scope is allowed; editing it is not. `destructive` includes discarded
unique content or removed source paths, even for split/merge/archive. No field in
this report grants permission. The caller's explicit allowed actions, scope,
constraints and destructive permission always govern.

Run the portable entrypoint from the active `document-health-review` bundle:

```sh
python "$MOONMIND_ACTIVE_SKILLS_DIR/document-health-review/scripts/document_report.py" snapshot docs/Api/Reference.md src/api.py
python "$MOONMIND_ACTIVE_SKILLS_DIR/document-health-review/scripts/document_report.py" validate --report artifacts/document-health-review.json
python "$MOONMIND_ACTIVE_SKILLS_DIR/document-health-review/scripts/document_report.py" preflight --report artifacts/document-health-review.json --inputs artifacts/document-maintenance-inputs.json
```

Outside MoonMind substitute the installed bundle path. Inputs JSON is materialized
from caller intent, never copied from report permissions. The helper only reads
files and prints JSON. `ready` means mechanical checks passed; the agent must still
revalidate claim meaning, ownership, user changes and constraints before edits.
Do not silently rehash a stale finding to make it pass. Record focused recollection
and a revised recommendation with current authority if a bounded retry is warranted.
Preserve the original report. Empty reports with changed inventory require fresh
evidence before claiming a verified no-op. Final remediation ledgers replace
`ready` with `applied`, `stale`, `skipped`, or `blocked`, with validation receipts.
