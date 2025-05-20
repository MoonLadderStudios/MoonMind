import logging
import os
import time

from moonmind.utils.find_files import find_files
from moonmind.utils.read_text_file import read_text_file

def summarize_text(base_prompt: str, input_text: str, model: any):
    logger = logging.getLogger(__name__)
    try:
        prompt = f"{base_prompt}\n\n{input_text}"
        response = model.generate_content(prompt)

        if response.prompt_feedback and response.prompt_feedback.block_reason:
            logger.warning(f"Gemini content generation was blocked. Reason: {response.prompt_feedback.block_reason}. Input (start): '{input_text[:100]}...'")
            return None

        response_text_content = None
        if response.candidates:
            first_candidate = response.candidates[0]
            if first_candidate.content and first_candidate.content.parts:
                text_parts = [part.text for part in first_candidate.content.parts if hasattr(part, 'text') and part.text]
                if text_parts:
                    response_text_content = "".join(text_parts)

        if not response_text_content and hasattr(response, 'text') and response.text:
             response_text_content = response.text

        if not response_text_content:
            logger.warning(f"Gemini API returned no parsable text content. Input (start): '{input_text[:100]}...'. Full response: {response}")
            return None

        return response_text_content.strip()

    except Exception as e:
        logger.exception(f"Error during text summarization (Gemini call or response processing): {e}. Input (start): '{input_text[:100]}...'")
        return None

def update_summaries(input_dir: str, output_dir: str, prompt_file_path: str, model_factory: callable, text_summarizer: callable, input_ext: str = ".copy", output_ext: str = ".rst", request_delay: int = 15, replace_existing: bool = False):
    logger = logging.getLogger(__name__) # Define logger for this function's scope or use a module-level one
    logger.info(f"Starting summary generation process for input_dir='{input_dir}', output_dir='{output_dir}', prompt_file='{prompt_file_path}', replace_existing={replace_existing}.")

    try:
        # Use the passed model_factory to get the model
        model = model_factory()
    except Exception as e:
        logger.exception(f"Failed to initialize model via model_factory: {e}. Aborting summary generation.")
        return

    try:
        base_prompt = read_text_file(prompt_file_path)
        if not base_prompt:
            logger.error(f"Failed to load base prompt or prompt is empty from {prompt_file_path}. Aborting summary generation.")
            return
    except FileNotFoundError:
        logger.error(f"Base prompt file not found at {prompt_file_path}. Aborting summary generation.")
        return
    except IOError as e:
        logger.error(f"IOError reading base prompt from {prompt_file_path}: {e}. Aborting summary generation.")
        return
    except Exception as e:
        logger.error(f"An unexpected error occurred while loading base prompt from {prompt_file_path}: {e}. Aborting summary generation.")
        return

    files_processed = 0
    files_summarized = 0
    files_skipped_existing_output = 0
    files_failed_input_read = 0
    files_failed_summarization = 0
    files_failed_output_write = 0
    files_failed_other = 0

    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"Created output directory: {output_dir}")
        except OSError as e:
            logger.error(f"Failed to create output directory {output_dir}: {e}. Aborting.")
            return

    logger.info(f"Searching for '{input_ext}' files in '{input_dir}'")
    try:
        for input_file_path in find_files(search_directory=input_dir, target_extension=input_ext):
            logger.info(f"Processing input file: {input_file_path}")
            files_processed += 1

            relative_path_from_input_dir = os.path.relpath(input_file_path, input_dir)
            output_file_name = relative_path_from_input_dir.replace(input_ext, output_ext)
            output_file_path = os.path.join(output_dir, output_file_name)

            output_file_dir = os.path.dirname(output_file_path)
            if not os.path.exists(output_file_dir):
                try:
                    os.makedirs(output_file_dir, exist_ok=True)
                    logger.info(f"Created subdirectory for output: {output_file_dir}")
                except OSError as e:
                    logger.error(f"Failed to create subdirectory {output_file_dir} for output file {output_file_path}: {e}. Skipping this file.")
                    files_failed_other += 1
                    continue

            if not replace_existing and os.path.exists(output_file_path):
                logger.info(f"Output file {output_file_path} already exists and replace_existing is False. Skipping.")
                files_skipped_existing_output += 1
                continue

            try:
                input_text_content = read_text_file(input_file_path)
                if not input_text_content:
                    logger.error(f"Failed to load input text or file is empty from {input_file_path}. Skipping summarization for this file.")
                    files_failed_input_read += 1
                    continue

                logger.info(f"Generating summary for {input_file_path} to be saved at {output_file_path}.")
                # Use the passed text_summarizer function
                summary_text = text_summarizer(base_prompt, input_text_content, model)

                if summary_text is None:
                    logger.error(f"Failed to generate summary for content from {input_file_path}. Skipping writing to {output_file_path}.")
                    files_failed_summarization += 1
                    continue

                try:
                    with open(output_file_path, 'w', encoding='utf-8') as f:
                        f.write(summary_text)
                    files_summarized += 1
                    logger.info(f"Successfully wrote summary to {output_file_path}.")
                except IOError as e_write:
                    logger.error(f"Failed to write summary to {output_file_path}: {e_write}")
                    files_failed_output_write += 1
                    continue

                logger.info(f"Waiting for {request_delay} seconds before next request.")
                time.sleep(request_delay)

            except FileNotFoundError as e_fnf:
                logger.error(f"Input file not found during processing {input_file_path}: {e_fnf}. Skipping.")
                files_failed_other += 1
            except IOError as e_io:
                logger.error(f"IOError during processing of input file {input_file_path}: {e_io}. Skipping.")
                files_failed_input_read +=1
            except Exception as e_inner:
                logger.exception(f"An unexpected error occurred processing input file {input_file_path}: {e_inner}. Skipping.")
                files_failed_other += 1
    except FileNotFoundError: # This will catch if input_dir itself is not found by find_files
        logger.error(f"The input directory {input_dir} was not found. Cannot process files.")
    except Exception as e_outer:
        logger.exception(f"An unexpected error occurred during the main file finding/processing loop: {e_outer}")

    logger.info("Summary generation process finished.")
    logger.info(f"Total input files encountered: {files_processed}")
    logger.info(f"  Summaries written/overwritten: {files_summarized}")
    logger.info(f"  Skipped (output file exists, replace_existing=False): {files_skipped_existing_output}")
    logger.info(f"  Failed (input read error): {files_failed_input_read}")
    logger.info(f"  Failed (summarization error): {files_failed_summarization}")
    logger.info(f"  Failed (output write error): {files_failed_output_write}")
    logger.info(f"  Failed (other errors): {files_failed_other}")
