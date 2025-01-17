from typing import Generator, Optional

from llama_index.readers.github import GithubClient, GithubRepositoryReader

from .base_connector import BaseConnector, BaseDocument


class GithubConnector(BaseConnector):
    """Connector for reading documents from GitHub repositories"""

    def __init__(
        self,
        github_token: str,
        owner: str,
        repo: str,
        branch: str = "main",
        filter_directories: Optional[tuple] = None,
        filter_file_extensions: Optional[tuple] = None,
        logger=None
    ):
        super().__init__(logger)
        self.github_client = GithubClient(github_token=github_token, verbose=False)
        self.owner = owner
        self.repo = repo
        self.branch = branch

        # Default file extensions to exclude
        default_exclude_extensions = (
            [".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".json", ".ipynb"],
            GithubRepositoryReader.FilterType.EXCLUDE,
        )

        self.filter_directories = filter_directories
        self.filter_file_extensions = filter_file_extensions or default_exclude_extensions

    def stream_documents(self, **kwargs) -> Generator[BaseDocument, None, None]:
        """Stream documents from the GitHub repository"""
        try:
            reader = GithubRepositoryReader(
                github_client=self.github_client,
                owner=self.owner,
                repo=self.repo,
                use_parser=False,
                verbose=False,
                filter_directories=self.filter_directories,
                filter_file_extensions=self.filter_file_extensions,
            )

            documents = reader.load_data(branch=self.branch)

            for count, doc in enumerate(documents):
                self._log_progress(count)

                # Create metadata with GitHub-specific information
                metadata = {
                    "source": f"github/{self.owner}/{self.repo}",
                    "file_path": doc.metadata.get("file_path", ""),
                    "repo": self.repo,
                    "owner": self.owner,
                    "branch": self.branch,
                }

                yield BaseDocument.from_llama_document(doc, source=metadata["source"])

        except Exception as e:
            self.logger.error(f"Error streaming documents from GitHub: {str(e)}")
            raise