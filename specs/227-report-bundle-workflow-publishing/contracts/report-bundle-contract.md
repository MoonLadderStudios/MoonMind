# Report Bundle Runtime Contract

## Activity Boundary

Report-producing activity code publishes bundles through an artifact-backed helper. The helper accepts:

- execution identity: `namespace`, `workflow_id`, `run_id`
- principal
- report metadata: `report_type`, `report_scope`, optional `sensitivity`, optional `counts`
- optional step metadata: `step_id`, `attempt`, `scope`
- component payloads for primary report, summary, structured result, and zero or more evidence artifacts

## Output Shape

The activity returns a compact mapping:

```json
{
  "report_bundle_v": 1,
  "primary_report_ref": {"artifact_ref_v": 1, "artifact_id": "art_..."},
  "summary_ref": {"artifact_ref_v": 1, "artifact_id": "art_..."},
  "structured_ref": {"artifact_ref_v": 1, "artifact_id": "art_..."},
  "evidence_refs": [
    {"artifact_ref_v": 1, "artifact_id": "art_..."}
  ],
  "report_type": "unit_test_report",
  "report_scope": "final",
  "sensitivity": "restricted",
  "counts": {"total": 3}
}
```

## Invariants

- `report_bundle_v` is exactly `1`.
- Workflow-facing results contain artifact refs and bounded metadata only.
- Report bodies, evidence blobs, logs, screenshots, transcripts, raw download URLs, and large finding details are invalid in workflow-facing results.
- Each component artifact is linked with namespace, workflow_id, run_id, link_type, and optional label.
- Final report bundles have exactly one canonical final marker using `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
- Evidence remains separately addressable through `evidence_refs`.
