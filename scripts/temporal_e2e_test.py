#!/usr/bin/env python3
import os
import sys
import time

import requests

API_BASE = os.getenv("API_BASE_URL", "http://localhost:5000/api")


def main():
    print("Starting Temporal E2E test...")
    # 1. Create a task
    payload = {
        "workflowType": "MoonMind.Run",
        "title": "E2E Automated Verification",
        "initialParameters": {"entry": "Test execution"},
    }

    try:
        response = requests.post(f"{API_BASE}/executions", json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to create execution: {e}")
        sys.exit(1)

    data = response.json()
    workflow_id = data.get("workflowId")
    if not workflow_id:
        print(f"No workflowId in response: {data}")
        sys.exit(1)

    print(f"Created execution: {workflow_id}")

    # 2. Poll for worker execution and state progression until completion
    max_retries = 60
    sleep_seconds = 2
    success = False

    for i in range(max_retries):
        try:
            resp = requests.get(f"{API_BASE}/executions/{workflow_id}", timeout=5)
            resp.raise_for_status()
            current_state = resp.json()

            state = current_state.get("state")
            raw_state = current_state.get("rawState")
            temporal_status = current_state.get("temporalStatus")
            dashboard_status = current_state.get("dashboardStatus")

            print(
                f"[{i+1}/{max_retries}] Workflow {workflow_id} state: {state} (raw: {raw_state}), temporalStatus: {temporal_status}, dashboardStatus: {dashboard_status}"
            )

            # Wait for completion
            if temporal_status in ("completed", "failed", "canceled"):
                print(
                    f"Worker finished task. Final temporalStatus is: {temporal_status}"
                )
                if temporal_status == "completed":
                    success = True
                break
        except Exception as e:
            print(f"Error polling execution: {e}")

        time.sleep(sleep_seconds)
    else:
        print("Timeout waiting for worker to complete task.")
        sys.exit(1)

    if not success:
        print("Workflow did not complete successfully.")
        sys.exit(1)

    # 3. Check artifacts
    try:
        resp = requests.get(f"{API_BASE}/executions/{workflow_id}", timeout=5)
        current_state = resp.json()
        artifacts = current_state.get("artifactRefs", [])
        print(f"Execution has {len(artifacts)} artifact(s) linked in the state.")

        # Verify UI Status aligns with Temporal workflow state
        if current_state.get("dashboardStatus") != "completed":
            print(
                f"Warning: Dashboard status is {current_state.get('dashboardStatus')}, expected 'completed'"
            )
        else:
            print("Dashboard status aligns with Temporal state.")

        # Optional: check if artifacts are actually retrievable if there are any
        if artifacts:
            for artifact in artifacts:
                print(f"Found artifact reference: {artifact}")

        actions = current_state.get("actions", {})
        print(
            f"Execution UI Actions available: {list(k for k, v in actions.items() if v is True)}"
        )
    except Exception as e:
        print(f"Error checking artifacts/status: {e}")
        sys.exit(1)

    print("Temporal E2E test completed successfully!")


if __name__ == "__main__":
    main()
