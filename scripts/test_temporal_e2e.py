import os
import sys
import time

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")


def wait_for_api():
    print(f"Waiting for API at {API_URL}/health ...")
    for _ in range(30):
        try:
            resp = requests.get(f"{API_URL}/health", timeout=2)
            if resp.status_code == 200:
                print("API is healthy.")
                return True
        except requests.exceptions.RequestException as exc:
            print(f"Health check request failed: {exc}. Retrying...")
        time.sleep(2)
    return False


def main():
    if not wait_for_api():
        print("Error: API did not become healthy in time.")
        sys.exit(1)

    print("\\n1. Creating Temporal Task...")
    payload = {
        "workflowType": "MoonMind.Run",
        "title": "E2E Temporal Migration Test",
        "initialParameters": {"entry": "E2E test execution"},
    }
    resp = requests.post(f"{API_URL}/api/executions", json=payload)
    if resp.status_code != 201:
        print(f"Error creating task: {resp.status_code} {resp.text}")
        sys.exit(1)

    data = resp.json()
    workflow_id = data.get("workflowId") or data.get("id")
    print(f"Created workflow: {workflow_id}")

    print("\\n2. Waiting for worker execution...")
    # Poll until the status is no longer "initializing" or "queued"
    max_retries = 30
    status = ""
    exec_data = {}
    for _ in range(max_retries):
        resp = requests.get(f"{API_URL}/api/executions/{workflow_id}")
        if resp.status_code == 200:
            exec_data = resp.json()
            status = exec_data.get("temporalStatus") or exec_data.get(
                "status", "unknown"
            )
            print(f"Current status: {status}")
            if status not in ["initializing", "queued", "running", "unknown"]:
                break
            # Also check dashboardStatus
            dashboard_status = exec_data.get("dashboardStatus")
            if dashboard_status in ["completed", "failed", "awaiting_action"]:
                print(f"Dashboard status reached: {dashboard_status}")
                break
        time.sleep(2)

    print("\\n3. Checking Artifacts...")
    run_id = exec_data.get("runId")
    if run_id:
        namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
        resp = requests.get(
            f"{API_URL}/api/executions/{namespace}/{workflow_id}/{run_id}/artifacts"
        )
        if resp.status_code == 200:
            artifacts = resp.json().get("artifacts", [])
            print(f"Found {len(artifacts)} artifacts.")
        else:
            print(f"Checking artifacts endpoint failed with status {resp.status_code}.")
            sys.exit(1)
    else:
        print("Could not determine runId, skipping artifact check.")

    print("\\n4. Checking UI Status (via API)...")
    resp = requests.get(f"{API_URL}/api/tasks/{workflow_id}/source")
    if resp.status_code == 200:
        source_data = resp.json()
        print(
            f"Task source resolution: {source_data.get('sourceLabel')} -> {source_data.get('detailPath')}"
        )
    else:
        print(
            "Source resolution endpoint not found or workflow not available in dashboard yet."
        )
        sys.exit(1)

    print("\\n5. Cleaning up (cancelling execution if still running)...")
    if status in ["initializing", "queued", "running"]:
        cancel_resp = requests.post(f"{API_URL}/api/executions/{workflow_id}/cancel")
        if cancel_resp.status_code in [200, 202]:
            print("Successfully requested cancellation.")
        else:
            print(f"Failed to cancel: {cancel_resp.status_code}")

    print("\\nE2E Test Completed.")


if __name__ == "__main__":
    main()
