#!/usr/bin/env bash
set -euo pipefail

matches_openapi_affecting_path() {
    local path="$1"

    case "$path" in
        api_service/*|\
moonmind/schemas/__init__.py|\
moonmind/schemas/agent_runtime_models.py|\
moonmind/schemas/agent_skill_models.py|\
moonmind/schemas/chat_models.py|\
moonmind/schemas/documents_models.py|\
moonmind/schemas/managed_session_models.py|\
moonmind/schemas/manifest_ingest_models.py|\
moonmind/schemas/manifest_models.py|\
moonmind/schemas/manifest_v0_models.py|\
moonmind/schemas/temporal_activity_models.py|\
moonmind/schemas/temporal_artifact_models.py|\
moonmind/schemas/temporal_models.py|\
moonmind/schemas/temporal_signal_contracts.py|\
moonmind/schemas/workflow_models.py|\
moonmind/schemas/workflow_proposal_models.py|\
tools/export_openapi.py|tools/generate_openapi_types.py|tools/run_repo_python.sh|frontend/src/generated/openapi.ts|package.json|package-lock.json|pyproject.toml|uv.lock)
            return 0
            ;;
    esac

    return 1
}

if [[ $# -gt 0 ]]; then
    for path in "$@"; do
        if matches_openapi_affecting_path "$path"; then
            exit 0
        fi
    done

    exit 1
fi

while IFS= read -r path; do
    if matches_openapi_affecting_path "$path"; then
        exit 0
    fi
done

exit 1
