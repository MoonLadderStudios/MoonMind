import io
import logging
import os


def insert_text_file(
    file_path, text, line_number, blank_lines_before=0, blank_lines_after=0
):
    logger = logging.getLogger(__name__)
    if not file_path:
        logger.debug("No file path provided for text.")
        return False
    try:
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            return False
        if not os.path.isfile(file_path):
            logger.warning(f"Path is not a file: {file_path}")
            return False

        # Read current content
        with open(file_path, "r", encoding="utf-8") as f:
            current_lines = f.readlines()

        # Determine the 0-indexed insertion point, clamped
        # line_number is 1-indexed from the user
        insert_index = max(0, min(line_number - 1, len(current_lines)))

        lines_to_insert_block = []

        # 1. Add blank lines before
        if blank_lines_before > 0:
            lines_to_insert_block.extend(["\n"] * blank_lines_before)

        # 2. Process and add the main text
        if text:  # Only process if text is non-empty string
            text_source_lines = io.StringIO(text).readlines()
            if text_source_lines:  # If reading text produced any lines
                # Ensure the text block itself ends with a newline if it contained content.
                if not text_source_lines[-1].endswith("\n"):
                    text_source_lines[-1] += "\n"
                lines_to_insert_block.extend(text_source_lines)

        # 3. Add blank lines after
        if blank_lines_after > 0:
            lines_to_insert_block.extend(["\n"] * blank_lines_after)

        # 4. Construct the new file content
        modified_content_lines = (
            current_lines[:insert_index]
            + lines_to_insert_block
            + current_lines[insert_index:]
        )

        # 5. Write the new content back to the file
        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(modified_content_lines)

        logger.info(
            f"Successfully inserted text in file: {file_path} at line {line_number}"
        )
        return True
    except Exception as e:
        logger.error(f"Error inserting text in file {file_path}: {e}")
        return False
