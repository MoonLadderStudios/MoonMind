# Agent Instructions

## Working with GitHub Actions

### Retrieving Action Status

The script `scripts/get_latest_action_status.py` can be used to retrieve the status of the latest GitHub Action workflow runs.

**Prerequisites:**

1.  **`GITHUB_TOKEN` Environment Variable:** This script requires a `GITHUB_TOKEN` environment variable to be set with a valid GitHub Personal Access Token. The token needs permissions to read repository data and action statuses (e.g., `repo` scope or `actions:read`). If you encounter authentication errors (like 401 or 403), please ensure this token is correctly set and has the necessary permissions.

**Usage:**

To run the script:
```bash
python scripts/get_latest_action_status.py [--branch <branch-name>]
```

**Specifying Branch:**

*   **`--branch <branch-name>` (Optional):** You can specify a branch name directly using this command-line argument. This will override any auto-detection.

    Example:
    ```bash
    python scripts/get_latest_action_status.py --branch feature/my-cool-feature
    ```

*   **Auto-detection (Default Behavior):** If the `--branch` argument is not provided, the script will:
    1.  Attempt to determine the current Git branch.
    2.  If the current branch cannot be determined (e.g., in a detached HEAD state, or if not in a Git repository), it will default to querying the `main` branch. You can change this default in the script's configuration section if needed.

**Troubleshooting:**

*   **401/403 Errors:** Check your `GITHUB_TOKEN`.
*   **404 Errors:** This might indicate the specified workflow file or branch does not exist, or there are no action runs for that workflow/branch. Ensure the `WORKFLOW_FILE_NAME` variable in the script (default: `build-and-test.yml`) is correct for this repository and that the target branch exists and has workflow runs. *(Agent note: The default branch for this repository needs to be determined and filled in here once a GITHUB_TOKEN is available. For now, this is a placeholder).*
