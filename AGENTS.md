# Agent Instructions

## Retrieving GitHub Action Status

The script `tools/get_action_status.py` can be used to retrieve the latest GitHub Action workflow results for a particular branch with detailed error information.

**Usage:**

To run the script:
```bash
python tools/get_action_status.py [--branch <branch-name>]
```

--branch <branch-name> should be explicitly specified when git is in a detached HEAD state as branch auto-detection will no longer work.
