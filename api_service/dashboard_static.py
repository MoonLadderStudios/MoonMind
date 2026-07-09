"""Static file cache policy for built dashboard assets."""

from __future__ import annotations

import os

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

DASHBOARD_HTML_CACHE_CONTROL = "no-store"
DASHBOARD_DIST_CACHE_CONTROL = "no-cache, must-revalidate"
DASHBOARD_IMMUTABLE_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class DashboardStaticFiles(StaticFiles):
    """Serve Vite dist files with cache headers matched to asset stability."""

    def file_response(
        self,
        full_path: os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        relative_path = self.get_path(scope).replace("\\", "/")
        if relative_path.startswith("assets/"):
            response.headers["Cache-Control"] = DASHBOARD_IMMUTABLE_ASSET_CACHE_CONTROL
        else:
            response.headers["Cache-Control"] = DASHBOARD_DIST_CACHE_CONTROL
        return response
