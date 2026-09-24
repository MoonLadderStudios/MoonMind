"""Trusted GitLab integration package.

Import concrete GitLab primitives from their owning modules to avoid
package-level cycles, mirroring the Jira integration package.
"""

from moonmind.integrations.gitlab.adapter import GitLabMRAdapter
from moonmind.integrations.gitlab.client import (
    GitLabClient,
    ResolvedGitLabConnection,
    build_gitlab_connection,
    connection_from_bound_credential,
)
from moonmind.integrations.gitlab.errors import (
    GitLabCapabilityUnavailable,
    GitLabIdentityError,
    GitLabTokenExpiredError,
    GitLabToolError,
)
from moonmind.integrations.gitlab.identity import (
    GitLabMRRef,
    GitLabProjectIdentity,
    resolve_gitlab_identity,
)

__all__ = [
    "GitLabCapabilityUnavailable",
    "GitLabClient",
    "GitLabIdentityError",
    "GitLabMRAdapter",
    "GitLabMRRef",
    "GitLabProjectIdentity",
    "GitLabTokenExpiredError",
    "GitLabToolError",
    "ResolvedGitLabConnection",
    "build_gitlab_connection",
    "connection_from_bound_credential",
    "resolve_gitlab_identity",
]
