# Quickstart: Temporal Schedule CRUD

## Prerequisites

- MoonMind repo checked out on branch `102-temporal-schedule-crud`
- Python 3.11+ with `temporalio` SDK installed

## Verify policy mapping

```python
from moonmind.workflows.temporal.schedule_mapping import (
    map_overlap_policy,
    map_catchup_window,
    make_schedule_id,
)
from uuid import UUID

# Overlap mapping
assert map_overlap_policy("skip").name == "SKIP"
assert map_overlap_policy("allow").name == "ALLOW_ALL"

# Catchup mapping
assert map_catchup_window("none").total_seconds() == 0
assert map_catchup_window("last").total_seconds() == 900  # 15 min

# Schedule ID
test_id = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
assert make_schedule_id(test_id) == "mm-schedule:a1b2c3d4-e5f6-7890-abcd-ef1234567890"

print("All quickstart checks passed.")
```

## Run unit tests

```bash
./tools/test_unit.sh
```
