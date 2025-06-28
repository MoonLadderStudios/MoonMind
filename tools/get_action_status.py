import argparse
import io
import json
import os
import re
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
import zipfile

# --- Configuration ---
OWNER = "MoonLadderStudios"
REPO = "MoonMind"
WORKFLOW_FILE_NAME = "pytest-unit-tests.yml"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
LOG_CONTEXT_LINES = 5
# --- End Configuration ---


# --- Error Clustering Functions ---
def is_error(line: str) -> bool:
    error_signatures = [
        "error:",
        "error ",  # General error terms, case handled by .lower()
        "exception:",  # Exceptions
        "failed",  # General failure terms (singular)
        "failures",  # General failure terms (plural)
        "build failed",  # Specific build failure
        "traceback (most recent call last):",  # Python traceback start
        "traceback:",  # Generic traceback start
        "##[error]",  # GitHub Actions error logging command
    ]
    line_lower = line.lower()

    # Exclusions for common false positives
    if (
        "0 failures" in line_lower
        or "0 errors" in line_lower
        or "exit code 0" in line_lower
        or "completed successfully" in line_lower
        or "error summary" in line_lower
        or "no error" in line_lower
        or "error report" in line_lower
        and "generated" in line_lower
    ):  # e.g. "Error report generated"
        return False

    return any(sig in line_lower for sig in error_signatures)


def find_and_cluster_errors(
    log_lines: list[str], log_context_lines_count: int = 5
) -> list[dict]:
    error_cluster_threshold = (log_context_lines_count * 2) + 1
    error_indices = [i for i, line in enumerate(log_lines) if is_error(line)]
    if not error_indices:
        return []
    clusters = []
    if error_indices:
        current_cluster = [error_indices[0]]
        for i in range(1, len(error_indices)):
            if (error_indices[i] - error_indices[i - 1]) <= error_cluster_threshold:
                current_cluster.append(error_indices[i])
            else:
                clusters.append(current_cluster)
                current_cluster = [error_indices[i]]
        clusters.append(current_cluster)
    error_events = []
    for cluster_indices in clusters:
        first_error_idx = cluster_indices[0]
        last_error_idx = cluster_indices[-1]
        context_start = max(0, first_error_idx - log_context_lines_count)
        context_end = min(len(log_lines), last_error_idx + log_context_lines_count + 1)
        log_context_snippet = log_lines[context_start:context_end]
        primary_errors = []
        for error_line_index in cluster_indices:
            primary_errors.append(
                {
                    "log_line_number": error_line_index + 1,
                    "raw_message": log_lines[error_line_index],
                }
            )
        error_events.append(
            {"log_context": log_context_snippet, "errors": primary_errors}
        )
    return error_events


# --- End Error Clustering Functions ---


# --- API Request Helper ---
def make_api_request(
    url: str, headers: dict, timeout: int = 15, is_json: bool = True
) -> dict | str | None:
    """
    Makes an API request, handles common errors, and optionally decodes JSON.
    Prints errors to stderr. Returns decoded JSON, text content, or None on failure.
    """
    print(f"Making API request to: {url}", file=sys.stderr)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response_body = response.read()  # Read bytes first
            if is_json:
                return json.loads(response_body.decode("utf-8"))
            else:
                return response_body.decode("utf-8")  # Return as string
    except urllib.error.HTTPError as e:
        error_message = f"HTTP Error {e.code} {e.reason} for URL: {url}"
        try:
            error_details = e.read().decode("utf-8", "replace")
            error_message += f"\nResponse: {error_details}"
        except Exception as read_err:
            error_message += f"\nCould not read error response body: {read_err}"
        print(error_message, file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"URL Error for {url}: {e.reason}", file=sys.stderr)
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error for {url}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Unexpected error during API request to {url}: {e}", file=sys.stderr)
    return None


# --- End API Request Helper ---


def get_owner_and_repo_from_git():
    try:
        remote_url_bytes = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"], stderr=subprocess.STDOUT
        )
        remote_url = remote_url_bytes.decode("utf-8").strip()
        match = re.search(r"(?:[:/])([^/]+)/([^/.]+)(?:\.git)?$", remote_url)
        if match:
            owner, repo = match.groups()
            print(
                f"Auto-detected OWNER: {owner}, REPO: {repo} from git remote 'origin'.",
                file=sys.stderr,
            )
            return owner, repo
        else:
            print(
                f"Could not parse OWNER and REPO from remote URL: {remote_url}",
                file=sys.stderr,
            )
            return None, None
    except subprocess.CalledProcessError:
        print(
            "Could not find git remote 'origin'. OWNER and REPO must be set manually or git remote added.",
            file=sys.stderr,
        )
        return None, None
    except FileNotFoundError:
        print(
            "Error: Git command not found. Is Git installed and in your PATH?",
            file=sys.stderr,
        )
        return None, None


def get_current_git_commit_sha():
    try:
        sha_bytes = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.STDOUT
        )
        sha = sha_bytes.decode("utf-8").strip()
        if not sha:
            print("Error: Could not determine current commit SHA.", file=sys.stderr)
            return None
        return sha
    except subprocess.CalledProcessError as e:
        print(
            f"Error getting Git commit SHA: {e.output.decode('utf-8').strip()}",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        print(
            "Error: Git command not found. Is Git installed and in your PATH?",
            file=sys.stderr,
        )
        return None


def get_current_git_branch():
    try:
        branch_bytes = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], stderr=subprocess.STDOUT
        )
        branch_name = branch_bytes.decode("utf-8").strip()
        if not branch_name or branch_name == "HEAD":
            print(
                "Error: Could not determine current branch. Are you in a detached HEAD state?",
                file=sys.stderr,
            )
            return None
        return branch_name
    except subprocess.CalledProcessError as e:
        print(
            f"Error getting Git branch: {e.output.decode('utf-8').strip()}",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        print(
            "Error: Git command not found. Is Git installed and in your PATH?",
            file=sys.stderr,
        )
        return None


def get_latest_action_run(branch_name):
    global OWNER, REPO
    if not OWNER or not REPO:
        detected_owner, detected_repo = get_owner_and_repo_from_git()
        if detected_owner and detected_repo:
            OWNER = detected_owner
            REPO = detected_repo
        else:
            print(
                "Error: OWNER and REPO are not set and could not be auto-detected.",
                file=sys.stderr,
            )
            return None
    if not OWNER or not REPO:
        print("Error: OWNER and REPO are still not set.", file=sys.stderr)
        return None

    params = {"branch": branch_name}
    query_string = urllib.parse.urlencode(params)
    api_url = f"https://api.github.com/repos/{OWNER}/{REPO}/actions/workflows/{WORKFLOW_FILE_NAME}/runs?{query_string}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-GitHubActionStatusScript",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    runs_data = make_api_request(api_url, headers)
    if not runs_data:
        return None

    if runs_data.get("total_count", 0) == 0 or not runs_data.get("workflow_runs"):
        print(
            f"No workflow runs found for '{WORKFLOW_FILE_NAME}' on branch '{branch_name}'.",
            file=sys.stderr,
        )
        return None
    return runs_data["workflow_runs"][0]


def fetch_jobs_for_run(jobs_url: str, token: str) -> list[dict] | None:
    """Fetches all job objects for a given workflow run's jobs_url."""
    print(f"Fetching job details from: {jobs_url}", file=sys.stderr)
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-GitHubActionStatusScript",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    jobs_data_response = make_api_request(jobs_url, headers)
    if not jobs_data_response:
        return None

    # The API for jobs_url returns an object with a "jobs" array
    return jobs_data_response.get("jobs")


class NoRedirection(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_302


def get_job_log_content(
    job_id: int, job_name: str, owner: str, repo: str, token: str
) -> list[str] | None:
    print(
        f"Attempting to fetch log content for job '{job_name}' (ID: {job_id})",
        file=sys.stderr,
    )
    log_api_url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/jobs/{job_id}/logs"
    )
    base_headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Python-GitHubActionStatusScript",
    }
    if token:
        base_headers["Authorization"] = f"token {token}"

    zip_data_bytes = None
    content_type = ""
    opener = urllib.request.build_opener(NoRedirection)
    req_log_location = urllib.request.Request(
        log_api_url, headers=base_headers, method="GET"
    )

    try:
        print(f"Fetching log location from: {log_api_url}", file=sys.stderr)
        opener.open(req_log_location, timeout=30)
        print(
            f"Warning: Request to {log_api_url} did not redirect as expected.",
            file=sys.stderr,
        )
        return None
    except urllib.error.HTTPError as e_redirect:
        if 300 <= e_redirect.code < 400 and "Location" in e_redirect.headers:
            actual_log_download_url = e_redirect.headers["Location"]
            print(
                f"Redirected to log location: {actual_log_download_url}",
                file=sys.stderr,
            )
            headers_for_storage = {"User-Agent": base_headers["User-Agent"]}
            req_actual_log = urllib.request.Request(
                actual_log_download_url, headers=headers_for_storage, method="GET"
            )
            try:
                with urllib.request.urlopen(
                    req_actual_log, timeout=60
                ) as response_actual_log:
                    if response_actual_log.status != 200:
                        print(
                            f"Error: Expected 200 for log download from {actual_log_download_url}, got {response_actual_log.status}.",
                            file=sys.stderr,
                        )
                        try:
                            print(
                                f"Storage Response body: {response_actual_log.read().decode('utf-8', 'replace')}",
                                file=sys.stderr,
                            )
                        except:
                            pass
                        return None
                    print(
                        "Successfully initiated download from redirected URL.",
                        file=sys.stderr,
                    )
                    content_type = (
                        response_actual_log.info().get("Content-Type", "").lower()
                    )
                    print(f"Downloaded content type: {content_type}", file=sys.stderr)
                    zip_data_bytes = response_actual_log.read()
            except Exception as e_storage:
                print(
                    f"Error downloading log from storage {actual_log_download_url}: {e_storage}",
                    file=sys.stderr,
                )
                return None
        else:
            print(
                f"HTTP Error (not redirect/no Location) fetching log location: {e_redirect.code} {e_redirect.reason} from {e_redirect.url}",
                file=sys.stderr,
            )
            try:
                print(
                    f"API Response content: {e_redirect.read().decode('utf-8', 'replace')}",
                    file=sys.stderr,
                )
            except:
                pass
            return None
    except Exception as e_initial:
        print(
            f"Unexpected error fetching log location {log_api_url}: {e_initial}",
            file=sys.stderr,
        )
        return None

    if not zip_data_bytes:
        print("Failed to download log data bytes.", file=sys.stderr)
        return None

    log_content_lines = []
    try:
        if "text/plain" in content_type:
            print("Processing log data as plain text.", file=sys.stderr)
            log_content_lines = zip_data_bytes.decode(
                "utf-8", errors="replace"
            ).splitlines()
        elif (
            "application/zip" in content_type
            or "application/octet-stream" in content_type
            or not content_type
        ):
            if not content_type:
                print("Warning: Content-Type missing. Attempting ZIP.", file=sys.stderr)
            print("Processing log data as ZIP archive.", file=sys.stderr)
            try:
                zip_data_io = io.BytesIO(zip_data_bytes)
                with zipfile.ZipFile(zip_data_io, "r") as zip_ref:
                    log_text_parts = []
                    for member_name in zip_ref.namelist():
                        if member_name.endswith(".txt") or "/" not in member_name:
                            log_text_parts.append(
                                zip_ref.read(member_name).decode(
                                    "utf-8", errors="replace"
                                )
                            )
                    if not log_text_parts:
                        return []
                    log_content_lines = "".join(log_text_parts).splitlines()
            except zipfile.BadZipFile:
                print(
                    "Error: Not a valid zip. Fallback to plain text.", file=sys.stderr
                )
                log_content_lines = zip_data_bytes.decode(
                    "utf-8", errors="replace"
                ).splitlines()
        else:
            print(
                f"Warning: Unhandled Content-Type '{content_type}'. Attempting plain text.",
                file=sys.stderr,
            )
            log_content_lines = zip_data_bytes.decode(
                "utf-8", errors="replace"
            ).splitlines()
    except Exception as e_proc:
        print(f"Error processing log data for '{job_name}': {e_proc}", file=sys.stderr)
        return None

    if not log_content_lines and zip_data_bytes:
        return []  # Empty but successfully processed
    print(
        f"Successfully processed log for job '{job_name}'. Lines: {len(log_content_lines)}",
        file=sys.stderr,
    )
    return log_content_lines


def main():
    parser = argparse.ArgumentParser(
        description="Fetch latest GitHub Action status for a workflow and branch."
    )
    parser.add_argument(
        "--branch",
        type=str,
        help="The Git branch to query for workflow runs. Overrides auto-detection.",
    )
    args = parser.parse_args()

    output_data = {}
    output_data.setdefault("git_context", {})  # Initialize git_context

    try:
        # Determine branch_to_query (this is for the API call)
        if args.branch is not None and args.branch != "":
            # User explicitly provided a branch, so skip auto-detection completely
            branch_to_query = args.branch
            print(
                f"Using specified branch from --branch argument for API query: {branch_to_query}",
                file=sys.stderr,
            )

            # Do not attempt to detect branch for local context when --branch is supplied
            local_head_branch_for_context = None
        else:
            print(
                "Attempting to retrieve current Git branch for API query (no --branch argument provided)...",
                file=sys.stderr,
            )
            current_branch_for_query = (
                get_current_git_branch()
            )  # May print its own errors
            if current_branch_for_query:
                branch_to_query = current_branch_for_query
                print(
                    f"Using current Git branch for API query: {branch_to_query}",
                    file=sys.stderr,
                )
            else:
                # Error already printed by get_current_git_branch() if it failed
                print(
                    "Warning: Failed to determine current Git branch for API query. Defaulting to 'main'.",
                    file=sys.stderr,
                )
                branch_to_query = "main"

            # For git_context we record whatever branch we managed to detect (may be None)
            local_head_branch_for_context = current_branch_for_query

        # Populate git_context for JSON output (informational about local state)
        output_data["git_context"]["head_branch"] = (
            local_head_branch_for_context or None
        )

        # NOTE: When --branch is supplied we purposefully do NOT attempt to reconcile against local HEAD
        if (
            args.branch
            and local_head_branch_for_context
            and local_head_branch_for_context != args.branch
        ):
            print(
                f"Note: Local Git HEAD is on branch '{local_head_branch_for_context}', but API query is for specified branch '{args.branch}'.",
                file=sys.stderr,
            )
        elif args.branch and local_head_branch_for_context is None:
            # Could be detached head or non-git directory
            print(
                f"Note: Could not determine local Git HEAD branch for context. API query is for specified branch '{args.branch}'.",
                file=sys.stderr,
            )
        # When args.branch is not supplied, local_head_branch_for_context is already used for branch_to_query (or default 'main')

        # SHA retrieval is for local context.
        # The get_current_git_commit_sha() function prints its own errors to stderr if it fails.
        print(
            "Attempting to retrieve current Git commit SHA (local context)...",
            file=sys.stderr,
        )
        current_sha_for_context = get_current_git_commit_sha()
        output_data["git_context"]["head_sha"] = current_sha_for_context or None
        if current_sha_for_context:
            print(
                f"Current Git SHA (local context): {current_sha_for_context}",
                file=sys.stderr,
            )
        else:
            print(
                "Warning: Failed to determine Git SHA (local context).", file=sys.stderr
            )

        # Ensure branch_to_query is robustly set (should be by logic above)
        if not branch_to_query:
            print(
                "Critical Error: branch_to_query ended up not being set. Defaulting to 'main'. This indicates a logic flaw.",
                file=sys.stderr,
            )
            branch_to_query = "main"

        print(
            f"\nAttempting to retrieve latest Action run for workflow '{WORKFLOW_FILE_NAME}' on branch '{branch_to_query}' (this is the branch used for API query)...",
            file=sys.stderr,
        )
        latest_run = get_latest_action_run(branch_to_query)

        if OWNER and REPO:  # OWNER/REPO are global, set by get_latest_action_run
            output_data.setdefault("repository", {})["owner"] = OWNER
            output_data.setdefault("repository", {})["name"] = REPO
        else:
            print(
                "Error: OWNER/REPO not determined. Repo info missing.", file=sys.stderr
            )
            output_data.setdefault("repository", {"owner": None, "name": None})

        if latest_run:
            wf_run_data = output_data.setdefault("workflow_run", {})
            wf_run_data["id"] = latest_run.get("id")
            wf_run_data["workflow_name"] = latest_run.get(
                "display_title", latest_run.get("name", WORKFLOW_FILE_NAME)
            )
            wf_run_data["url"] = latest_run.get("html_url")
            wf_run_data["status"] = latest_run.get("status")
            wf_run_data["conclusion"] = latest_run.get("conclusion")
            # Optional extra data:
            # wf_run_data["created_at"] = latest_run.get("created_at")
            # wf_run_data["updated_at"] = latest_run.get("updated_at")

            print(
                f"\n--- Workflow Run for JSON --- \n  Name: {wf_run_data.get('workflow_name')}\n  Conclusion: {wf_run_data.get('conclusion')}\n-----------------------------",
                file=sys.stderr,
            )

            output_data["failed_jobs"] = []  # Initialize failed_jobs list
            if wf_run_data.get("conclusion") == "failure" and latest_run.get(
                "jobs_url"
            ):
                if OWNER and REPO:
                    jobs_list = fetch_jobs_for_run(latest_run["jobs_url"], GITHUB_TOKEN)
                    if jobs_list:
                        for job in jobs_list:
                            if job.get("conclusion") == "failure":
                                print(
                                    f"Processing failed job: {job.get('name')} (ID: {job.get('id')})",
                                    file=sys.stderr,
                                )
                                job_data = {
                                    "id": job.get("id"),
                                    "name": job.get("name"),
                                    "html_url": job.get("html_url"),
                                    "conclusion": job.get("conclusion"),
                                    "labels": job.get("labels", []),
                                }

                                log_lines = get_job_log_content(
                                    job.get("id"),
                                    job.get("name"),
                                    OWNER,
                                    REPO,
                                    GITHUB_TOKEN,
                                )

                                error_clusters = []
                                synthetic_step_name = (
                                    f"Analysis of {job.get('name', 'job')}"
                                )
                                if (
                                    log_lines is not None
                                ):  # Can be [] for empty but successfully processed log
                                    if log_lines:  # Only cluster if there are lines
                                        error_clusters = find_and_cluster_errors(
                                            log_lines, LOG_CONTEXT_LINES
                                        )
                                    job_steps = [
                                        {
                                            "name": synthetic_step_name,
                                            "status": "completed",  # Assuming job completed to give a conclusion
                                            "conclusion": job_data[
                                                "conclusion"
                                            ],  # Should be 'failure'
                                            "number": 1,  # Synthetic step number
                                            "error_summary": {
                                                "error_clusters": error_clusters
                                            },
                                        }
                                    ]
                                else:  # Log retrieval failed
                                    job_steps = [
                                        {
                                            "name": synthetic_step_name,
                                            "status": "completed",
                                            "conclusion": "failure",  # Mark step as failure due to log issue
                                            "number": 1,
                                            "error_summary": {
                                                "error_clusters": [
                                                    {
                                                        "log_context": [
                                                            f"Failed to retrieve logs for job: {job.get('name')}"
                                                        ],
                                                        "errors": [
                                                            {
                                                                "log_line_number": 1,
                                                                "raw_message": "Log content unavailable.",
                                                            }
                                                        ],
                                                    }
                                                ]
                                            },
                                        }
                                    ]
                                job_data["steps"] = job_steps
                                output_data["failed_jobs"].append(job_data)
                    else:
                        print(
                            f"Failed to fetch job list from {latest_run['jobs_url']}",
                            file=sys.stderr,
                        )
                        output_data["failed_jobs"].append(
                            {
                                "error": f"Failed to fetch job list from {latest_run['jobs_url']}"
                            }
                        )
                else:
                    print(
                        "Cannot fetch jobs: OWNER or REPO not determined.",
                        file=sys.stderr,
                    )
                    output_data["failed_jobs"].append(
                        {"error": "OWNER/REPO undetermined, cannot fetch job details."}
                    )

        else:  # Workflow did not fail or no jobs_url
            print("No failed jobs to process or jobs_url missing.", file=sys.stderr)
            # output_data["failed_jobs"] is already initialized to []

        print(json.dumps(output_data, indent=2), file=sys.stdout)

    except Exception as e:
        error_json = {
            "script_error": True,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
            "partial_data": output_data,
        }
        print(json.dumps(error_json, indent=2), file=sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
