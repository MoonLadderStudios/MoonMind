from __future__ import annotations

import logging
from importlib import import_module
from typing import Any, Dict, List, Optional

from llama_index.core import download_loader

from moonmind.schemas import Manifest


class ManifestRunner:
    """Run readers defined in a Manifest."""

    def __init__(self, manifest: Manifest, logger: Optional[logging.Logger] = None) -> None:
        self.manifest = manifest
        self.logger = logger or logging.getLogger(__name__)

    def run(self) -> Dict[str, List[Any]]:
        """Instantiate and run each enabled reader.

        Returns a mapping of reader name/type to the list of loaded data results.
        """
        results: Dict[str, List[Any]] = {}
        for reader in self.manifest.spec.readers:
            name = reader.name or reader.type
            if not reader.enabled:
                self.logger.info("Reader %s is disabled; skipping", name)
                continue
            try:
                cls = self._load_reader_class(reader.type)
                instance = cls(**reader.init)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Failed to initialize reader %s: %s", name, exc)
                continue

            reader_results: List[Any] = []
            for load_args in reader.load_data or [{}]:
                try:
                    data = instance.load_data(**load_args)
                    reader_results.append(data)
                except Exception as exc:  # pragma: no cover - defensive
                    self.logger.exception(
                        "Error loading data with reader %s: %s", name, exc
                    )
            results[name] = reader_results
        return results

    def _load_reader_class(self, type_name: str):
        """Return the reader class for the given type name."""
        try:
            return download_loader(type_name)
        except Exception:
            # Fallback to importing from llama_index.readers package
            try:
                module = import_module(f"llama_index.readers.{type_name.lower()}")
                return getattr(module, type_name)
            except Exception:
                module = import_module("llama_index.readers")
                return getattr(module, type_name)

