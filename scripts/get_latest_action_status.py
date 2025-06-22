import subprocess
import os
import json
import urllib.request
import urllib.error
import re
import zipfile
import io

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
    # Only 'branch' is needed. The API returns runs in reverse chronological order by default.
    params = {"branch": branch_name}
    query_string = urllib.parse.urlencode(params)
    api_url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE_NAME}/runs?{query_string}"

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

def get_and_display_failed_job_logs(jobs_url, owner, repo, token):
    """
    Fetches logs for the first failed job in a workflow run, searches for an error,
    and prints a contextual snippet of the log.
    """
    print(f"\nAttempting to fetch job details from: {jobs_url}")
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-GitHubActionStatusScript"
    }
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        req_jobs = urllib.request.Request(jobs_url, headers=headers)
        with urllib.request.urlopen(req_jobs, timeout=15) as response:
            jobs_data = json.loads(response.read().decode("utf-8"))

        failed_job = None
        if "jobs" in jobs_data:
            for job in jobs_data["jobs"]:
                if job.get("conclusion") == "failure":
                    failed_job = job
                    break # Process the first failed job found

        if not failed_job:
            print("No failed jobs found in this workflow run.")
            return

        job_id = failed_job.get("id")
        job_name = failed_job.get("name")
        print(f"Found failed job: '{job_name}' (ID: {job_id})")

        # Fetch job log (zip)
        log_url = f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
        print(f"Fetching logs for job ID {job_id} from {log_url}...")

        req_log = urllib.request.Request(log_url, headers=headers, method='GET') # Explicit GET for clarity

        log_content_lines = []

        with urllib.request.urlopen(req_log, timeout=30) as response_log:
            # urlopen handles redirects automatically for GET.
            # The response_log.url might be different if redirected (e.g. to S3 presigned URL)
            if response_log.status != 200:
                 print(f"Error: Expected status 200 for log download, got {response_log.status}. URL: {response_log.url}")
                 print("Headers:", response_log.getheaders())
                 # Try to read body for more info if it's an error Github might send
                 try:
                     error_body = response_log.read().decode('utf-8', errors='replace')
                     print("Response body:", error_body)
                 except Exception as e_read:
                     print(f"Could not read error response body: {e_read}")
                 return

            zip_data = io.BytesIO(response_log.read())

        print("Successfully downloaded log zip, processing...")
        with zipfile.ZipFile(zip_data, 'r') as zip_ref:
            log_text_parts = []
            for member_name in zip_ref.namelist():
                # Typically logs are .txt files, sometimes within job-name specific folders
                if member_name.endswith(".txt"): # Basic filter
                    try:
                        # Read file content as bytes from zip
                        file_bytes = zip_ref.read(member_name)
                        # Decode using utf-8, replacing errors
                        file_content = file_bytes.decode('utf-8', errors='replace')
                        log_text_parts.append(file_content)
                    except Exception as e_zip_read:
                        print(f"Error reading or decoding file {member_name} from zip: {e_zip_read}")

            if not log_text_parts:
                print(f"No .txt log files found in the zip for job '{job_name}'.")
                return

            full_log_text = "".join(log_text_parts)
            log_content_lines = full_log_text.splitlines()

        if not log_content_lines:
            print(f"Log for job '{job_name}' is empty.")
            return

        error_signatures = ["error:", "exception:", "traceback (most recent call last):", "failed", "failures", "build failed", "traceback:"]
        error_found = False
        first_error_line_index = -1

        for i, line in enumerate(log_content_lines):
            for sig in error_signatures:
                if sig in line.lower():
                    first_error_line_index = i
                    error_found = True
                    break
            if error_found:
                break

        print("\n---")
        if error_found:
            print(f"Error signature found in job '{job_name}' at line ~{first_error_line_index + 1}.")
            start_context = max(0, first_error_line_index - 10)
            end_context = min(len(log_content_lines), first_error_line_index + 500 + 1)

            if start_context > 0:
                print("... (previous lines hidden) ...")

            for i in range(start_context, end_context):
                print(log_content_lines[i])

            if end_context < len(log_content_lines):
                print("... (subsequent lines hidden) ...")
            print(f"--- End of contextual log for job: '{job_name}' ---")
        else:
            print(f"No specific error signature found in job '{job_name}'. Displaying last 30 lines:")
            start_default = max(0, len(log_content_lines) - 30)
            for i in range(start_default, len(log_content_lines)):
                print(log_content_lines[i])
            print(f"--- End of log for job: '{job_name}' (last 30 lines) ---")

    except urllib.error.HTTPError as e:
        print(f"HTTP Error fetching job details or logs: {e.code} {e.reason}")
        try:
            error_content = e.read().decode("utf-8", "replace")
            print(f"Response content: {error_content}")
        except Exception:
            print("Could not read error response body.")
    except urllib.error.URLError as e:
        print(f"URL Error fetching job details or logs: {e.reason}")
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON for job list: {e}")
    except zipfile.BadZipFile:
        print("Error: Downloaded log file is not a valid zip file or is corrupted.")
    except Exception as e:
        print(f"An unexpected error occurred while fetching/processing logs: {e}")


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

        if conclusion == 'failure' and latest_run.get('jobs_url'):
            # OWNER and REPO are global and should be set by get_latest_action_run or manually
            if OWNER and REPO:
                 get_and_display_failed_job_logs(latest_run['jobs_url'], OWNER, REPO, GITHUB_TOKEN)
            else:
                print("Cannot fetch logs: OWNER or REPO not determined.")
    else:
        print(f"Could not retrieve the latest action run for branch '{current_branch}'.")

if __name__ == "__main__":
    main()
