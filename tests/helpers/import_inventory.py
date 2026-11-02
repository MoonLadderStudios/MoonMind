"""Immutable import inventories shared by architecture guards in one process."""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


def imports(repo_root: Path, relative_path: str) -> tuple[str, ...]:
    path = (repo_root / relative_path).resolve()
    stat = path.stat()
    return _imports(path, relative_path, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=None)
def _imports(
    path: Path, relative_path: str, mtime_ns: int, size: int
) -> tuple[str, ...]:
    # Cache the final tuples, not mutable ASTs. File identity invalidates a
    # changed source even when a negative fixture rewrites it in this process.
    module = relative_path.removesuffix(".py").replace("/", ".")
    imported: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            package = module.split(".")[: -node.level] if node.level else []
            imported.append(".".join([*package, node.module]))
    return tuple(imported)
