"""Epic-scoped regression pin for MoonLadderStudios/MoonMind#3930.

The four child issues (#3967-#3970) own their enforcement, explanation,
reporting, and tool-pack tests. This module pins only what the #3930 umbrella
directly asserts: the integrated journey is described with an exact
entrypoint, authorization boundary, terminal evidence, and recovery behavior;
repository events alone cannot launch work; local-first behavior is preserved
honestly; and closed-not-planned #3971 stays excluded.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
JOURNEY_DOC = REPO_ROOT / "docs" / "Workflows" / "SoftwareEngineeringAdoptionJourney.md"
EXECUTIONS_ROUTER = REPO_ROOT / "api_service" / "api" / "routers" / "executions.py"
ACTIVITY_RUNTIME = REPO_ROOT / "moonmind" / "workflows" / "temporal" / "activity_runtime.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_journey_doc_defines_entrypoint_boundary_evidence_and_recovery() -> None:
    text = _read(JOURNEY_DOC)
    assert "POST /api/executions" in text
    assert "get_current_user" in text
    assert "terminal evidence" in text.lower() or "Terminal evidence" in text
    assert "Recovery behavior" in text or "recovery" in text.lower()
    assert "Publish Saved Work" in text
    assert "publish_result.json" in text


def test_journey_doc_denies_repository_event_authority() -> None:
    text = _read(JOURNEY_DOC)
    assert "alone cannot authorize" in text
    assert "No webhook-driven execution path" in text or "no inbound" in text.lower()
    assert "#3967" in text


def test_journey_doc_preserves_local_first_and_honest_limitations() -> None:
    text = _read(JOURNEY_DOC)
    assert "repository-independent" in text.lower()
    assert "Planned, implemented, and qualified" in text or "planned-vs-implemented" in text.lower()
    assert "no_eligible_free_model" in text
    # Phrasing precision (child #3968): the user-facing explanation above maps
    # to these product-code gate reasons, not to a separate free-model string.
    assert "no_eligible_profile" in text
    assert "no_eligible_codex_oauth_profile" in text
    assert "api_service/api/routers/omnigent_catalog.py" in text


def test_journey_doc_traces_readme_pillars_to_code() -> None:
    text = _read(JOURNEY_DOC)
    for pillar in ("Security", "Resilience", "Observability"):
        assert pillar in text
    assert "SecretsSystem.md" in text
    assert "repository_contract.py" in text
    assert "not proof of end-to-end enforcement" in text


def test_journey_doc_excludes_closed_not_planned_3971() -> None:
    text = _read(JOURNEY_DOC)
    assert "#3971" in text
    assert "not planned" in text
    assert "Excluded" in text or "excluded" in text


def test_execution_creation_requires_authenticated_operator() -> None:
    text = _read(EXECUTIONS_ROUTER)
    assert "get_current_user" in text
    assert 'APIRouter(prefix="/api/executions"' in text


def test_no_inbound_github_webhook_execution_path() -> None:
    executions_text = _read(EXECUTIONS_ROUTER)
    lowered = executions_text.lower()
    assert "x-hub-signature" not in lowered
    assert "x-github-event" not in lowered
    # The only webhook-adjacent execution surface is the outbound completion
    # notification, which lives in the activity runtime, not in admission.
    runtime_text = _read(ACTIVITY_RUNTIME)
    assert "execution.notification.webhook.payload" in runtime_text
