# Agent Instructions

## Retrieving GitHub Action Results

The script `scripts/get_action_results.py` can be used to retrieve the latest GitHub Action workflow results for a particular branch with detailed error information.

**Usage:**

To run the script:
```bash
python scripts/get_action_results.py [--branch <branch-name>]
```

--branch <branch-name> should be explicitly specified when git is in a detached HEAD state as branch auto-detection will no longer work.
