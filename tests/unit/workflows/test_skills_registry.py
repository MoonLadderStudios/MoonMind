"""Unit tests for skills registry logic."""

from __future__ import annotations

from moonmind.workflows.skills import registry
from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore

def test_contract_registry_helpers_roundtrip_snapshot():
    store = InMemoryArtifactStore()
    skills = registry.parse_skill_registry(
        {
            "skills": [
                {
                    "name": "repo.run_tests",
                    "description": "Run tests",
                    "inputs": {
                        "schema": {
                            "type": "object",
                            "required": ["repo_ref"],
                            "properties": {"repo_ref": {"type": "string"}},
                        }
                    },
                    "outputs": {
                        "schema": {
                            "type": "object",
                            "required": ["ok"],
                            "properties": {"ok": {"type": "boolean"}},
                        }
                    },
                    "executor": {
                        "activity_type": "mm.skill.execute",
                        "selector": {"mode": "by_capability"},
                    },
                    "requirements": {"capabilities": ["sandbox"]},
                    "policies": {
                        "timeouts": {
                            "start_to_close_seconds": 10,
                            "schedule_to_close_seconds": 20,
                        },
                        "retries": {"max_attempts": 1},
                    },
                }
            ]
        }
    )
    registry.validate_skill_registry(skills)

    snapshot = registry.create_registry_snapshot(skills=skills, artifact_store=store)
    loaded = registry.load_registry_snapshot_from_artifact(
        artifact_ref=snapshot.artifact_ref,
        artifact_store=store,
    )

    assert loaded.digest == snapshot.digest
    assert loaded.get_skill(name="repo.run_tests").executor.activity_type == (
        "mm.skill.execute"
    )
