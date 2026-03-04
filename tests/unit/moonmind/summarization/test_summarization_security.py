from unittest.mock import patch

from moonmind.summarization.summarization import (
    DEFAULT_PROMPT_SAFE_BASE_DIR,
    update_summaries,
)


def test_update_summaries_uses_default_trusted_prompt_boundary(tmp_path):
    prompt_file_path = str(tmp_path / "outside" / "prompt.txt")
    with patch(
        "moonmind.summarization.summarization.read_text_file", return_value=None
    ) as read_text_file_mock:
        update_summaries(
            input_dir=str(tmp_path / "input"),
            output_dir=str(tmp_path / "output"),
            prompt_file_path=prompt_file_path,
            model_factory=lambda: object(),
            text_summarizer=lambda _base, _input, _model: "summary",
        )

        read_text_file_mock.assert_called_once_with(
            prompt_file_path, safe_base_dir=str(DEFAULT_PROMPT_SAFE_BASE_DIR)
        )


def test_update_summaries_uses_explicit_trusted_prompt_boundary(tmp_path):
    prompt_file_path = str(tmp_path / "outside" / "prompt.txt")
    trusted_prompt_dir = tmp_path / "trusted_prompts"
    trusted_prompt_dir.mkdir()
    with patch(
        "moonmind.summarization.summarization.read_text_file", return_value=None
    ) as read_text_file_mock:
        update_summaries(
            input_dir=str(tmp_path / "input"),
            output_dir=str(tmp_path / "output"),
            prompt_file_path=prompt_file_path,
            model_factory=lambda: object(),
            text_summarizer=lambda _base, _input, _model: "summary",
            prompt_safe_base_dir=str(trusted_prompt_dir),
        )

        read_text_file_mock.assert_called_once_with(
            prompt_file_path, safe_base_dir=str(trusted_prompt_dir)
        )
