import json
import logging
import os

import toml  # For pyproject.toml or other toml files

# We might need find_files or similar from utils later
# from moonmind.utils.find_files import find_files

logger = logging.getLogger(__name__)


def summarize_repo_for_readme(
    repo_path: str,
    model_factory: callable,
    text_summarizer: callable,
    project_name_arg: str = None,
    main_language_arg: str = None,
) -> str:
    """
    Generates a README content string for a given code repository.
    Aims to provide context for coding agents and follows modern summarization strategies.
    """
    logger.info(f"Starting README generation for repository at: {repo_path}")

    project_name = project_name_arg if project_name_arg else os.path.basename(repo_path)
    detected_language = main_language_arg  # Prioritize argument
    tech_stack = []
    top_level_files = []
    top_level_dirs = []
    dependencies = []
    all_files_detail = []  # For deeper analysis if needed later

    if main_language_arg:
        tech_stack.append(main_language_arg)

    logger.info(f"Project name set to: {project_name}")
    logger.info(f"Main language (from arg): {main_language_arg}")

    try:
        for item in os.listdir(repo_path):
            item_path = os.path.join(repo_path, item)
            if os.path.isfile(item_path):
                top_level_files.append(item)
                _, ext = os.path.splitext(item)
                ext = ext.lower()
                if item == "requirements.txt":
                    tech_stack.append("Python")
                    try:
                        with open(item_path, "r") as f:
                            dependencies.extend(
                                [
                                    line.strip()
                                    for line in f
                                    if line.strip() and not line.startswith("#")
                                ]
                            )
                        logger.info(f"Parsed dependencies from {item}")
                    except Exception as e:
                        logger.warning(f"Could not read {item}: {e}")
                elif item == "pyproject.toml":
                    tech_stack.append("Python")
                    try:
                        with open(item_path, "r") as f:
                            toml_data = toml.load(f)
                        if (
                            "project" in toml_data
                            and "dependencies" in toml_data["project"]
                        ):
                            dependencies.extend(toml_data["project"]["dependencies"])
                        elif (
                            "tool" in toml_data
                            and "poetry" in toml_data["tool"]
                            and "dependencies" in toml_data["tool"]["poetry"]
                        ):
                            # Poetry dependencies can be a dict, we only want keys
                            deps = toml_data["tool"]["poetry"]["dependencies"]
                            dependencies.extend(
                                [dep for dep in deps.keys() if dep.lower() != "python"]
                            )  # Exclude 'python' itself
                        logger.info(f"Parsed dependencies from {item}")
                    except Exception as e:
                        logger.warning(f"Could not parse {item}: {e}")
                elif item == "package.json":
                    tech_stack.append("JavaScript/TypeScript")  # Could be either
                    try:
                        with open(item_path, "r") as f:
                            pkg_data = json.load(f)
                        if "dependencies" in pkg_data:
                            dependencies.extend(pkg_data["dependencies"].keys())
                        if "devDependencies" in pkg_data:
                            dependencies.extend(pkg_data["devDependencies"].keys())
                        logger.info(f"Parsed dependencies from {item}")
                    except Exception as e:
                        logger.warning(f"Could not parse {item}: {e}")
                elif item == "pom.xml" or ext == ".java":
                    tech_stack.append("Java")
                elif item == "Cargo.toml" or ext == ".rs":
                    tech_stack.append("Rust")
                elif item == "go.mod" or ext == ".go":
                    tech_stack.append("Go")
                elif ext == ".py":
                    tech_stack.append("Python")
                elif ext in [".js", ".ts"]:
                    tech_stack.append("JavaScript/TypeScript")

            elif os.path.isdir(item_path):
                if item not in [
                    ".git",
                    ".hg",
                    ".svn",
                    "__pycache__",
                    "node_modules",
                    "target",
                    "build",
                    "dist",
                    ".vscode",
                    ".idea",
                ]:  # common ignores
                    top_level_dirs.append(item)

        # Simple language detection if not provided
        if not detected_language and tech_stack:
            # Basic heuristic: most frequent or first one related to known languages
            # This can be improved significantly
            lang_counts = {}
            for tech in tech_stack:
                if tech in ["Python", "JavaScript/TypeScript", "Java", "Rust", "Go"]:
                    lang_counts[tech] = lang_counts.get(tech, 0) + 1
            if lang_counts:
                detected_language = max(lang_counts, key=lang_counts.get)
        logger.info(f"Detected main language: {detected_language}")

        # TODO: Deeper scan for all_files_detail if necessary for more advanced analysis
        # for root, dirs, files in os.walk(repo_path):
        #     # Add filtering for common ignore patterns here too
        #     dirs[:] = [d for d in dirs if d not in ['.git', '.hg', '.svn', '__pycache__', 'node_modules', 'target', 'build', 'dist', '.vscode', '.idea']]
        #     for file in files:
        #         # Add filtering for file extensions if needed
        #         all_files_detail.append(os.path.join(root, file))

    except Exception as e:
        logger.exception(f"Error during repository analysis: {e}")

    repo_info = {
        "project_name": project_name,
        "main_language": detected_language or "Undetermined",  # Fallback
        "tech_stack": list(set(tech_stack)),  # Unique items
        "top_level_files": top_level_files,
        "top_level_dirs": top_level_dirs,
        "dependencies": list(set(dependencies)),  # Unique items
        "all_files_detail": all_files_detail,  # To be populated by os.walk later
    }

    logger.info(
        f"Repo info gathered: {json.dumps(repo_info, indent=2, ensure_ascii=False)}"
    )  # ensure_ascii for broader char support in logs

    readme_sections = []
    model = None
    try:
        model = model_factory()
    except Exception as e:
        logger.exception(
            f"Failed to initialize model via model_factory: {e}. Some summarization may not be available."
        )
        # Proceeding without a model means text_summarizer calls will likely fail or use a fallback if designed that way.

    # 1. Project Title Section
    readme_sections.append(f"# {repo_info['project_name']}")

    # 2. Overview/Purpose Section
    readme_sections.append("\n## Overview")
    existing_readme_content = None
    existing_readme_path = None
    readme_filenames = ["README.md", "readme.md", "README.txt", "README", "readme.txt"]
    for fname in readme_filenames:
        if fname in repo_info["top_level_files"]:
            try:
                potential_path = os.path.join(repo_path, fname)
                with open(potential_path, "r", encoding="utf-8", errors="ignore") as f:
                    # Read a snippet: first 20 lines or up to ~1000 chars to keep it manageable
                    from itertools import islice

                    lines = list(islice(f, 20))
                    existing_readme_content = "".join(lines)
                    if (
                        len(existing_readme_content) > 1000
                    ):  # Further truncate if very long lines
                        existing_readme_content = existing_readme_content[:1000] + "..."
                existing_readme_path = potential_path
                logger.info(
                    f"Found and read snippet from existing README: {existing_readme_path}"
                )
                break
            except Exception as e:
                logger.warning(f"Could not read existing README {fname}: {e}")

    purpose_summary_text = "Purpose of this project to be determined."  # Default
    if (
        model and text_summarizer
    ):  # Only attempt summarization if model and summarizer are available
        if existing_readme_content:
            overview_prompt = f"The following is the beginning of an existing README file for the project '{repo_info['project_name']}'. Summarize its main purpose concisely for a technical audience, focusing on what the project does. If it's too short or unclear to determine the purpose, state that 'The purpose is not clearly defined from this snippet.'"
            summary_attempt = text_summarizer(
                overview_prompt, existing_readme_content, model
            )
            if summary_attempt:
                purpose_summary_text = summary_attempt
            else:
                purpose_summary_text = "Could not summarize the existing README snippet. The purpose is not clearly defined from this snippet."
        else:
            # Try to infer from project name and key files
            # Construct a more informative input string for the summarizer
            context_for_llm = (
                f"Project Name: {repo_info['project_name']}\n"
                f"Main Language: {repo_info['main_language']}\n"
                f"Key Top-Level Files: {str(repo_info['top_level_files'][:5])}\n"  # First 5 files
                f"Key Top-Level Directories: {str(repo_info['top_level_dirs'][:3])}\n"  # First 3 dirs
            )
            overview_prompt = "Based on the following information about a software project, infer and describe its primary purpose in one or two sentences for a technical audience. If the purpose is unclear from this data, state that 'The purpose is not immediately obvious from the provided file and directory names.'"
            summary_attempt = text_summarizer(overview_prompt, context_for_llm, model)
            if summary_attempt:
                purpose_summary_text = summary_attempt
            else:
                purpose_summary_text = "The purpose is not immediately obvious from the provided file and directory names."
    readme_sections.append(purpose_summary_text)

    # 3. Tech Stack Section
    readme_sections.append("\n## Technology Stack")
    tech_stack_info = []
    if repo_info["main_language"] and repo_info["main_language"] != "Undetermined":
        tech_stack_info.append(f"- **Main Language:** {repo_info['main_language']}")

    unique_stack = sorted(
        list(
            set(
                item
                for item in repo_info["tech_stack"]
                if item.lower() != repo_info["main_language"].lower()
            )
        )
    )  # Avoid duplicating main lang
    if unique_stack:
        tech_stack_info.append("- **Other Technologies & Frameworks:**")
        for item in unique_stack:
            tech_stack_info.append(f"  - {item}")
    if (
        not tech_stack_info
    ):  # If main_language was undetermined and tech_stack was empty
        tech_stack_info.append(
            "Technology stack information is not readily available from the repository analysis."
        )
    readme_sections.extend(tech_stack_info)

    # 4. Directory Structure Section
    readme_sections.append("\n## Directory Structure")
    readme_sections.append("A brief look at the top-level directories and files:")
    if not repo_info["top_level_dirs"] and not repo_info["top_level_files"]:
        readme_sections.append(
            "- No top-level files or directories were identified (or all were ignored)."
        )
    else:
        for dir_name in sorted(repo_info["top_level_dirs"]):
            readme_sections.append(f"- `{dir_name}/`")
        for file_name in sorted(repo_info["top_level_files"]):
            readme_sections.append(f"- `{file_name}`")

    # 5. Setup and Usage Section
    readme_sections.append("\n## Setup and Usage")
    setup_instructions = []
    setup_files_found = []
    common_setup_files = [
        "INSTALL",
        "INSTALL.md",
        "CONTRIBUTING.md",
        "BUILD.md",
        "Makefile",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "Vagrantfile",
    ]
    for sfile in common_setup_files:
        if (
            sfile in repo_info["top_level_files"]
            or sfile.lower() in repo_info["top_level_files"]
        ):  # Check case-insensitively for some
            setup_files_found.append(sfile)

    if setup_files_found:
        setup_instructions.append(
            f"Refer to the following file(s) for setup and usage instructions: {', '.join(setup_files_found)}."
        )

    if (
        repo_info["main_language"] == "Python"
        and "requirements.txt" in repo_info["top_level_files"]
    ):
        setup_instructions.append(
            "To set up a Python environment, typically you would run: `pip install -r requirements.txt`"
        )
    elif (
        repo_info["main_language"] == "Python"
        and "pyproject.toml" in repo_info["top_level_files"]
    ):
        setup_instructions.append(
            "This Python project uses `pyproject.toml`. You might use Poetry (`poetry install`) or pip with build (`pip install .`) for setup."
        )

    if (
        repo_info["main_language"] == "JavaScript/TypeScript"
        and "package.json" in repo_info["top_level_files"]
    ):
        setup_instructions.append(
            "To install dependencies for this JavaScript/TypeScript project, typically you would run: `npm install` or `yarn install`."
        )

    if not setup_instructions:
        setup_instructions.append(
            "No specific setup instructions were automatically detected. Refer to standard practices for the identified technology stack or look for specific script files."
        )
    readme_sections.extend(setup_instructions)

    # 6. Dependencies Section (Optional)
    if repo_info["dependencies"]:
        readme_sections.append("\n## Key Dependencies")
        # List first 10-15 unique dependencies
        deps_to_list = sorted(list(set(repo_info["dependencies"])))[:15]
        for dep in deps_to_list:
            readme_sections.append(f"- {dep}")
        if len(repo_info["dependencies"]) > 15:
            readme_sections.append("- ... and more.")

    logger.info("Finished generating README sections.")
    return "\n\n".join(readme_sections)
