# Data Model: Column Filter Popovers

## ColumnFilterState

Represents all applied filters for the Tasks List page.

Fields:

- `status`: `ValueFilter` for canonical lifecycle states.
- `repository`: `RepositoryFilter`.
- `targetRuntime`: `ValueFilter` for runtime identifiers.
- `targetSkill`: `ValueFilter` for skill identifiers or stable labels.
- `scheduledFor`: `DateFilter` with blank support.
- `createdAt`: `DateFilter` without blank support.
- `closedAt`: `DateFilter` with blank support.

Validation:

- Empty value arrays with `mode=include` mean no filter only when no blank flag is active.
- Empty value arrays with `mode=exclude` mean no filter only when no blank-exclusion flag is active.
- Date lower bounds must not be after upper bounds.
- Created date filters cannot request blank matching.

## ValueFilter

Fields:

- `mode`: `include` or `exclude`.
- `values`: ordered unique strings.
- `blank`: `include`, `exclude`, or omitted.

State transitions:

- Select all -> filter removed.
- Select subset from none -> include mode.
- Deselect one or more values from all -> exclude mode.
- Apply -> draft becomes applied state and pagination resets.
- Cancel/Escape/outside click -> draft is discarded.

## RepositoryFilter

Fields:

- `mode`: `include` or `exclude`.
- `values`: ordered unique repository values selected from the checklist.
- `exactText`: optional legacy exact repository string.
- `blank`: `include`, `exclude`, or omitted.

Validation:

- `exactText` is trimmed for query behavior.
- `values` and `exactText` can coexist only when represented to the operator as one Repository filter chip set.

## DateFilter

Fields:

- `from`: optional inclusive date or timestamp.
- `to`: optional inclusive date or timestamp.
- `blank`: `include`, `exclude`, or omitted for fields that support blanks.

Validation:

- `from` and `to` use canonical URL/API values.
- Scheduled and Finished may use blank filtering.
- Created does not support blank filtering.

## ActiveFilterChip

Fields:

- `field`: column filter field.
- `label`: product-facing label.
- `summary`: readable applied value, such as `not canceled`, `Codex CLI +1`, or `blank`.
- `removeAction`: clears only this field.
- `openAction`: opens the matching popover with applied state copied into draft state.

Validation:

- Labels render as text.
- Remove never clears unrelated column filters.
- Clear filters removes every chip and restores the default task-run view.
