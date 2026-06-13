from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.memory.procedural import (
    EvidenceRun,
    FixPattern,
    extract_error_signature,
)
from moonmind.workflows.executions.prepared_context import (
    PreparedContextFailure,
    PreparedInputEntry,
    PreparedInputManifest,
    build_execution_context_bundle,
    build_memory_manifest,
    build_recovery_prepared_artifact_refs,
    build_prepared_input_manifest,
    build_retrieval_manifest,
    select_step_prepared_context,
)


def _task_payload() -> dict[str, object]:
    return {
        "inputAttachments": [
            {
                "artifactId": "artifact-objective",
                "filename": "objective.png",
                "contentType": "image/png",
                "sizeBytes": 42,
            }
        ],
        "steps": [
            {
                "id": "collect-evidence",
                "inputAttachments": [
                    {
                        "artifactId": "artifact-step-1",
                        "filename": "evidence.txt",
                        "contentType": "text/plain",
                        "sizeBytes": 17,
                    }
                ],
            },
            {
                "id": "write-report",
                "inputAttachments": [
                    {
                        "artifactId": "artifact-step-2",
                        "filename": "report-notes.txt",
                        "contentType": "text/plain",
                        "sizeBytes": 19,
                    }
                ],
            },
        ],
    }


def test_prepared_manifest_preserves_objective_and_step_targets() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    assert isinstance(manifest, PreparedInputManifest)
    assert manifest.manifest_ref.startswith("prepared-context-manifest://")
    assert [entry.target_kind for entry in manifest.entries] == [
        "objective",
        "step",
        "step",
    ]
    assert manifest.entries[0].raw_input_ref == "artifact://artifact-objective"
    assert manifest.entries[0].derived_context_ref == (
        "prepared-context://objective/artifact-objective"
    )
    assert manifest.entries[1].step_ref == "collect-evidence"
    assert manifest.entries[2].step_ref == "write-report"


def test_prepared_manifest_preserves_zero_byte_size() -> None:
    manifest = build_prepared_input_manifest(
        {
            "inputAttachments": [
                {
                    "artifactId": "empty-file",
                    "sizeBytes": 0,
                    "size": 42,
                }
            ]
        }
    )

    assert manifest.entries[0].size_bytes == 0


def test_prepared_manifest_rejects_step_attachment_without_stable_step_ref() -> None:
    with pytest.raises(ValueError, match="stable stepRef"):
        build_prepared_input_manifest(
            {
                "steps": [
                    {
                        "instructions": "No stable step identity",
                        "inputAttachments": [{"artifactId": "artifact-step"}],
                    }
                ]
            }
        )


def test_prepared_manifest_allows_anonymous_steps_without_attachments() -> None:
    manifest = build_prepared_input_manifest(
        {
            "inputAttachments": [{"artifactId": "objective-artifact"}],
            "steps": [
                {"instructions": "No attachments on this anonymous step"},
                {
                    "id": "attached-step",
                    "inputAttachments": [{"artifactId": "step-artifact"}],
                },
            ],
        }
    )

    assert [(entry.artifact_id, entry.step_ref) for entry in manifest.entries] == [
        ("objective-artifact", None),
        ("step-artifact", "attached-step"),
    ]


def test_prepared_manifest_bindings_survive_reorder_and_text_edits() -> None:
    original = {
        "steps": [
            {
                "id": "first-step",
                "instructions": "Original text",
                "inputAttachments": [{"artifactId": "first-artifact"}],
            },
            {
                "id": "second-step",
                "instructions": "Original text",
                "inputAttachments": [{"artifactId": "second-artifact"}],
            },
        ]
    }
    reordered = {
        "steps": [
            {
                "id": "second-step",
                "instructions": "Edited text",
                "inputAttachments": [{"artifactId": "second-artifact"}],
            },
            {
                "id": "first-step",
                "instructions": "Edited text",
                "inputAttachments": [{"artifactId": "first-artifact"}],
            },
        ]
    }

    original_manifest = build_prepared_input_manifest(original)
    reordered_manifest = build_prepared_input_manifest(reordered)

    original_bindings = {
        entry.artifact_id: entry.step_ref for entry in original_manifest.entries
    }
    reordered_bindings = {
        entry.artifact_id: entry.step_ref for entry in reordered_manifest.entries
    }

    assert reordered_bindings == original_bindings


def test_prepared_manifest_entries_include_stable_workspace_status_metadata() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    dumped = manifest.model_dump(by_alias=True)

    assert dumped["entries"][0]["workspacePath"] == (
        ".moonmind/inputs/objective/artifact-objective-objective.png"
    )
    assert dumped["entries"][0]["status"] == "prepared"
    assert dumped["entries"][1]["workspacePath"] == (
        ".moonmind/inputs/steps/collect-evidence/artifact-step-1-evidence.txt"
    )
    assert dumped["entries"][1]["status"] == "prepared"
    assert "data:image" not in str(dumped)
    assert "base64" not in str(dumped)


def test_prepared_models_reject_missing_step_binding_and_embedded_content() -> None:
    with pytest.raises(ValidationError, match="stepRef"):
        PreparedInputEntry.model_validate(
            {
                "artifactId": "artifact-step",
                "targetKind": "step",
                "rawInputRef": "artifact://artifact-step",
            }
        )

    with pytest.raises(ValueError, match="inline attachment content"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "artifact-objective",
                        "filename": "objective.png",
                        "contentType": "image/png",
                        "dataUrl": "data:image/png;base64,AAAA",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="inline attachment content"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "artifact-objective",
                        "filename": "has-space-in-data-url.txt",
                        "url": "data:image/png;base64,AAAA remaining-safe-text",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="generated markdown"):
        build_prepared_input_manifest(
            {
                "steps": [
                    {
                        "id": "collect-evidence",
                        "inputAttachments": [
                            {
                                "artifactId": "artifact-step-1",
                                "markdown": "![image](data:image/png;base64,AAAA)",
                            }
                        ],
                    }
                ]
            }
        )


def test_select_step_context_includes_objective_and_current_step_only() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    context = select_step_prepared_context(
        manifest,
        logical_step_id="collect-evidence",
    )

    assert context.logical_step_id == "collect-evidence"
    assert context.objective_context_refs == [
        "prepared-context://objective/artifact-objective"
    ]
    assert context.step_context_refs == [
        "prepared-context://steps/collect-evidence/artifact-step-1"
    ]
    assert context.raw_input_refs == [
        "artifact://artifact-objective",
        "artifact://artifact-step-1",
    ]
    assert "artifact-step-2" not in str(context.model_dump(by_alias=True))


def test_select_step_context_excludes_sibling_refs_from_shared_workspace() -> None:
    manifest = PreparedInputManifest.model_validate(
        {
            "manifestRef": "prepared-context-manifest://task-inputs",
            "entries": [
                {
                    "artifactId": "objective-image",
                    "targetKind": "objective",
                    "rawInputRef": "artifact://objective-image",
                    "derivedContextRef": "prepared-context://objective/objective-image",
                    "workspacePath": ".moonmind/inputs/shared/objective-image.png",
                },
                {
                    "artifactId": "collect-image",
                    "targetKind": "step",
                    "stepRef": "collect-evidence",
                    "rawInputRef": "artifact://collect-image",
                    "derivedContextRef": (
                        "prepared-context://steps/collect-evidence/collect-image"
                    ),
                    "workspacePath": ".moonmind/inputs/shared/image.png",
                },
                {
                    "artifactId": "report-image",
                    "targetKind": "step",
                    "stepRef": "write-report",
                    "rawInputRef": "artifact://report-image",
                    "derivedContextRef": (
                        "prepared-context://steps/write-report/report-image"
                    ),
                    "workspacePath": ".moonmind/inputs/shared/image.png",
                },
            ],
        }
    )

    context = select_step_prepared_context(
        manifest,
        logical_step_id="collect-evidence",
    )
    dumped = context.model_dump(by_alias=True)

    assert context.objective_context_refs == [
        "prepared-context://objective/objective-image"
    ]
    assert context.step_context_refs == [
        "prepared-context://steps/collect-evidence/collect-image"
    ]
    assert "report-image" not in str(dumped)
    assert "write-report" not in str(dumped)


def test_step_context_metadata_is_bounded_and_target_aware() -> None:
    manifest = build_prepared_input_manifest(_task_payload())
    context = select_step_prepared_context(manifest, logical_step_id="collect-evidence")

    metadata = context.to_metadata()

    assert metadata["manifestRef"] == manifest.manifest_ref
    assert metadata["logicalStepId"] == "collect-evidence"
    assert metadata["targetCounts"] == {"objective": 1, "step": 1}
    assert metadata["inputRefs"] == [
        "prepared-context://objective/artifact-objective",
        "prepared-context://steps/collect-evidence/artifact-step-1",
        "artifact://artifact-objective",
        "artifact://artifact-step-1",
    ]
    assert "data:image" not in str(metadata)


def test_execution_context_bundle_digest_is_stable_and_execution_scoped() -> None:
    manifest = build_prepared_input_manifest(_task_payload())
    context = select_step_prepared_context(manifest, logical_step_id="collect-evidence")

    first = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )
    duplicate = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )
    changed_execution = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=2,
        prepared_context=context,
        runtime_selection={"runtimeId": "codex_cli"},
    )

    assert first.context_bundle_digest == duplicate.context_bundle_digest
    assert first.context_bundle_ref == (
        f"execution-context-bundle://{first.context_bundle_digest}"
    )
    assert first.context_bundle_digest != changed_execution.context_bundle_digest
    assert first.builder_version == "execution-context-builder-v2"
    projection = first.to_manifest_projection()
    assert projection["context"]["contextBundleDigest"] == first.context_bundle_digest
    assert "preparedInputRefs" not in projection["context"]


def test_execution_context_records_retrieval_and_memory_manifest_refs() -> None:
    retrieval = build_retrieval_manifest(
        {
            "query": "execution context bundle",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://doc-1"],
            "filters": {"source": "docs"},
            "compactSummaries": ["Relevant source design section."],
        }
    )
    memory = build_memory_manifest(
        [
            {
                "proposalRef": "memory://proposal-1",
                "state": "proposed",
                "summary": "Remember failed execution evidence.",
            },
            {
                "proposalRef": "memory://proposal-2",
                "state": "rejected",
                "summary": "Rejected noisy suggestion.",
            },
        ]
    )
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        retrieval={
            "query": "execution context bundle",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://doc-1"],
            "filters": {"source": "docs"},
            "compactSummaries": ["Relevant source design section."],
        },
        memory_proposals=[
            {
                "proposalRef": "memory://proposal-1",
                "state": "proposed",
                "summary": "Remember failed execution evidence.",
            },
            {
                "proposalRef": "memory://proposal-2",
                "state": "rejected",
                "summary": "Rejected noisy suggestion.",
            },
        ],
    )

    assert retrieval.query == "execution context bundle"
    assert retrieval.index_version == "rag-index-1"
    assert retrieval.returned_refs == ["artifact://doc-1"]
    assert retrieval.compact_summaries == ["Relevant source design section."]
    assert retrieval.retrieval_manifest_ref.startswith(
        "attempt-retrieval-manifest://sha256:"
    )
    assert memory.proposals[0].state == "proposed"
    assert memory.proposals[1].state == "rejected"
    assert memory.memory_manifest_ref.startswith("attempt-memory-manifest://sha256:")
    assert bundle.retrieval_manifest_ref == retrieval.retrieval_manifest_ref
    assert bundle.memory_manifest_ref == memory.memory_manifest_ref


def test_execution_context_records_budgeted_memory_context_ref() -> None:
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        memory_context={
            "tokenBudget": 32,
            "candidates": [
                {
                    "text": "Prefer the proven fix pattern for this error.",
                    "source": "fix-pattern://signature-1",
                    "plane": "history",
                    "trustClass": "derived",
                    "provenance": {
                        "workflowId": "workflow-previous",
                        "artifactRefs": ["artifact://fix-pattern-1"],
                    },
                    "recency": "2026-06-01T12:00:00Z",
                    "tokenCost": 10,
                }
            ],
        },
    )

    assert bundle.memory_context_ref is not None
    assert bundle.memory_context_ref.startswith("memory-context-pack://sha256:")
    assert bundle.to_manifest_projection()["context"]["memoryContextRef"] == (
        bundle.memory_context_ref
    )


def test_execution_context_uses_configured_default_memory_context_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, int] = {}

    def fake_build_memory_context_pack(
        candidates: object,
        *,
        token_budget: int,
    ) -> object:
        observed["token_budget"] = token_budget

        class Pack:
            memory_context_ref = "memory-context-pack://sha256:configured"

        return Pack()

    monkeypatch.setattr(
        "moonmind.workflows.executions.prepared_context.settings.workflow."
        "memory_context_budget_tokens",
        1234,
    )
    monkeypatch.setattr(
        "moonmind.workflows.executions.prepared_context.build_memory_context_pack",
        fake_build_memory_context_pack,
    )

    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        memory_context={"candidates": []},
    )

    assert observed == {"token_budget": 1234}
    assert bundle.memory_context_ref == "memory-context-pack://sha256:configured"


def test_execution_context_rejects_zero_memory_context_budget() -> None:
    with pytest.raises(ValueError, match="token_budget must be positive"):
        build_execution_context_bundle(
            workflow_id="workflow-1",
            run_id="run-1",
            logical_step_id="collect-evidence",
            memory_context={
                "tokenBudget": 0,
                "candidates": [],
            },
        )


def test_execution_context_projects_fix_patterns_into_memory_manifest() -> None:
    signature = extract_error_signature("RuntimeError: missing qdrant collection")
    assert signature is not None
    fix_pattern = FixPattern.from_successful_run(
        signature=signature,
        summary="Create the Qdrant collection before indexing.",
        steps=["Run namespace bootstrap before the retrieval write."],
        evidence=EvidenceRun(workflowId="workflow-1", outcome="succeeded"),
    )
    expected_memory = build_memory_manifest(
        [
            {
                "proposalRef": fix_pattern.pattern_ref,
                "state": "accepted_for_run_context",
                "summary": (
                    "Create the Qdrant collection before indexing. Steps: "
                    "Run namespace bootstrap before the retrieval write."
                ),
            }
        ]
    )

    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        fix_patterns=[fix_pattern.model_dump(by_alias=True)],
    )

    assert bundle.memory_manifest_ref == expected_memory.memory_manifest_ref


def test_retrieval_manifest_accepts_documented_retrieved_refs_key() -> None:
    retrieval = build_retrieval_manifest(
        {
            "retrievedRefs": ["artifact://doc-1", "artifact://doc-2"],
        }
    )

    assert retrieval.returned_refs == ["artifact://doc-1", "artifact://doc-2"]
    assert retrieval.retrieval_manifest_ref.startswith(
        "attempt-retrieval-manifest://sha256:"
    )


def test_retrieval_manifest_allows_literal_assignment_search_terms() -> None:
    retrieval = build_retrieval_manifest(
        {
            "query": "find `password=` assignments and token= examples",
            "indexVersion": "rag-index-1",
        }
    )

    assert retrieval.query == "find `password=` assignments and token= examples"


def test_execution_context_rejects_secretish_values_and_unknown_memory_states() -> None:
    with pytest.raises(ValueError, match="raw secret material"):
        build_prepared_input_manifest(
            {
                "inputAttachments": [
                    {
                        "artifactId": "ghp_unsafe",
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="raw secret material"):
        build_execution_context_bundle(
            workflow_id="workflow-1",
            run_id="run-1",
            logical_step_id="collect-evidence",
            retrieval={
                "query": "ghp_unsafe",
                "indexVersion": "rag-index-1",
            },
        )

    with pytest.raises(ValidationError, match="Input should be"):
        build_memory_manifest(
            [
                {
                    "proposalRef": "memory://proposal-1",
                    "state": "auto_promoted",
                }
            ]
        )

    with pytest.raises(ValueError, match="policyRef"):
        build_memory_manifest(
            [
                {
                    "proposalRef": "memory://proposal-1",
                    "state": "applied_to_repo",
                }
            ]
        )


def test_recovery_prepared_artifact_refs_are_compact_and_deduped() -> None:
    manifest = build_prepared_input_manifest(_task_payload())

    refs = build_recovery_prepared_artifact_refs(manifest)

    assert refs == [
        "prepared-context://objective/artifact-objective",
        "artifact://artifact-objective",
        "prepared-context://steps/collect-evidence/artifact-step-1",
        "artifact://artifact-step-1",
        "prepared-context://steps/write-report/artifact-step-2",
        "artifact://artifact-step-2",
    ]
    assert "data:image" not in str(refs)


def test_prepare_failure_payload_is_bounded() -> None:
    failure = PreparedContextFailure.from_exception(
        RuntimeError("data:image/png;base64,AAAA " + "x" * 400),
        logical_step_id="collect-evidence",
        manifest_ref="prepared-context-manifest://task-inputs",
    )

    dumped = failure.model_dump(by_alias=True)

    assert dumped["logicalStepId"] == "collect-evidence"
    assert dumped["manifestRef"] == "prepared-context-manifest://task-inputs"
    assert dumped["reason"] == "RuntimeError"
    assert "data:image" not in dumped["message"]
    assert len(dumped["message"]) <= 180



def test_phase8_context_bundle_includes_full_launch_context_fields() -> None:
    bundle = build_execution_context_bundle(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=2,
        reason="reattempt",
        task_input_snapshot_ref="artifact://task-input-snapshot",
        plan_ref="artifact://plan",
        plan_digest="sha256:plan",
        workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        workspace_baseline={"kind": "git_commit", "commit": "abc123"},
        checkpoint_refs={"sourceCheckpointRef": "artifact://checkpoint-1"},
        prior_evidence_refs=["artifact://attempt-1-gate"],
        provenance={"source": "reattempt", "sourceCheckpointRef": "artifact://checkpoint-1"},
        runtime_selection={"runtimeId": "codex_cli", "model": "gpt-5"},
        quality_gate_profile="repo-default",
        provider_lease_refs=[{"leaseRef": "lease://profile/slot", "status": "acquired"}],
        skill_projection_state_refs=[{"resolvedSkillsetRef": "artifact://skillset"}],
        diagnostic_refs={"ghcrAuth": {"authMode": "anonymous", "diagnosticRefs": ["artifact://ghcr-diag"]}},
        correlation_refs={"traceRef": "trace://workflow-1", "logRef": "artifact://logs", "costRef": "cost://estimate"},
        retrieval={"state": "disabled", "reason": "retrieval_disabled_by_policy"},
    )

    dumped = bundle.model_dump(by_alias=True, exclude_none=True)

    assert dumped["schemaVersion"] == "v1"
    assert dumped["taskInputSnapshotRef"] == "artifact://task-input-snapshot"
    assert dumped["planRef"] == "artifact://plan"
    assert dumped["planDigest"] == "sha256:plan"
    assert dumped["workspacePolicy"] == "apply_previous_execution_diff_to_clean_baseline"
    assert dumped["workspaceBaseline"] == {"kind": "git_commit", "commit": "abc123"}
    assert dumped["checkpointRefs"] == {"sourceCheckpointRef": "artifact://checkpoint-1"}
    assert dumped["priorEvidenceRefs"] == ["artifact://attempt-1-gate"]
    assert dumped["provenance"]["source"] == "reattempt"
    assert dumped["qualityGateProfile"] == "repo-default"
    assert dumped["providerLeaseRefs"] == [{"leaseRef": "lease://profile/slot", "status": "acquired"}]
    assert dumped["skillProjectionStateRefs"] == [{"resolvedSkillsetRef": "artifact://skillset"}]
    assert dumped["diagnosticRefs"]["ghcrAuth"]["authMode"] == "anonymous"
    assert dumped["correlationRefs"]["traceRef"] == "trace://workflow-1"
    assert dumped["retrievalManifestRef"].startswith("attempt-retrieval-manifest://sha256:")
    assert dumped["builderVersion"] == "execution-context-builder-v2"
    assert dumped["contextBundleRef"] == f"execution-context-bundle://{dumped['contextBundleDigest']}"


def test_phase8_retrieval_manifest_supports_all_explicit_states() -> None:
    available = build_retrieval_manifest(
        {
            "state": "available",
            "query": "step scoped context",
            "selector": {"logicalStepId": "collect-evidence"},
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://context-pack-1"],
            "filters": {"trust": "canonical"},
            "exclusions": [{"ref": "artifact://noisy", "reason": "filtered"}],
            "compactSummaries": ["Bounded source summary."],
            "correlationRefs": {"traceRef": "trace://retrieval"},
        }
    )
    assert available.state == "available"
    assert available.exclusions == [{"ref": "artifact://noisy", "reason": "filtered"}]
    assert available.correlation_refs == {"traceRef": "trace://retrieval"}

    for state in ("disabled", "skipped", "unavailable"):
        manifest = build_retrieval_manifest({"state": state, "reason": f"{state}_reason"})
        assert manifest.state == state
        assert manifest.reason == f"{state}_reason"
        assert manifest.returned_refs == []

    empty = build_retrieval_manifest(
        {
            "state": "empty",
            "query": "step scoped context",
            "indexVersion": "rag-index-1",
            "filters": {"trust": "canonical"},
            "exclusions": [{"reason": "no refs after filters"}],
            "reason": "no_usable_refs",
        }
    )
    assert empty.state == "empty"
    assert empty.returned_refs == []
    assert empty.reason == "no_usable_refs"


def test_phase8_context_digest_changes_for_launch_visible_inputs() -> None:
    base_kwargs = dict(
        workflow_id="workflow-1",
        run_id="run-1",
        logical_step_id="collect-evidence",
        execution_ordinal=1,
        workspace_baseline={"kind": "git_commit", "commit": "abc123"},
        prior_evidence_refs=["artifact://attempt-1"],
        runtime_selection={"runtimeId": "codex_cli", "model": "gpt-5"},
        retrieval={
            "state": "available",
            "query": "context",
            "returnedRefs": ["artifact://doc-1"],
            "exclusions": [{"ref": "artifact://doc-2", "reason": "filtered"}],
        },
    )
    base = build_execution_context_bundle(**base_kwargs)

    variants = [
        {**base_kwargs, "workspace_baseline": {"kind": "git_commit", "commit": "def456"}},
        {**base_kwargs, "prior_evidence_refs": ["artifact://attempt-2"]},
        {**base_kwargs, "runtime_selection": {"runtimeId": "claude_code", "model": "sonnet"}},
        {**base_kwargs, "retrieval": {**base_kwargs["retrieval"], "returnedRefs": ["artifact://doc-3"]}},
        {**base_kwargs, "retrieval": {**base_kwargs["retrieval"], "exclusions": [{"ref": "artifact://doc-9", "reason": "filtered"}]}},
        {**base_kwargs, "retrieval": {**base_kwargs["retrieval"], "selector": {"topic": "changed"}}},
    ]

    assert {build_execution_context_bundle(**variant).context_bundle_digest for variant in variants} == {
        build_execution_context_bundle(**variant).context_bundle_digest for variant in variants
    }
    assert all(build_execution_context_bundle(**variant).context_bundle_digest != base.context_bundle_digest for variant in variants)


def test_phase8_ref_only_fields_reject_raw_payloads_and_credentials() -> None:
    forbidden_contexts = [
        {"provider_lease_refs": [{"leaseRef": "lease://profile", "token": "ghp_unsafe"}]},
        {"diagnostic_refs": {"sidecar": {"stdout": "raw log output"}}},
        {"diagnostic_refs": {"preflight": {"diff": "diff --git a/file b/file"}}},
        {"skill_projection_state_refs": [{"materializedState": "inline workspace state"}]},
        {"correlation_refs": {"log": "x" * 5000}},
    ]
    for extra in forbidden_contexts:
        with pytest.raises(ValueError):
            build_execution_context_bundle(
                workflow_id="workflow-1",
                run_id="run-1",
                logical_step_id="collect-evidence",
                **extra,
            )

    with pytest.raises(ValueError):
        build_retrieval_manifest(
            {
                "state": "available",
                "query": "context",
                "returnedRefs": ["artifact://doc-1"],
                "compactSummaries": ["raw stdout: " + "x" * 5000],
            }
        )
