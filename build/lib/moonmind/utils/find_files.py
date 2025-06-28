import os


def find_files(search_directory: str, target_extension: str):
    if not target_extension.startswith("."):
        target_extension = "." + target_extension
    target_extension = target_extension.lower()

    if not os.path.isdir(search_directory):
        raise FileNotFoundError(
            f"Error: The directory '{search_directory}' does not exist or is not a directory."
        )

    for dirpath, dirnames, filenames in os.walk(
        search_directory, topdown=True, onerror=None, followlinks=False
    ):
        for filename in filenames:
            _root, ext = os.path.splitext(filename)
            if ext.lower() == target_extension:
                full_path = os.path.join(dirpath, filename)
                yield full_path
