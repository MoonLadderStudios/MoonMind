import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class MissionControlUIAssetsError(Exception):
    """Mission Control cannot resolve Vite assets (strict mode; see MOONMIND_LENIENT_UI_ASSETS)."""


class ManifestNotFoundError(MissionControlUIAssetsError):
    pass


class ManifestInvalidJsonError(MissionControlUIAssetsError):
    pass


class EntrypointMissingError(MissionControlUIAssetsError):
    pass


class AssetFileMissingError(MissionControlUIAssetsError):
    pass


def _default_manifest_path() -> str:
    return os.environ.get(
        "VITE_MANIFEST_PATH",
        "api_service/static/task_dashboard/dist/.vite/manifest.json",
    )


def _lenient_ui_assets() -> bool:
    return os.environ.get("MOONMIND_LENIENT_UI_ASSETS", "").lower() in (
        "1",
        "true",
        "yes",
    )


class ViteAssetResolver:
    """Loads the Vite manifest for tests and tooling (lenient I/O)."""

    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self._manifest_cache: Optional[Dict[str, Any]] = None

    def get_manifest(self) -> Dict[str, Any]:
        if self._manifest_cache is None:
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    self._manifest_cache = json.load(f)
            except FileNotFoundError:
                self._manifest_cache = {}
            except json.JSONDecodeError:
                self._manifest_cache = {}

        return self._manifest_cache

    def resolve_entrypoint(self, entrypoint: str) -> Dict[str, Any]:
        manifest = self.get_manifest()
        key = f"entrypoints/{entrypoint}.tsx"
        if key in manifest:
            return manifest[key]
        return {}


def _dist_root_for_manifest(manifest_path: str) -> Path:
    return Path(manifest_path).resolve().parent.parent


def _verify_asset_paths(dist_root: Path, asset_info: Dict[str, Any]) -> None:
    js_file = asset_info.get("file")
    if not js_file or not isinstance(js_file, str):
        raise AssetFileMissingError(
            "Manifest entry has no usable 'file' path for the JavaScript bundle."
        )
    js_path = dist_root / js_file
    if not js_path.is_file():
        raise AssetFileMissingError(f"Referenced script is missing on disk: {js_path}")

    css_files = asset_info.get("css", [])
    if css_files is None:
        return
    if not isinstance(css_files, list):
        raise AssetFileMissingError("Manifest entry 'css' must be a list when present.")
    for css in css_files:
        if not isinstance(css, str):
            raise AssetFileMissingError("Manifest CSS entry must be a string path.")
        css_path = dist_root / css
        if not css_path.is_file():
            raise AssetFileMissingError(
                f"Referenced stylesheet is missing on disk: {css_path}"
            )


def _walk_manifest_imports(
    manifest: Dict[str, Any], manifest_key: str, seen: set[str] | None = None
) -> list[tuple[str, Dict[str, Any]]]:
    if seen is None:
        seen = set()
    if manifest_key in seen:
        return []

    asset_info = manifest.get(manifest_key)
    if not isinstance(asset_info, dict):
        raise AssetFileMissingError(
            f"Manifest import {manifest_key!r} is missing or invalid."
        )

    seen.add(manifest_key)
    ordered_assets: list[tuple[str, Dict[str, Any]]] = []
    imports = asset_info.get("imports") or []
    if not isinstance(imports, list):
        raise AssetFileMissingError(
            f"Manifest entry {manifest_key!r} has a non-list 'imports' field."
        )

    for import_key in imports:
        if not isinstance(import_key, str):
            raise AssetFileMissingError(
                f"Manifest entry {manifest_key!r} contains a non-string import key."
            )
        ordered_assets.extend(_walk_manifest_imports(manifest, import_key, seen))

    ordered_assets.append((manifest_key, asset_info))
    return ordered_assets


def _collect_css_files(
    manifest: Dict[str, Any], manifest_key: str
) -> list[str]:
    css_files: list[str] = []
    seen_css: set[str] = set()

    for _, asset_info in _walk_manifest_imports(manifest, manifest_key):
        css_entries = asset_info.get("css") or []
        if not isinstance(css_entries, list):
            raise AssetFileMissingError("Manifest entry 'css' must be a list when present.")
        for css_file in css_entries:
            if not isinstance(css_file, str):
                raise AssetFileMissingError(
                    "Manifest CSS entry must be a string path."
                )
            if css_file in seen_css:
                continue
            seen_css.add(css_file)
            css_files.append(css_file)

    return css_files


def ui_assets(entrypoint: str) -> str:
    """Return HTML tags to load the Vite bundle for a Mission Control entrypoint.

    By default (strict), raises MissionControlUIAssetsError if the manifest or files
    are missing so operators never get a blank content region without explanation.

    Set MOONMIND_LENIENT_UI_ASSETS=1 for local experiments without a built dist.
    """
    manifest_path = _default_manifest_path()
    manifest_key = f"entrypoints/{entrypoint}.tsx"
    lenient = _lenient_ui_assets()

    if not os.path.isfile(manifest_path):
        if lenient:
            return f"<!-- Vite manifest not found at {manifest_path} for {entrypoint} -->"
        raise ManifestNotFoundError(
            f"Vite manifest file not found at {manifest_path!r}. "
            "Run `npm run ui:build` or deploy an image that builds the UI from source."
        )

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest: Dict[str, Any] = json.load(f)
    except json.JSONDecodeError as exc:
        if lenient:
            return f"<!-- Vite manifest JSON invalid at {manifest_path} for {entrypoint} -->"
        raise ManifestInvalidJsonError(
            f"Vite manifest at {manifest_path!r} is not valid JSON."
        ) from exc

    asset_info = manifest.get(manifest_key, {})
    if not asset_info:
        if lenient:
            return f"<!-- Vite manifest entry not found for {entrypoint} -->"
        raise EntrypointMissingError(
            f"No manifest key {manifest_key!r} in {manifest_path!r}. "
            "The UI was built without this page or the manifest is stale."
        )

    dist_root = _dist_root_for_manifest(manifest_path)
    try:
        for _, imported_asset_info in _walk_manifest_imports(manifest, manifest_key):
            _verify_asset_paths(dist_root, imported_asset_info)
    except AssetFileMissingError:
        if lenient:
            return f"<!-- Vite manifest references missing files for {entrypoint} -->"
        raise

    js_file = asset_info["file"]
    css_files = _collect_css_files(manifest, manifest_key)

    tags: list[str] = []
    tags.append(
        f'<script type="module" crossorigin src="/static/task_dashboard/dist/{js_file}"></script>'
    )
    for css_file in css_files:
        tags.append(
            f'<link rel="stylesheet" crossorigin href="/static/task_dashboard/dist/{css_file}">'
        )

    return "\n".join(tags)
