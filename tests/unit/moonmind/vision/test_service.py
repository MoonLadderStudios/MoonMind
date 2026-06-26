from pathlib import Path
from unittest.mock import patch

import pytest

from moonmind.vision.service import (
    AttachmentContextInput,
    VisionContextTargetInput,
    VisionContextStatus,
    VisionService,
)
from moonmind.vision.settings import VisionConfig

@pytest.fixture
def base_config():
    return VisionConfig(
        enabled=True,
        provider="gemini_cli",
        model="gemini-1.5-flash",
        max_tokens=4000,
        ocr_enabled=True,
    )

@pytest.fixture
def sample_attachment():
    return AttachmentContextInput(
        id="att-123",
        filename="test_image.png",
        content_type="image/png",
        size_bytes=1024,
        digest="sha256:abcd",
        local_path="/tmp/test_image.png",
        user_caption_hint=None,
    )

def test_render_context_no_attachments(base_config):
    service = VisionService(config=base_config)
    context = service.render_context([])

    assert not context.enabled
    assert context.status is VisionContextStatus.NO_ATTACHMENTS
    assert not context.attachments
    assert "IMAGE ATTACHMENTS (0)" in context.markdown
    assert "No attachments were provided" in context.markdown

def test_render_context_disabled_flag(base_config, sample_attachment):
    disabled_config = VisionConfig(
        enabled=False,
        provider="gemini_cli",
        model="gemini-1.5-flash",
        max_tokens=4000,
        ocr_enabled=True,
    )
    service = VisionService(config=disabled_config)
    context = service.render_context([sample_attachment])

    assert not context.enabled
    assert context.status is VisionContextStatus.DISABLED
    assert len(context.attachments) == 1
    assert context.attachments[0].description == "Vision context generation disabled."
    assert (
        "NOTE: Vision context generation is disabled via configuration."
        in context.markdown
    )

def test_render_context_provider_off(base_config, sample_attachment):
    off_config = VisionConfig(
        enabled=True,
        provider="off",
        model="gemini-1.5-flash",
        max_tokens=4000,
        ocr_enabled=True,
    )
    service = VisionService(config=off_config)
    context = service.render_context([sample_attachment])

    assert not context.enabled
    assert context.status is VisionContextStatus.DISABLED

@patch("moonmind.vision.service.settings")
def test_render_context_provider_unavailable(mock_settings, sample_attachment):
    # Mock all API keys to be empty
    mock_settings.google.google_api_key = ""
    mock_settings.openai.openai_api_key = ""
    mock_settings.anthropic.anthropic_api_key = ""

    for provider in ["gemini_cli", "openai", "anthropic", "unknown"]:
        config = VisionConfig(
            enabled=True,
            provider=provider,
            model="any",
            max_tokens=1000,
            ocr_enabled=True,
        )
        service = VisionService(config=config)
        context = service.render_context([sample_attachment])

        assert not context.enabled
        assert context.status is VisionContextStatus.PROVIDER_UNAVAILABLE
        assert len(context.attachments) == 1
        assert (
            "Vision provider credentials unavailable"
            in context.attachments[0].description
        )
        assert "NOTE: Vision provider credentials are unavailable" in context.markdown

@patch("moonmind.vision.service.settings")
def test_render_context_ok_gemini(mock_settings, sample_attachment):
    mock_settings.google.google_api_key = "fake_key"
    config = VisionConfig(
        enabled=True, provider="gemini_cli", model="any", max_tokens=1000, ocr_enabled=True
    )
    service = VisionService(config=config)
    context = service.render_context([sample_attachment])

    assert context.enabled
    assert context.status is VisionContextStatus.OK

@patch("moonmind.vision.service.settings")
def test_render_context_ok_openai(mock_settings, sample_attachment):
    mock_settings.openai.openai_api_key = "fake_key"
    config = VisionConfig(
        enabled=True, provider="openai", model="any", max_tokens=1000, ocr_enabled=True
    )
    service = VisionService(config=config)
    context = service.render_context([sample_attachment])

    assert context.enabled
    assert context.status is VisionContextStatus.OK

@patch("moonmind.vision.service.settings")
def test_render_context_ok_anthropic(mock_settings, sample_attachment):
    mock_settings.anthropic.anthropic_api_key = "fake_key"
    config = VisionConfig(
        enabled=True,
        provider="anthropic",
        model="any",
        max_tokens=1000,
        ocr_enabled=True,
    )
    service = VisionService(config=config)
    context = service.render_context([sample_attachment])

    assert context.enabled
    assert context.status is VisionContextStatus.OK

@patch("moonmind.vision.service.settings")
def test_render_context_attachment_parsing_and_markdown(
    mock_settings, base_config, sample_attachment
):
    mock_settings.google.google_api_key = "fake_key"

    # create a second attachment to test multiple attachments and formatting
    attachment2 = AttachmentContextInput(
        id="att-456",
        filename="no_digest.jpg",
        content_type=None,
        size_bytes=2048,
        digest=None,
        local_path="/tmp/no_digest.jpg",
        user_caption_hint="User specifically asked to check this chart.",
    )

    service = VisionService(config=base_config)
    context = service.render_context([sample_attachment, attachment2])

    assert context.enabled
    assert context.status is VisionContextStatus.OK
    assert len(context.attachments) == 2

    # Check first attachment metadata and default description
    att1 = context.attachments[0]
    assert att1.index == 1
    assert att1.filename == "test_image.png"
    assert att1.content_type == "image/png"
    assert att1.digest == "sha256:abcd"
    assert (
        att1.description
        == "Vision provider is enabled but automatic captions are pending."
    )
    assert att1.ocr_text == "OCR capture not available"

    # Check second attachment metadata and user hint description
    att2 = context.attachments[1]
    assert att2.index == 2
    assert att2.filename == "no_digest.jpg"
    assert att2.content_type is None
    assert att2.digest is None
    assert att2.description == "User specifically asked to check this chart."

    # Check markdown rendering
    md = context.markdown
    assert "IMAGE ATTACHMENTS (2):" in md
    assert "1) /tmp/test_image.png" in md
    assert "   - filename: test_image.png" in md
    assert "   - contentType: image/png" in md
    assert "   - digest: sha256:abcd" in md
    assert "   - description:" in md
    assert "     Vision provider is enabled but automatic captions are pending." in md

    assert "2) /tmp/no_digest.jpg" in md
    assert "   - filename: no_digest.jpg" in md
    assert (
        "contentType" not in md.split("2) /tmp/no_digest.jpg")[1]
    )  # content type omitted if None
    assert (
        " - digest:" not in md.split("2) /tmp/no_digest.jpg")[1]
    )  # digest omitted if None
    assert "     User specifically asked to check this chart." in md

@patch("moonmind.vision.service.settings")
def test_render_context_ocr_disabled(mock_settings, sample_attachment):
    mock_settings.google.google_api_key = "fake_key"
    no_ocr_config = VisionConfig(
        enabled=True, provider="gemini_cli", model="any", max_tokens=1000, ocr_enabled=False
    )
    service = VisionService(config=no_ocr_config)
    context = service.render_context([sample_attachment])

    assert context.attachments[0].ocr_text == "OCR disabled"
    assert "     OCR disabled" in context.markdown

def test_vision_service_default_config():
    with patch("moonmind.vision.service.get_vision_config") as mock_get_config:
        mock_get_config.return_value = VisionConfig(
            enabled=True, provider="test", model="test", max_tokens=10, ocr_enabled=True
        )
        service = VisionService()
        assert service._config.provider == "test"
        mock_get_config.assert_called_once()

@patch("moonmind.vision.service.settings")
def test_render_target_contexts_preserves_objective_and_step_targets(
    mock_settings, base_config
):
    mock_settings.google.google_api_key = "fake_key"
    objective_attachment = AttachmentContextInput(
        id="artifact-objective",
        filename="shared.png",
        content_type="image/png",
        size_bytes=100,
        digest="sha256:objective",
        local_path=".moonmind/inputs/objective/artifact-objective-shared.png",
    )
    step_attachment = AttachmentContextInput(
        id="artifact-step",
        filename="shared.png",
        content_type="image/png",
        size_bytes=200,
        digest="sha256:step",
        local_path=".moonmind/inputs/steps/review/artifact-step-shared.png",
    )
    service = VisionService(config=base_config)

    bundle = service.render_target_contexts(
        [
            VisionContextTargetInput.objective([objective_attachment]),
            VisionContextTargetInput.step("review", [step_attachment]),
        ]
    )

    assert bundle.index["generated"] is True
    assert [entry["contextPath"] for entry in bundle.index["targets"]] == [
        ".moonmind/vision/task/image_context.md",
        ".moonmind/vision/steps/review/image_context.md",
    ]
    assert bundle.index["targets"][0]["targetKind"] == "objective"
    assert bundle.index["targets"][0]["attachmentRefs"] == ["artifact-objective"]
    assert bundle.index["targets"][1]["targetKind"] == "step"
    assert bundle.index["targets"][1]["stepRef"] == "review"
    assert bundle.index["targets"][1]["attachmentRefs"] == ["artifact-step"]
    assert "artifact-objective" in bundle.artifacts[0].context.markdown
    assert "artifact-step" in bundle.artifacts[1].context.markdown
    assert [event["event"] for event in bundle.diagnostics] == [
        "image_context_generation_started",
        "image_context_generation_completed",
        "image_context_generation_started",
        "image_context_generation_completed",
    ]
    assert bundle.diagnostics[0] == {
        "event": "image_context_generation_started",
        "status": "started",
        "targetKind": "objective",
        "attachmentRefs": ["artifact-objective"],
        "sourcePaths": [
            ".moonmind/inputs/objective/artifact-objective-shared.png"
        ],
    }
    assert bundle.diagnostics[3]["targetKind"] == "step"
    assert bundle.diagnostics[3]["stepRef"] == "review"
    assert bundle.diagnostics[3]["contextPath"] == (
        ".moonmind/vision/steps/review/image_context.md"
    )

def test_render_target_contexts_disabled_records_status_and_traceability(
    base_config,
):
    disabled_config = VisionConfig(
        enabled=False,
        provider="gemini_cli",
        model="gemini-1.5-flash",
        max_tokens=4000,
        ocr_enabled=True,
    )
    attachment = AttachmentContextInput(
        id="artifact-disabled",
        filename="disabled.png",
        content_type="image/png",
        size_bytes=300,
        digest=None,
        local_path=".moonmind/inputs/objective/artifact-disabled-disabled.png",
    )
    service = VisionService(config=disabled_config)

    bundle = service.render_target_contexts(
        [VisionContextTargetInput.objective([attachment])]
    )

    assert bundle.index["generated"] is False
    target = bundle.index["targets"][0]
    assert target["status"] == VisionContextStatus.DISABLED.value
    assert target["attachmentRefs"] == ["artifact-disabled"]
    assert target["sourcePaths"] == [
        ".moonmind/inputs/objective/artifact-disabled-disabled.png"
    ]
    assert "Vision context generation disabled" in bundle.artifacts[0].context.markdown
    assert [event["event"] for event in bundle.diagnostics] == [
        "image_context_generation_started",
        "image_context_generation_disabled",
    ]
    assert bundle.diagnostics[1]["status"] == "disabled"
    assert bundle.diagnostics[1]["targetKind"] == "objective"
    assert bundle.diagnostics[1]["contextPath"] == (
        ".moonmind/vision/task/image_context.md"
    )

def test_render_target_contexts_sanitizes_step_ref_for_context_path(base_config):
    service = VisionService(config=base_config)
    bundle = service.render_target_contexts(
        [
            VisionContextTargetInput.step(
                "../Review Step!",
                [
                    AttachmentContextInput(
                        id="artifact-safe",
                        filename="safe.png",
                        content_type="image/png",
                        size_bytes=300,
                        digest=None,
                        local_path=".moonmind/inputs/steps/review/artifact-safe-safe.png",
                    )
                ],
            )
        ]
    )

    assert bundle.artifacts[0].context_path == Path(
        ".moonmind/vision/steps/review-step/image_context.md"
    )
    assert bundle.index["targets"][0]["stepRef"] == "../Review Step!"
    assert bundle.index["targets"][0]["contextPath"] == (
        ".moonmind/vision/steps/review-step/image_context.md"
    )
    assert bundle.diagnostics[1]["stepRef"] == "../Review Step!"
    assert bundle.diagnostics[1]["contextPath"] == (
        ".moonmind/vision/steps/review-step/image_context.md"
    )

@patch("moonmind.vision.service.settings")
def test_render_target_contexts_failed_diagnostics_for_provider_unavailable(
    mock_settings, base_config
):
    mock_settings.google.google_api_key = ""
    attachment = AttachmentContextInput(
        id="artifact-provider-missing",
        filename="missing-provider.png",
        content_type="image/png",
        size_bytes=300,
        digest=None,
        local_path=".moonmind/inputs/steps/review/artifact-provider-missing.png",
    )
    service = VisionService(config=base_config)

    bundle = service.render_target_contexts(
        [VisionContextTargetInput.step("review", [attachment])]
    )

    assert [event["event"] for event in bundle.diagnostics] == [
        "image_context_generation_started",
        "image_context_generation_failed",
    ]
    failed = bundle.diagnostics[1]
    assert failed["status"] == "failed"
    assert failed["targetKind"] == "step"
    assert failed["stepRef"] == "review"
    assert failed["attachmentRefs"] == ["artifact-provider-missing"]
    assert "provider credentials unavailable" in str(failed["error"])

def test_render_target_contexts_rejects_colliding_sanitized_step_refs(base_config):
    service = VisionService(config=base_config)
    attachment_a = AttachmentContextInput(
        id="artifact-a",
        filename="shared.png",
        content_type="image/png",
        size_bytes=300,
        digest=None,
        local_path=".moonmind/inputs/steps/a-b/artifact-a-shared.png",
    )
    attachment_b = AttachmentContextInput(
        id="artifact-b",
        filename="shared.png",
        content_type="image/png",
        size_bytes=300,
        digest=None,
        local_path=".moonmind/inputs/steps/a-b/artifact-b-shared.png",
    )

    with pytest.raises(ValueError, match="target path collision"):
        service.render_target_contexts(
            [
                VisionContextTargetInput.step("A B", [attachment_a]),
                VisionContextTargetInput.step("A-B", [attachment_b]),
            ]
        )

def test_render_target_contexts_rejects_unusable_step_ref_with_value(base_config):
    service = VisionService(config=base_config)
    attachment = AttachmentContextInput(
        id="artifact-unsafe",
        filename="unsafe.png",
        content_type="image/png",
        size_bytes=300,
        digest=None,
        local_path=".moonmind/inputs/steps/unsafe/artifact-unsafe-unsafe.png",
    )

    with pytest.raises(ValueError, match="got unusable value: '!!!'"):
        service.render_target_contexts(
            [VisionContextTargetInput.step("!!!", [attachment])]
        )
