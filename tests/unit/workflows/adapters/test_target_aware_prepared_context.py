from __future__ import annotations

from moonmind.workflows.tasks.prepared_context import (
    build_prepared_input_manifest,
    select_step_prepared_context,
)


def test_adapter_contract_consumes_selected_refs_without_broadening_targets() -> None:
    manifest = build_prepared_input_manifest(
        {
            "inputAttachments": [
                {"artifactId": "objective-doc", "contentType": "text/plain"}
            ],
            "steps": [
                {
                    "id": "first-step",
                    "inputAttachments": [{"artifactId": "first-step-image"}],
                },
                {
                    "id": "second-step",
                    "inputAttachments": [{"artifactId": "second-step-image"}],
                },
            ],
        }
    )

    context = select_step_prepared_context(manifest, logical_step_id="first-step")

    assert context.input_refs == [
        "prepared-context://objective/objective-doc",
        "prepared-context://steps/first-step/first-step-image",
        "artifact://objective-doc",
        "artifact://first-step-image",
    ]
    assert "second-step-image" not in str(context.to_metadata())
