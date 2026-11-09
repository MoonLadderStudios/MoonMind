"""Technical presets use the existing typed admission and shared compiler."""

from pathlib import Path

import pytest
import yaml

from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
)

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "contract,value",
    [
        (
            "repository-scan",
            {"scanner": "opengrep", "outputDirectory": "reports", "rules": []},
        ),
        ("image-assessment", {"operation": "assess", "target": "archive"}),
        (
            "cloud-assessment",
            {
                "provider": "azure",
                "assessmentRef": "test",
                "account": "123456789012",
                "credentialRef": "db://test",
                "regions": ["us-east-1"],
                "services": ["s3"],
                "egressPolicyRef": "test",
            },
        ),
        (
            "web-assessment",
            {
                "mode": "api",
                "origin": "https://example.test",
                "routes": [{"path": "/"}],
                "egressPolicyRef": "test",
            },
        ),
    ],
)
def test_preset_inputs_reject_invalid_native_admission(contract, value):
    with pytest.raises(PresetValidationError) as exc:
        PresetCatalogService(None)._resolve_inputs(
            schema=[
                {
                    "name": "assessment",
                    "type": "object",
                    "required": True,
                    "schema": {"type": "object", "x-moonmind-input-contract": contract},
                }
            ],
            submitted={"assessment": value},
        )
    assert exc.value.errors[0]["path"].startswith("preset.inputs.assessment")


@pytest.mark.parametrize(
    "slug",
    [
        "security-code-supply-chain",
        "security-image-sbom",
        "security-aws-posture",
        "security-web-api",
    ],
)
def test_supported_catalog_contracts(slug):
    seed = yaml.safe_load(
        (ROOT / "api_service/data/presets" / f"{slug}.yaml").read_text()
    )
    assert seed["annotations"]["inputSchema"]["properties"]["assessment"][
        "x-moonmind-input-contract"
    ]
    assert seed["annotations"]["workflowPublish"] == {"mode": "none"}
    assert 1 <= len(seed["steps"]) <= 3
    assert all(step["skill"]["id"] == "security-review" for step in seed["steps"])


CASES = [
    (
        "security-code-supply-chain",
        "repositoryScan",
        {
            "scanner": "gitleaks",
            "inputPath": "src",
            "outputDirectory": "reports",
            "exclusions": ["vendor"],
            "timeoutSeconds": 45,
            "gitleaksOwnsSecrets": False,
            "requireFreshFeeds": False,
        },
    ),
    (
        "security-image-sbom",
        "imageAssessment",
        {
            "operation": "sbom-generate",
            "target": "archive",
            "inputArtifactRef": "art_archive",
            "timeoutSeconds": 90,
        },
    ),
    (
        "security-aws-posture",
        "cloudAssessment",
        {
            "provider": "aws",
            "assessmentRef": "test",
            "account": "123456789012",
            "credentialRef": "db://test",
            "regions": ["us-east-1"],
            "services": ["s3"],
            "egressPolicyRef": "test",
            "timeoutSeconds": 300,
        },
    ),
    (
        "security-web-api",
        "webAssessment",
        {
            "mode": "baseline",
            "assessmentRef": "test",
            "origin": "https://example.test",
            "routes": [
                {"path": "/", "methods": ["GET"]},
                {"path": "/login", "methods": ["GET", "POST"]},
            ],
            "exclusions": ["/logout"],
            "egressPolicyRef": "test",
            "timeoutSeconds": 300,
            "identity": {
                "name": "reader",
                "method": "form",
                "credentialRef": "vault://reader",
                "verificationPath": "/",
                "loggedInMarker": "Fixture reader",
                "loginPath": "/login",
                "username": "reader",
                "usernameField": "username",
                "passwordField": "password",
            },
        },
    ),
]


@pytest.mark.parametrize("slug,job_key,assessment", CASES)
def test_saved_native_defaults_keep_contract_names_and_reject_secrets(
    slug, job_key, assessment
):
    from api_service.services.presets.catalog import (
        _normalize_seed_annotations,
        _schema_contract_input_definitions,
    )

    seed = yaml.safe_load(
        (ROOT / "api_service/data/presets" / f"{slug}.yaml").read_text()
    )
    seed["annotations"]["defaults"] = {"assessment": assessment}
    assert _normalize_seed_annotations(seed)["defaults"]["assessment"] == assessment
    definition = next(
        item
        for item in _schema_contract_input_definitions(
            input_schema=seed["annotations"]["inputSchema"],
            ui_schema={},
            defaults={"assessment": assessment},
        )
        if item["name"] == "assessment"
    )
    assert (
        PresetCatalogService(None)._validate_inputs_schema([definition])[0]["default"]
        == assessment
    )
    for invalid in [
        dict(assessment, password="unvalidated"),
        dict(assessment, outputDirectory="token=raw-secret"),
    ]:
        seed["annotations"]["defaults"] = {"assessment": invalid}
        with pytest.raises(PresetValidationError):
            _normalize_seed_annotations(seed)
        with pytest.raises(PresetValidationError):
            PresetCatalogService(None)._validate_inputs_schema(
                [dict(definition, default=invalid)]
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("slug,job_key,assessment", CASES)
async def test_api_expansion_and_rerun_feed_existing_native_jobs(
    tmp_path, monkeypatch, slug, job_key, assessment
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api_service.api.routers.presets import router
    from api_service.db.base import get_async_session
    from api_service.services.single_user_conversion import run_guarded_upgrade
    from moonmind.config.settings import settings
    from moonmind.security.assessment_preset_inputs import ASSESSMENT_INPUT_CONTRACTS
    from moonmind.security.cloud_assessments import build_cloud_assessment_job
    from moonmind.security.image_assessments import build_image_assessment_job
    from moonmind.security.repository_scanners import build_scanner_job
    from moonmind.security.web_assessments import build_web_assessment_job
    from tests.unit.api.test_presets_service import template_db

    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.feature_flags, "disable_preset_catalog", False)
    async with template_db(tmp_path) as maker:
        async with maker() as session:
            await run_guarded_upgrade(session, operator_authorized=True)
            seed_dir = tmp_path / "seeds"
            seed_dir.mkdir()
            source = ROOT / "api_service/data/presets" / f"{slug}.yaml"
            (seed_dir / source.name).write_text(source.read_text())
            await PresetCatalogService(session).import_seed_templates(seed_dir=seed_dir)

        async def sessions():
            async with maker() as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_async_session] = sessions
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 12345)),
            base_url="http://localhost",
        ) as client:
            values = {
                "goal": "Inspect controlled test target only",
                "assessment": assessment,
            }
            response = await client.post(
                f"/api/presets/{slug}:expand", json={"inputs": values}
            )
            assert response.status_code == 200, response.text
            expanded = response.json()
            step = expanded["steps"][0]
            selection = step["annotations"]["securityAssessment"]
            assert selection["admission"] == step["skill"]["args"]["assessment"]
            for key, value in assessment.items():
                assert selection["admission"][key] == value
            assert step["skill"]["args"]["baseline_ref"] in (None, "")
            model = ASSESSMENT_INPUT_CONTRACTS[selection["kind"]].model_validate(
                selection["admission"]
            )
            builder = {
                "repositoryScan": build_scanner_job,
                "imageAssessment": build_image_assessment_job,
                "cloudAssessment": build_cloud_assessment_job,
                "webAssessment": build_web_assessment_job,
            }[job_key]
            job = builder(model)
            assert selection["jobSpec"] == job
            assert step["skill"]["args"]["job_spec"] == job
            assert "moonmind container run --spec" in step["instructions"]
            assert job[job_key] == model.model_dump(mode="json", by_alias=True)
            assert job["outputs"]
            assert "assessmentScope" not in expanded
            rerun = await client.post(
                f"/api/presets/{slug}:expand",
                json={"inputs": expanded["appliedTemplate"]["inputs"]},
            )
            assert rerun.status_code == 200, rerun.text
            assert rerun.json()["steps"] == expanded["steps"]
            detail = (await client.get(f"/api/presets/{slug}")).json()
            saved = await client.post(
                "/api/presets",
                json={
                    "slug": "saved-assessment",
                    "title": "Saved assessment",
                    "description": "Controlled target configuration",
                    "steps": detail["steps"],
                    "annotations": {
                        "inputSchema": detail["inputSchema"],
                        "uiSchema": detail["uiSchema"],
                        "defaults": dict(
                            detail["defaults"], **expanded["appliedTemplate"]["inputs"]
                        ),
                    },
                    "releaseStatus": "active",
                },
            )
            assert saved.status_code == 201, saved.text
            saved_detail = (await client.get("/api/presets/saved-assessment")).json()
            assert (
                saved_detail["defaults"]["assessment"]
                == expanded["appliedTemplate"]["inputs"]["assessment"]
            )
            saved_run = await client.post(
                "/api/presets/saved-assessment:expand", json={"inputs": {}}
            )
            assert saved_run.status_code == 200, saved_run.text
            assert saved_run.json()["steps"][0]["skill"]["id"] == step["skill"]["id"]
            assert (
                saved_run.json()["steps"][0]["skill"]["args"] == step["skill"]["args"]
            )
            assert (
                saved_run.json()["steps"][0]["annotations"]["securityAssessment"]
                == selection
            )

        async with maker() as session:
            catalog = PresetCatalogService(session)
            await catalog.create_template(
                slug="nested-assessment",
                title="Nested assessment",
                description="Controlled composed assessment",
                tags=[],
                inputs_schema=[],
                steps=[
                    {
                        "kind": "include",
                        "slug": slug,
                        "alias": "assessment",
                        "inputMapping": values,
                    }
                ],
                annotations={},
                required_capabilities=[],
            )
            nested = await catalog.expand_template(slug="nested-assessment", inputs={})
            assert nested["steps"][0]["skill"]["args"] == step["skill"]["args"]
            assert nested["steps"][0]["annotations"]["securityAssessment"] == selection


@pytest.mark.parametrize(
    "extra", [{"password": "hidden"}, {"outputDirectory": "ghp_secretvalue"}]
)
def test_typed_scanner_inputs_still_reject_secrets(extra):
    value = {"scanner": "gitleaks", "outputDirectory": "reports", **extra}
    with pytest.raises(PresetValidationError):
        PresetCatalogService(None)._resolve_inputs(
            schema=[
                {
                    "name": "assessment",
                    "type": "object",
                    "required": True,
                    "schema": {
                        "type": "object",
                        "x-moonmind-input-contract": "repository-scan",
                    },
                }
            ],
            submitted={"assessment": value},
        )


def test_native_validator_diagnostics_do_not_echo_untrusted_values():
    from moonmind.security.assessment_preset_inputs import validate_assessment_input

    value = dict(CASES[2][2], provider="privatevalue")
    with pytest.raises(ValueError) as exc:
        validate_assessment_input("cloud-assessment", value)
    assert "privatevalue" not in str(exc.value)
    assert "provider:" in str(exc.value)


@pytest.mark.parametrize("explicit", [None, False, True])
def test_nested_defaults_preserve_explicit_feed_policy(explicit):
    from api_service.services.presets.catalog import _schema_contract_input_definitions

    seed = yaml.safe_load(
        (ROOT / "api_service/data/presets/security-code-supply-chain.yaml").read_text()
    )
    schema = _schema_contract_input_definitions(
        input_schema=seed["annotations"]["inputSchema"], ui_schema={}, defaults={}
    )
    value = {"scanner": "trivy"}
    if explicit is not None:
        value["requireFreshFeeds"] = explicit
    result = PresetCatalogService(None)._resolve_inputs(
        schema=schema, submitted={"assessment": value}
    )
    assert result["assessment"]["requireFreshFeeds"] is (
        True if explicit is None else explicit
    )
    assert result["assessment"]["outputDirectory"] == "assessment-reports"


def test_web_preset_requires_matching_authorization_reference_before_expansion():
    from api_service.services.presets.catalog import _schema_contract_input_definitions

    seed = yaml.safe_load(
        (ROOT / "api_service/data/presets/security-web-api.yaml").read_text()
    )
    schema = _schema_contract_input_definitions(
        input_schema=seed["annotations"]["inputSchema"], ui_schema={}, defaults={}
    )
    value = dict(CASES[3][2])
    value.pop("assessmentRef", None)
    with pytest.raises(PresetValidationError) as error:
        PresetCatalogService(None)._resolve_inputs(
            schema=schema, submitted={"assessment": value}
        )
    assert "assessmentRef" in str(error.value.errors)
    value["assessmentRef"] = "web-review"
    result = PresetCatalogService(None)._resolve_inputs(
        schema=schema, submitted={"assessment": value}
    )
    assert result["assessment"]["assessmentRef"] == "web-review"
