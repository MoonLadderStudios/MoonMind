import json
import os
from typing import Dict, Optional

class ViteAssetResolver:
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self._manifest_cache: Optional[Dict] = None

    def get_manifest(self) -> Dict:
        if self._manifest_cache is None:
            try:
                with open(self.manifest_path, "r") as f:
                    self._manifest_cache = json.load(f)
            except FileNotFoundError:
                self._manifest_cache = {}
            except json.JSONDecodeError:
                self._manifest_cache = {}

        return self._manifest_cache

    def resolve_entrypoint(self, entrypoint: str) -> Dict:
        manifest = self.get_manifest()
        # The manifest keys are relative to the root defined in vite config, e.g., 'entrypoints/tasks-home.tsx'
        key = f"entrypoints/{entrypoint}.tsx"
        if key in manifest:
            return manifest[key]
        return {}

def ui_assets(entrypoint: str) -> str:
    # In full dev mode, we would proxy to vite
    # In transitional / production, read from manifest

    # Path is relative to where the server runs, assuming project root
    manifest_path = "api_service/static/task_dashboard/dist/.vite/manifest.json"
    resolver = ViteAssetResolver(manifest_path)
    asset_info = resolver.resolve_entrypoint(entrypoint)

    if not asset_info:
        return f"<!-- Vite manifest entry not found for {entrypoint} -->"

    js_file = asset_info.get("file")
    css_files = asset_info.get("css", [])

    tags = []

    if js_file:
        tags.append(f'<script type="module" crossorigin src="/static/task_dashboard/dist/{js_file}"></script>')

    for css_file in css_files:
        tags.append(f'<link rel="stylesheet" crossorigin href="/static/task_dashboard/dist/{css_file}">')

    return "\n".join(tags)
