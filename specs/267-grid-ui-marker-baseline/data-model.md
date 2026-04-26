# Data Model: Grid UI Marker Baseline

This story does not introduce persistent storage. It does require checked-in baseline artifacts and diagnostic evidence in the target Tactics frontend project.

## Marker/Decal Mutation Call Site

- **Purpose**: Represents one direct invocation of a named marker/decal mutation API.
- **Required Fields**:
  - Source file path
  - Line or stable source location
  - Invoked API name
  - Producer role
  - Marker/decal type when knowable
  - Clear/spawn operation category
  - Notes for ambiguous context
- **Validation Rules**:
  - Every direct use of the named APIs in the target source tree must have one inventory entry.
  - Every entry must have exactly one producer role from the canonical role list.

## Producer Role

- **Allowed Values**:
  - selected movement
  - hover movement
  - attack targeting
  - ability preview
  - focus/selection
  - path/ghost path
  - phase clear
  - teardown clear
  - debug/demo utility
- **Validation Rules**:
  - Roles are mutually exclusive per call-site entry for this baseline.
  - Broad lifecycle clears must not be classified as producer-owned overlay clears.

## Diagnostic Event

- **Purpose**: Captures producer or renderer churn evidence for marker/decal activity.
- **Required Fields**:
  - source
  - marker type
  - reason
  - owner controller
  - tile count
  - operation type
- **Validation Rules**:
  - Events must allow maintainers to distinguish producer churn from renderer churn.
  - Events with zero tiles or unknown owner controller still need all required field keys.

## Movement Overlay Interference Scenario

- **Purpose**: Captures the known bug class where clearing one Movement producer can erase another Movement producer's overlay.
- **Required Fields**:
  - First Movement producer identity
  - Second Movement producer identity
  - Initial tile sets or equivalent marker state
  - Clear operation source
  - Observed remaining overlay state
- **Validation Rules**:
  - The automated test must prove the current interference behavior or its corrected baseline expectation.
  - The test must be traceable to `MM-525`.
