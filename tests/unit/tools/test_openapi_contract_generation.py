from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest-unit-tests.yml"


def test_mm852_contract_job_uses_minimal_generator_setup() -> None:
    """MM-852 / MM-846: contract checks install only contract-generation tools."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    contract_job_match = re.search(
        r"(?ms)^  run-generated-contracts:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert contract_job_match is not None
    contract_job = contract_job_match.group("body")

    assert "bash tools/install_openapi_typescript.sh" in contract_job
    assert "uv pip install --system -e ." in contract_job
    assert "uv pip install --system -e .[tests]" not in contract_job
    assert "npm ci" not in contract_job


def test_mm852_openapi_export_avoids_startup_only_llamaindex_imports() -> None:
    """MM-852 / MM-846: exporting OpenAPI should not import RAG/indexer runtime."""

    script = """
import json
import sys

import tools.export_openapi as export_openapi

schema = export_openapi.app.openapi()
llama_modules = [
    name for name in sys.modules
    if name == "llama_index" or name.startswith("llama_index.")
]
print(json.dumps({"paths": len(schema["paths"]), "llama_modules": llama_modules}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["paths"] > 0
    assert payload["llama_modules"] == []


def test_4129_generated_client_has_no_legacy_auth_routes() -> None:
    """#4129: generated OpenAPI client must not advertise removed auth routes.

    Static file check only (no app import): the fastapi-users
    login/register/reset/verify/users routers are unmounted, so
    ``frontend/src/generated/openapi.ts`` must contain no ``/api/v1/auth``
    path, no legacy auth operation, and no schema referenced solely by
    those operations. Regenerate with ``tools/generate_openapi_types.py``
    (``npm run api:types``) when the app changes; this test pins the
    deletion.
    """

    generated = REPO_ROOT / "frontend" / "src" / "generated" / "openapi.ts"
    content = generated.read_text(encoding="utf-8")

    for legacy_path in (
        '"/api/v1/auth/login"',
        '"/api/v1/auth/logout"',
        '"/api/v1/auth/register"',
        '"/api/v1/auth/forgot-password"',
        '"/api/v1/auth/reset-password"',
        '"/api/v1/auth/request-verify-token"',
        '"/api/v1/auth/verify"',
        '"/api/v1/auth/users/me"',
        '"/api/v1/auth/users/{id}"',
    ):
        assert legacy_path not in content, (
            f"removed auth route still advertised: {legacy_path}"
        )

    for operation in (
        "    auth_jwt_login_api_v1_auth_login_post:",
        "    auth_jwt_logout_api_v1_auth_logout_post:",
        "    register_register_api_v1_auth_register_post:",
        "    reset_forgot_password_api_v1_auth_forgot_password_post:",
        "    reset_reset_password_api_v1_auth_reset_password_post:",
        "    verify_request_token_api_v1_auth_request_verify_token_post:",
        "    verify_verify_api_v1_auth_verify_post:",
        "    users_current_user_api_v1_auth_users_me_get:",
        "    users_patch_current_user_api_v1_auth_users_me_patch:",
        "    users_user_api_v1_auth_users__id__get:",
        "    users_delete_user_api_v1_auth_users__id__delete:",
        "    users_patch_user_api_v1_auth_users__id__patch:",
    ):
        assert operation not in content, (
            f"removed auth operation still generated: {operation.strip()}"
        )

    for schema in (
        "BearerResponse",
        "Body_auth_jwt_login_api_v1_auth_login_post",
        "Body_reset_forgot_password_api_v1_auth_forgot_password_post",
        "Body_reset_reset_password_api_v1_auth_reset_password_post",
        "Body_verify_request_token_api_v1_auth_request_verify_token_post",
        "Body_verify_verify_api_v1_auth_verify_post",
        "UserCreate",
        "UserRead",
        "UserUpdate",
    ):
        assert f"        {schema}:" not in content, (
            f"orphaned auth schema still generated: {schema}"
        )
        assert f'["{schema}"]' not in content, (
            f"orphaned auth schema still referenced: {schema}"
        )


def test_4129_bearer_transport_advertises_no_removed_login_route() -> None:
    """#4129: BearerTransport tokenUrl must not point at a removed route.

    Static source check only (no app import): ``tokenUrl`` is OpenAPI docs
    metadata, and no token-issuance route remains mounted after the bundled
    Keycloak/fastapi-users route removal.
    """

    auth_source = (REPO_ROOT / "api_service" / "auth.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r"BearerTransport\(tokenUrl=(?P<value>.*?)\)", auth_source)
    assert match is not None, "BearerTransport declaration not found"
    assert "auth/jwt/login" not in match.group("value"), (
        "BearerTransport still advertises the removed auth/jwt/login route"
    )
