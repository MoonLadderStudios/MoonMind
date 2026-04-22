# Contract: Report Artifact Contract

## Artifact Create

Report artifact creation uses the existing artifact create path.

When `link.link_type` is one of the supported `report.*` values:

- `metadata` must contain only allowed report metadata keys.
- metadata values must be compact and safe for control-plane display.
- unsupported `report.*` link types must fail validation.
- unsafe keys or values must fail validation before persistence.

## Artifact Link

Existing artifacts may be linked as report artifacts through the existing link path.

When `execution_ref.link_type` is one of the supported `report.*` values:

- the target artifact's current metadata must satisfy the report metadata contract.
- unsupported `report.*` link types must fail validation.
- the link is stored in the existing artifact link table.

## Latest Report Lookup

Latest report lookup uses existing execution artifact listing semantics:

```text
namespace + workflow_id + run_id + link_type = report.primary + latest_only
```

No report-specific latest pointer or table is added.

## Generic Output Compatibility

The following generic output link types remain valid non-report link types:

- `output.primary`
- `output.summary`
- `output.agent_result`

They are not aliases for report artifacts and do not require report metadata.
