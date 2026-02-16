"""Skill artifact verification and shared workspace materialization."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
import socket
import subprocess
import stat
import tarfile
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .resolver import ResolvedSkill, RunSkillSelection, validate_skill_name
from .workspace_links import (
    SkillWorkspaceError,
    SkillWorkspaceLinks,
    ensure_shared_skill_links,
)


class SkillMaterializationError(RuntimeError):
    """Raised when skill materialization fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MaterializedSkill:
    """Materialized skill metadata for one run."""

    name: str
    version: str
    source_uri: str
    content_hash: str
    cache_path: Path


@dataclass(frozen=True, slots=True)
class MaterializedSkillWorkspace:
    """Resolved shared skill workspace for one run."""

    run_id: str
    selection_source: str
    run_root: Path
    cache_root: Path
    links: SkillWorkspaceLinks
    skills: tuple[MaterializedSkill, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "runId": self.run_id,
            "selectionSource": self.selection_source,
            "skills": [
                {
                    "name": skill.name,
                    "version": skill.version,
                    "sourceUri": skill.source_uri,
                    "contentHash": skill.content_hash,
                    "cachePath": str(skill.cache_path),
                }
                for skill in self.skills
            ],
            **self.links.to_payload(),
        }


def _parse_frontmatter_name(skill_md: Path) -> str | None:
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillMaterializationError(
            "skill_metadata_unreadable",
            f"Unable to read skill metadata file: {skill_md} ({exc})",
        ) from exc

    if not raw.startswith("---"):
        return None

    lines = raw.splitlines()
    try:
        end_index = lines[1:].index("---") + 1
    except ValueError:
        return None

    for line in lines[1:end_index]:
        if not line.strip().startswith("name:"):
            continue
        _, value = line.split(":", 1)
        parsed = value.strip().strip('"').strip("'")
        return parsed or None
    return None


def _hash_skill_directory(skill_dir: Path) -> str:
    digest = hashlib.sha256()

    for path in sorted(
        skill_dir.rglob("*"), key=lambda item: str(item.relative_to(skill_dir))
    ):
        rel = str(path.relative_to(skill_dir)).replace("\\", "/")
        digest.update(rel.encode("utf-8"))
        if path.is_symlink():
            digest.update(b"SYMLINK")
            digest.update(str(path.readlink()).encode("utf-8"))
            continue
        if path.is_dir():
            digest.update(b"DIR")
            continue
        digest.update(b"FILE")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)

    return digest.hexdigest()


def _mark_read_only(path: Path) -> None:
    if path.is_symlink():
        return
    if path.is_dir():
        path.chmod(0o555)
        for child in path.iterdir():
            _mark_read_only(child)
        return
    path.chmod(0o444)


def _extract_archive(archive: Path, destination: Path) -> Path:
    destination_root = destination.resolve()

    def _validated_member_path(name: str) -> Path:
        normalized = name.replace("\\", "/")
        member = PurePosixPath(normalized)
        if member.is_absolute() or ".." in member.parts:
            raise SkillMaterializationError(
                "unsafe_bundle_member",
                f"Archive member path is not allowed: {name}",
            )
        target = (destination_root / member).resolve()
        try:
            target.relative_to(destination_root)
        except ValueError as exc:
            raise SkillMaterializationError(
                "unsafe_bundle_member",
                f"Archive member path escapes extraction root: {name}",
            ) from exc
        return target

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if not member.filename:
                    continue
                _validated_member_path(member.filename)
                mode = (member.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise SkillMaterializationError(
                        "unsafe_bundle_member",
                        f"Archive member symlinks are not allowed: {member.filename}",
                    )
                bundle.extract(member, destination_root)
        return destination

    try:
        with tarfile.open(archive) as bundle:
            members = [member for member in bundle.getmembers() if member.name]
            for member in members:
                _validated_member_path(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise SkillMaterializationError(
                        "unsafe_bundle_member",
                        f"Archive member link/device entries are not allowed: {member.name}",
                    )
            bundle.extractall(destination_root, members=members)
        return destination
    except tarfile.TarError as exc:
        raise SkillMaterializationError(
            "unsupported_bundle",
            f"Skill bundle is not a valid zip/tar archive: {archive}",
        ) from exc


def _validate_public_remote_host(source_uri: str) -> None:
    parsed = urlparse(source_uri)
    hostname = parsed.hostname
    if not hostname:
        raise SkillMaterializationError(
            "bundle_fetch_failed",
            f"Skill bundle source URI is missing a hostname: {source_uri}",
        )

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SkillMaterializationError(
            "bundle_fetch_failed",
            f"Unable to resolve skill bundle host '{hostname}': {exc}",
        ) from exc

    for addrinfo in addresses:
        try:
            ip = ipaddress.ip_address(addrinfo[4][0])
        except ValueError as exc:
            raise SkillMaterializationError(
                "bundle_fetch_failed",
                f"Unable to parse resolved bundle host IP for '{hostname}'",
            ) from exc
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SkillMaterializationError(
                "bundle_fetch_failed",
                f"Skill bundle source host resolves to a non-public address: {hostname}",
            )


def _download_remote_bundle(source_uri: str, destination: Path) -> Path:
    _validate_public_remote_host(source_uri)
    request = Request(source_uri, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            _validate_public_remote_host(final_url)
            with destination.open("wb") as output:
                shutil.copyfileobj(response, output)
    except OSError as exc:
        raise SkillMaterializationError(
            "bundle_fetch_failed",
            f"Unable to download skill bundle from {source_uri}: {exc}",
        ) from exc
    return destination


def _resolve_source_root(entry: ResolvedSkill, scratch_dir: Path) -> Path:
    source_uri = entry.source_uri.strip()
    parsed = urlparse(source_uri)
    skill_name = validate_skill_name(entry.skill_name)

    if parsed.scheme == "builtin":
        builtin_root = scratch_dir / f"builtin-{skill_name}" / skill_name
        builtin_root.mkdir(parents=True, exist_ok=True)
        (builtin_root / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: Built-in MoonMind skill\n---\n",
            encoding="utf-8",
        )
        (builtin_root / "README.md").write_text(
            "Built-in compatibility skill generated by MoonMind runtime.\n",
            encoding="utf-8",
        )
        return builtin_root

    if source_uri.startswith("git+"):
        repo_uri = source_uri[len("git+") :].strip()
        destination = scratch_dir / f"git-{skill_name}"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--", repo_uri, str(destination)],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SkillMaterializationError(
                "git_fetch_failed",
                f"Unable to clone git skill source for {skill_name}: {exc}",
            ) from exc
        return destination

    if parsed.scheme in {"http", "https"}:
        download_path = scratch_dir / f"bundle-{skill_name}"
        local_path = _download_remote_bundle(source_uri, download_path)
        extracted = scratch_dir / f"bundle-extract-{skill_name}"
        extracted.mkdir(parents=True, exist_ok=True)
        return _extract_archive(local_path, extracted)

    if parsed.scheme == "file":
        candidate = Path(parsed.path)
    elif parsed.scheme:
        raise SkillMaterializationError(
            "unsupported_source_scheme",
            f"Unsupported source URI scheme '{parsed.scheme}' for {skill_name}",
        )
    else:
        candidate = Path(source_uri)

    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()

    if candidate.is_dir():
        return candidate
    if candidate.is_file():
        extracted = scratch_dir / f"bundle-extract-{skill_name}"
        extracted.mkdir(parents=True, exist_ok=True)
        return _extract_archive(candidate, extracted)

    raise SkillMaterializationError(
        "source_not_found",
        f"Skill source path does not exist for {skill_name}: {candidate}",
    )


def _find_skill_dir(root: Path, *, skill_name: str) -> Path:
    if root.name == skill_name:
        return root

    direct = root / skill_name
    if direct.is_dir() and (direct / "SKILL.md").is_file():
        return direct

    candidates: list[Path] = []
    for skill_md in root.glob("**/SKILL.md"):
        parent = skill_md.parent
        if parent.name == skill_name:
            return parent
        candidates.append(parent)

    if len(candidates) == 1:
        return candidates[0]

    raise SkillMaterializationError(
        "skill_dir_not_found",
        f"Unable to locate skill directory for '{skill_name}' in source root {root}",
    )


def _validate_skill_metadata(entry: ResolvedSkill, skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SkillMaterializationError(
            "missing_skill_md",
            f"Missing SKILL.md for skill '{entry.skill_name}' in {skill_dir}",
        )

    metadata_name = _parse_frontmatter_name(skill_md)
    if metadata_name and metadata_name != skill_dir.name:
        raise SkillMaterializationError(
            "skill_name_mismatch",
            f"Skill metadata name '{metadata_name}' does not match directory '{skill_dir.name}'",
        )
    if skill_dir.name != entry.skill_name:
        raise SkillMaterializationError(
            "skill_name_mismatch",
            f"Resolved skill name '{entry.skill_name}' does not match directory '{skill_dir.name}'",
        )


def _clear_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child)


def _ensure_signature(entry: ResolvedSkill, *, verify_signatures: bool) -> None:
    if verify_signatures and not entry.signature:
        raise SkillMaterializationError(
            "signature_missing",
            f"Skill '{entry.skill_name}:{entry.version}' is missing a required signature",
        )


def _materialize_cache_entry(
    *, entry: ResolvedSkill, cache_root: Path
) -> MaterializedSkill:
    skill_name = validate_skill_name(entry.skill_name)
    with tempfile.TemporaryDirectory(
        prefix=f"skill-{skill_name}-"
    ) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        source_root = _resolve_source_root(entry, temp_dir)
        skill_dir = _find_skill_dir(source_root, skill_name=skill_name)
        _validate_skill_metadata(entry, skill_dir)

        computed_hash = _hash_skill_directory(skill_dir)
        if entry.content_hash and entry.content_hash != computed_hash:
            raise SkillMaterializationError(
                "hash_mismatch",
                f"Hash mismatch for '{skill_name}:{entry.version}' "
                f"(expected {entry.content_hash}, got {computed_hash})",
            )

        skill_hash_root = cache_root / computed_hash
        skill_cache_dir = skill_hash_root / skill_name
        if not skill_cache_dir.exists():
            skill_hash_root.mkdir(parents=True, exist_ok=True)
            staging_dir = skill_hash_root / f".{skill_name}.tmp-{uuid.uuid4().hex}"
            try:
                shutil.copytree(skill_dir, staging_dir)
                _mark_read_only(staging_dir)
                os.replace(staging_dir, skill_cache_dir)
            except FileExistsError:
                # Concurrent run already materialized the same digest.
                shutil.rmtree(staging_dir, ignore_errors=True)

    return MaterializedSkill(
        name=skill_name,
        version=entry.version,
        source_uri=entry.source_uri,
        content_hash=computed_hash,
        cache_path=skill_cache_dir,
    )


def materialize_run_skill_workspace(
    *,
    selection: RunSkillSelection,
    run_root: Path,
    cache_root: Path,
    verify_signatures: bool = False,
) -> MaterializedSkillWorkspace:
    """Resolve, verify, cache, and link a run-local shared skills workspace."""

    run_root = run_root.resolve()
    cache_root = cache_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    skills_active_path = run_root / "skills_active"
    skills_active_path.mkdir(parents=True, exist_ok=True)
    _clear_directory(skills_active_path)

    materialized: list[MaterializedSkill] = []
    seen_names: set[str] = set()

    for entry in selection.skills:
        if entry.skill_name in seen_names:
            raise SkillMaterializationError(
                "duplicate_skill_name",
                f"Duplicate skill name in selection: {entry.skill_name}",
            )
        _ensure_signature(entry, verify_signatures=verify_signatures)
        result = _materialize_cache_entry(entry=entry, cache_root=cache_root)
        seen_names.add(result.name)
        materialized.append(result)

    for item in materialized:
        target = skills_active_path / item.name
        if target.exists() or target.is_symlink():
            target.unlink(missing_ok=True)
        target.symlink_to(item.cache_path)

    try:
        links = ensure_shared_skill_links(
            run_root=run_root,
            skills_active_path=skills_active_path,
        )
    except SkillWorkspaceError as exc:
        raise SkillMaterializationError("workspace_link_failed", str(exc)) from exc

    return MaterializedSkillWorkspace(
        run_id=selection.run_id,
        selection_source=selection.selection_source,
        run_root=run_root,
        cache_root=cache_root,
        links=links,
        skills=tuple(materialized),
    )
