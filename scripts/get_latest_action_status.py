import subprocess
import requests
import os
import json

# --- Configuration ---
# Please replace these with your GitHub repository owner and repository name
# For example, if your repository is "https://github.com/octocat/Hello-World",
# OWNER would be "octocat" and REPO would be "Hello-World".
OWNER = "YOUR_OWNER"
REPO = "YOUR_REPO"
WORKFLOW_FILE_NAME = "pytest-unit-tests.yml"  # The name of your workflow file

# You might need a GitHub token for private repositories or to avoid rate limiting.
# Set this environment variable with your GitHub Personal Access Token.
# e.g., export GITHUB_TOKEN="your_token_here"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
# --- End Configuration ---

def get_current_git_branch():
    """Retrieves the current Git branch name."""
    try:
        branch_bytes = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.STDOUT
        )
        branch_name = branch_bytes.decode("utf-8").strip()
        if not branch_name or branch_name == "HEAD":
            print("Error: Could not determine current branch. Are you in a detached HEAD state?")
            return None
        return branch_name
    except subprocess.CalledProcessError as e:
        print(f"Error getting Git branch: {e.output.decode('utf-8').strip()}")
        return None
    except FileNotFoundError:
        print("Error: Git command not found. Is Git installed and in your PATH?")
        return None

def get_latest_action_run(branch_name):
    """
    Retrieves the latest GitHub Action run for the specified branch and workflow.
    """
    if OWNER == "YOUR_OWNER" or REPO == "YOUR_REPO":
        print("Error: Please configure OWNER and REPO variables in the script.")
        return None

    api_url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE_NAME}/runs"
    headers = {
        "Accept": "application/vnd.github.v3+json",
    }
    params = {
        "branch": branch_name,
        "per_page": 1,  # We only need the most recent run
        "status": "completed,in_progress,queued,requested,waiting,pending" # Consider all possible statuses
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    print(f"Fetching workflow runs for branch '{branch_name}' from {api_url} (params: {params})...")

    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)

        runs_data = response.json()

        if runs_data.get("total_count", 0) == 0 or not runs_data.get("workflow_runs"):
            print(f"No workflow runs found for '{WORKFLOW_FILE_NAME}' on branch '{branch_name}'.")
            return None

        latest_run = runs_data["workflow_runs"][0]
        return latest_run

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error fetching action runs: {e.response.status_code} {e.response.reason}")
        try:
            print(f"Response content: {e.response.json()}")
        except json.JSONDecodeError:
            print(f"Response content: {e.response.text}")
        if e.response.status_code == 401:
            print("Authentication failed. If this is a private repository, ensure GITHUB_TOKEN is set correctly.")
        elif e.response.status_code == 404:
            print(f"Repository or workflow not found. Check OWNER, REPO, and WORKFLOW_FILE_NAME: {OWNER}/{REPO}, {WORKFLOW_FILE_NAME}")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching action runs: {e}")
    return None

def main():
    print("Attempting to retrieve current Git branch...")
    current_branch = get_current_git_branch()

    if not current_branch:
        print("Exiting due to error in getting Git branch.")
        return

    print(f"Current Git branch: {current_branch}")

    print(f"\nAttempting to retrieve the latest GitHub Action run for workflow '{WORKFLOW_FILE_NAME}' on branch '{current_branch}'...")
    latest_run = get_latest_action_run(current_branch)

    if latest_run:
        run_id = latest_run.get("id")
        status = latest_run.get("status")
        conclusion = latest_run.get("conclusion")
        html_url = latest_run.get("html_url")
        created_at = latest_run.get("created_at")
        updated_at = latest_run.get("updated_at")

        print("\n--- Latest GitHub Action Run ---")
        print(f"  Workflow: {WORKFLOW_FILE_NAME}")
        print(f"  Branch:   {current_branch}")
        print(f"  Run ID:   {run_id}")
        print(f"  Status:   {status}")
        print(f"  Conclusion: {conclusion if conclusion else 'N/A (run may still be in progress)'}")
        print(f"  Created:  {created_at}")
        print(f"  Updated:  {updated_at}")
        print(f"  URL:      {html_url}")
        print("---------------------------------")
    else:
        print(f"Could not retrieve the latest action run for branch '{current_branch}'.")

if __name__ == "__main__":
    main()
