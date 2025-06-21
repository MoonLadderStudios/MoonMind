import subprocess
import os
import json
import urllib.request
import urllib.error
import re

# --- Configuration ---
# These will be attempted to be auto-detected from git remote.
# If auto-detection fails, you may need to set them manually here or ensure
# your git remote 'origin' is configured correctly.
OWNER = ""
REPO = ""
WORKFLOW_FILE_NAME = "pytest-unit-tests.yml"  # The name of your workflow file

# You might need a GitHub token for private repositories or to avoid rate limiting.
# Set this environment variable with your GitHub Personal Access Token.
# e.g., export GITHUB_TOKEN="your_token_here"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
# --- End Configuration ---

def get_owner_and_repo_from_git():
    """
    Tries to extract the OWNER and REPO from the 'origin' remote URL.
    Returns (owner, repo) or (None, None) if unable to determine.
    """
    try:
        remote_url_bytes = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"], stderr=subprocess.STDOUT
        )
        remote_url = remote_url_bytes.decode("utf-8").strip()

        # Regex to match both SSH and HTTPS URLs
        # SSH: git@github.com:OWNER/REPO.git
        # HTTPS: https://github.com/OWNER/REPO.git
        # Also handles cases without .git suffix
        match = re.search(r"(?:[:/])([^/]+)/([^/.]+)(?:\.git)?$", remote_url)

        if match:
            owner, repo = match.groups()
            print(f"Auto-detected OWNER: {owner}, REPO: {repo} from git remote 'origin'.")
            return owner, repo
        else:
            print(f"Could not parse OWNER and REPO from remote URL: {remote_url}")
            return None, None
    except subprocess.CalledProcessError:
        print("Could not find git remote 'origin'. OWNER and REPO must be set manually or git remote added.")
        return None, None
    except FileNotFoundError:
        print("Error: Git command not found. Is Git installed and in your PATH?")
        return None, None

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
    global OWNER, REPO # Allow modification if auto-detection works

    # Attempt to auto-detect owner and repo if not already set
    if not OWNER or not REPO:
        detected_owner, detected_repo = get_owner_and_repo_from_git()
        if detected_owner and detected_repo:
            OWNER = detected_owner
            REPO = detected_repo
        else:
            print("Error: OWNER and REPO are not set and could not be auto-detected from git remote 'origin'.")
            print("Please configure them manually in the script or ensure 'git remote -v' shows a valid origin.")
            return None

    if not OWNER or not REPO: # Check again after attempting auto-detection
        print("Error: OWNER and REPO are still not set. Exiting.")
        return None

    # Construct URL with query parameters
    query_params = urllib.parse.urlencode({
        "branch": branch_name,
        "per_page": 1,
        "status": "completed,in_progress,queued,requested,waiting,pending"
    })
    api_url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE_NAME}/runs?{query_params}"

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-GitHubActionStatusScript" # Good practice to set a User-Agent
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    print(f"Fetching workflow runs for branch '{branch_name}' from {api_url}...")

    req = urllib.request.Request(api_url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            response_body = response.read().decode("utf-8")
            runs_data = json.loads(response_body)

            if runs_data.get("total_count", 0) == 0 or not runs_data.get("workflow_runs"):
                print(f"No workflow runs found for '{WORKFLOW_FILE_NAME}' on branch '{branch_name}'.")
                return None

            latest_run = runs_data["workflow_runs"][0]
            return latest_run

    except urllib.error.HTTPError as e:
        print(f"HTTP Error fetching action runs: {e.code} {e.reason}")
        try:
            error_content = e.read().decode("utf-8")
            print(f"Response content: {json.loads(error_content)}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"Response content (raw): {e.read().decode('utf-8', 'replace')}")
        if e.code == 401:
            print("Authentication failed. If this is a private repository, ensure GITHUB_TOKEN is set correctly.")
        elif e.code == 404:
            print(f"Repository or workflow not found. Check OWNER, REPO, and WORKFLOW_FILE_NAME: {OWNER}/{REPO}, {WORKFLOW_FILE_NAME}")
    except urllib.error.URLError as e:
        print(f"URL Error fetching action runs: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON response: {e}")
    except Exception as e: # Catch any other unexpected errors
        print(f"An unexpected error occurred: {e}")
    return None

def main():
    # OWNER and REPO are now set globally, potentially by get_latest_action_run
    # So we don't need to pass them around as much.

    print("Attempting to retrieve current Git branch...")
    current_branch = get_current_git_branch()

    if not current_branch:
        print("Exiting due to error in getting Git branch.")
        return

    print(f"Current Git branch: {current_branch}")

    # Auto-detection of OWNER and REPO will happen inside get_latest_action_run if needed
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
