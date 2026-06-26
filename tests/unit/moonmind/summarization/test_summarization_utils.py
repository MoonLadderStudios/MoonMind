import unittest
from unittest.mock import MagicMock, mock_open, patch

from moonmind.summarization.summarization import summarize_text_gemini, update_summaries

class TestSummarizeTextGemini(unittest.TestCase):
    def setUp(self):
        self.mock_model = MagicMock()
        self.base_prompt = "Summarize this:"
        self.input_text = "This is a long text to summarize."

    def test_happy_path_with_candidates(self):
        mock_response = MagicMock()
        mock_response.prompt_feedback.block_reason = None

        mock_part = MagicMock()
        mock_part.text = "This is the summary."

        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]

        mock_response.candidates = [mock_candidate]
        self.mock_model.generate_content.return_value = mock_response

        result = summarize_text_gemini(
            self.base_prompt, self.input_text, self.mock_model
        )

        self.assertEqual(result, "This is the summary.")
        self.mock_model.generate_content.assert_called_once_with(
            "Summarize this:\n\nThis is a long text to summarize."
        )

    def test_blocked_content(self):
        mock_response = MagicMock()
        mock_response.prompt_feedback.block_reason = "SAFETY"
        self.mock_model.generate_content.return_value = mock_response

        result = summarize_text_gemini(
            self.base_prompt, self.input_text, self.mock_model
        )

        self.assertIsNone(result)

    def test_fallback_to_text_attribute(self):
        mock_response = MagicMock()
        mock_response.prompt_feedback.block_reason = None
        mock_response.candidates = []
        mock_response.text = "Fallback summary text."
        self.mock_model.generate_content.return_value = mock_response

        result = summarize_text_gemini(
            self.base_prompt, self.input_text, self.mock_model
        )

        self.assertEqual(result, "Fallback summary text.")

    def test_no_parsable_text(self):
        mock_response = MagicMock()
        mock_response.prompt_feedback.block_reason = None
        mock_response.candidates = []
        mock_response.text = None
        self.mock_model.generate_content.return_value = mock_response

        result = summarize_text_gemini(
            self.base_prompt, self.input_text, self.mock_model
        )

        self.assertIsNone(result)

    def test_exception_during_generation(self):
        self.mock_model.generate_content.side_effect = Exception("API Error")

        result = summarize_text_gemini(
            self.base_prompt, self.input_text, self.mock_model
        )

        self.assertIsNone(result)

class TestUpdateSummaries(unittest.TestCase):
    def setUp(self):
        self.mock_model_factory = MagicMock()
        self.mock_model = MagicMock()
        self.mock_model_factory.return_value = self.mock_model

        self.mock_text_summarizer = MagicMock()
        self.mock_text_summarizer.return_value = "Summary of the input text."

        self.input_dir = "/input"
        self.output_dir = "/output"
        self.prompt_file_path = "/prompt.txt"

    @patch("moonmind.summarization.summarization.os.makedirs")
    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    @patch("moonmind.summarization.summarization.time.sleep")
    @patch("builtins.open", new_callable=mock_open)
    def test_happy_path(
        self,
        mock_file_open,
        mock_sleep,
        mock_read_text_file,
        mock_find_files,
        mock_exists,
        mock_makedirs,
    ):
        mock_read_text_file.side_effect = ["Base prompt text", "Input text content"]
        mock_find_files.return_value = ["/input/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return False
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return False
            return True

        mock_exists.side_effect = exists_side_effect

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
            request_delay=0,
        )

        self.mock_model_factory.assert_called_once()
        mock_read_text_file.assert_any_call("/prompt.txt")
        mock_read_text_file.assert_any_call("/input/file1.copy", safe_base_dir="/input")
        mock_find_files.assert_called_once_with(
            search_directory="/input", target_extension=".copy"
        )
        self.mock_text_summarizer.assert_called_once_with(
            "Base prompt text", "Input text content", self.mock_model
        )

        mock_file_open.assert_called_once_with(
            "/output/file1.rst", "w", encoding="utf-8"
        )
        mock_file_open().write.assert_called_once_with("Summary of the input text.")
        mock_sleep.assert_called_once_with(0)

    @patch("moonmind.summarization.summarization.read_text_file")
    def test_model_factory_fails(self, mock_read_text_file):
        self.mock_model_factory.side_effect = Exception("Model initialization failed")

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        mock_read_text_file.assert_not_called()

    @patch("moonmind.summarization.summarization.read_text_file")
    def test_prompt_file_missing(self, mock_read_text_file):
        mock_read_text_file.side_effect = FileNotFoundError()

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.read_text_file")
    def test_prompt_file_empty(self, mock_read_text_file):
        mock_read_text_file.return_value = ""

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.read_text_file")
    def test_prompt_file_ioerror(self, mock_read_text_file):
        mock_read_text_file.side_effect = IOError("Disk error")

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.read_text_file")
    def test_prompt_file_unexpected_error(self, mock_read_text_file):
        mock_read_text_file.side_effect = Exception("Unexpected error")

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.makedirs")
    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_makedirs_fails(self, mock_read_text_file, mock_exists, mock_makedirs):
        mock_read_text_file.return_value = "Base prompt text"
        mock_exists.return_value = False
        mock_makedirs.side_effect = OSError("Permission denied")

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_makedirs_for_output_file_fails(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.return_value = "Base prompt text"
        mock_find_files.return_value = ["/input/subdir/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/subdir/file1.rst":
                return False
            if path == "/output/subdir":
                return False
            return True

        mock_exists.side_effect = exists_side_effect

        with patch("moonmind.summarization.summarization.os.makedirs") as mock_makedirs:
            mock_makedirs.side_effect = OSError("Cannot create subdir")
            update_summaries(
                self.input_dir,
                self.output_dir,
                self.prompt_file_path,
                self.mock_model_factory,
                self.mock_text_summarizer,
                replace_existing=False,
            )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_skip_existing_file(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.side_effect = ["Base prompt text", "Input text content"]
        mock_find_files.return_value = ["/input/file1.copy"]

        mock_exists.return_value = True

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
            replace_existing=False,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_input_text_empty(self, mock_read_text_file, mock_find_files, mock_exists):
        mock_read_text_file.side_effect = ["Base prompt text", ""]
        mock_find_files.return_value = ["/input/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return True
            return True

        mock_exists.side_effect = exists_side_effect

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )

        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_text_summarizer_returns_none(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.side_effect = ["Base prompt text", "Input text content"]
        mock_find_files.return_value = ["/input/file1.copy"]
        self.mock_text_summarizer.return_value = None

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return True
            return True

        mock_exists.side_effect = exists_side_effect

        with patch("builtins.open", new_callable=mock_open) as mock_file_open:
            update_summaries(
                self.input_dir,
                self.output_dir,
                self.prompt_file_path,
                self.mock_model_factory,
                self.mock_text_summarizer,
            )
            mock_file_open.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_output_write_fails(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.side_effect = ["Base prompt text", "Input text content"]
        mock_find_files.return_value = ["/input/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return True
            return True

        mock_exists.side_effect = exists_side_effect

        with patch("builtins.open", new_callable=mock_open) as mock_file_open:
            mock_file_open.side_effect = IOError("Cannot write output file")
            with patch("moonmind.summarization.summarization.time.sleep") as mock_sleep:
                update_summaries(
                    self.input_dir,
                    self.output_dir,
                    self.prompt_file_path,
                    self.mock_model_factory,
                    self.mock_text_summarizer,
                )
                mock_sleep.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_input_file_not_found(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.side_effect = ["Base prompt text", FileNotFoundError()]
        mock_find_files.return_value = ["/input/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return True
            return True

        mock_exists.side_effect = exists_side_effect

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )
        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_input_file_ioerror(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.side_effect = ["Base prompt text", IOError()]
        mock_find_files.return_value = ["/input/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return True
            return True

        mock_exists.side_effect = exists_side_effect

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )
        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    @patch("moonmind.summarization.summarization.read_text_file")
    def test_input_file_unexpected_error(
        self, mock_read_text_file, mock_find_files, mock_exists
    ):
        mock_read_text_file.side_effect = ["Base prompt text", Exception()]
        mock_find_files.return_value = ["/input/file1.copy"]

        def exists_side_effect(path):
            if path == "/output":
                return True
            if path == "/output/file1.rst":
                return False
            if path == "/output/":
                return True
            return True

        mock_exists.side_effect = exists_side_effect

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )
        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.read_text_file")
    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    def test_input_dir_not_found(
        self, mock_find_files, mock_exists, mock_read_text_file
    ):
        mock_read_text_file.return_value = "Base prompt text"
        mock_exists.return_value = True
        mock_find_files.side_effect = FileNotFoundError()

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )
        self.mock_text_summarizer.assert_not_called()

    @patch("moonmind.summarization.summarization.read_text_file")
    @patch("moonmind.summarization.summarization.os.path.exists")
    @patch("moonmind.summarization.summarization.find_files")
    def test_find_files_unexpected_error(
        self, mock_find_files, mock_exists, mock_read_text_file
    ):
        mock_read_text_file.return_value = "Base prompt text"
        mock_exists.return_value = True
        mock_find_files.side_effect = Exception()

        update_summaries(
            self.input_dir,
            self.output_dir,
            self.prompt_file_path,
            self.mock_model_factory,
            self.mock_text_summarizer,
        )
        self.mock_text_summarizer.assert_not_called()

if __name__ == "__main__":
    unittest.main()
