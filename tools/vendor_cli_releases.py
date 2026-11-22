"""Choose the vendor CLI releases a MoonMind image installs.

Images follow each CLI's newest release rather than a version written into
the repository: hand-maintained pins went stale and kept new models out of
the profile catalogs until someone edited a Dockerfile. Two things narrow
"newest":

* The supported range. Inside the Omnigent host base, Omnigent's install
  specs declare which CLI releases its native harnesses accept, and the
  image's readiness gate rejects anything else, so the choice stays inside
  that range and follows it when a new base widens it. MoonMind adds a floor
  only where a provider requires one: Anthropic rejects Opus 5.5 requests
  from Claude Code older than 2.1.280.
* Complete publication. npm can serve a CLI wrapper before its platform
  binaries and silently skips a missing optional dependency, so installing a
  release caught mid-publish yields a CLI with nothing to run. A release
  counts only once every requested platform package (and, for OpenCode, the
  plugin SDK warmed at the same version) is published.

An exact version (a release workflow input or hold) is used as given after
the same range check. The script is stdlib-only because it runs in the Node
build stage of the worker image as well as in the Omnigent host base.
"""

from __future__ import annotations

import argparse
import json
import platform as host_platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

REGISTRY_URL = "https://registry.npmjs.org"
# Abbreviated metadata carries dist-tags and each version's dependencies.
_PACKUMENT_ACCEPT = (
    "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8"
)
_EXACT = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# How many of the newest in-range releases may be skipped as incomplete before
# resolution fails; a long run of incomplete releases means the registry
# layout changed, and silently downgrading would hide that.
_MAX_INCOMPLETE = 5

Packument = Callable[[str], "Mapping | None"]
Bounds = Callable[[str], "tuple[str | None, str | None]"]


class ResolutionError(RuntimeError):
    """No suitable release can be chosen for a requested CLI."""


@dataclass(frozen=True)
class VendorCli:
    name: str
    build_arg: str
    package: str
    omnigent_key: str
    floor: str | None = None
    companions: tuple[str, ...] = ()


CLIS: dict[str, VendorCli] = {
    cli.name: cli
    for cli in (
        VendorCli("codex", "CODEX_CLI_VERSION", "@openai/codex", "openai"),
        VendorCli(
            "claude",
            "CLAUDE_CLI_VERSION",
            "@anthropic-ai/claude-code",
            "anthropic",
            floor="2.1.280",
        ),
        VendorCli(
            "opencode",
            "OPENCODE_VERSION",
            "@opencode/cli",
            "opencode",
            companions=("@opencode/plugin",),
        ),
    )
}


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _range_text(minimum: str | None, maximum: str | None) -> str:
    parts = [f">={minimum}"] if minimum else []
    if maximum:
        parts.append(f"<{maximum}")
    return ", ".join(parts) or "any"


def _effective_range(
    cli: VendorCli, bounds: Bounds | None
) -> tuple[str | None, str | None]:
    minimum, maximum = bounds(cli.omnigent_key) if bounds else (None, None)
    if cli.floor and (minimum is None or _release(cli.floor) > _release(minimum)):
        minimum = cli.floor
    return minimum, maximum


def _in_range(version: str, minimum: str | None, maximum: str | None) -> bool:
    release = _release(version)
    if minimum and release < _release(minimum):
        return False
    return not (maximum and release >= _release(maximum))


def _dependency(key: str, spec: str) -> tuple[str, str]:
    # Codex publishes its binaries as npm aliases of versions of itself.
    if spec.startswith("npm:"):
        name, _, version = spec[len("npm:") :].rpartition("@")
        return name, version
    return key, spec


def _missing_artifacts(
    cli: VendorCli,
    version: str,
    manifest: Mapping,
    platforms: Sequence[str],
    packument: Packument,
) -> list[str]:
    def published(name: str, wanted: str) -> bool:
        doc = packument(name)
        return bool(doc) and wanted in (doc.get("versions") or {})

    missing: list[str] = []
    dependencies = manifest.get("optionalDependencies") or {}
    # A CLI without platform packages is plain JavaScript and always complete.
    for target in platforms if dependencies else ():
        entries = [
            _dependency(key, str(spec))
            for key, spec in dependencies.items()
            if key.endswith(f"-{target}")
        ]
        if not entries:
            missing.append(f"{cli.package}@{version} declares no {target} package")
        missing.extend(
            f"{name}@{wanted}"
            for name, wanted in entries
            if not published(name, wanted)
        )
    missing.extend(
        f"{companion}@{version}"
        for companion in cli.companions
        if not published(companion, version)
    )
    return missing


def _resolve_latest(
    cli: VendorCli,
    minimum: str | None,
    maximum: str | None,
    platforms: Sequence[str],
    packument: Packument,
) -> str:
    doc = packument(cli.package)
    if not doc:
        raise ResolutionError(f"npm has no package {cli.package}")
    versions = doc.get("versions") or {}
    latest = str((doc.get("dist-tags") or {}).get("latest") or "")
    candidates = sorted(
        (
            version
            for version in versions
            if _EXACT.fullmatch(version)
            and (not _EXACT.fullmatch(latest) or _release(version) <= _release(latest))
            and _in_range(version, minimum, maximum)
        ),
        key=_release,
        reverse=True,
    )
    if not candidates:
        raise ResolutionError(
            f"no published {cli.name} release is in the supported range "
            f"{_range_text(minimum, maximum)}"
        )
    skipped: list[str] = []
    for version in candidates[:_MAX_INCOMPLETE]:
        missing = _missing_artifacts(
            cli, version, versions[version], platforms, packument
        )
        if not missing:
            return version
        skipped.append(f"{version} (missing {', '.join(missing)})")
    raise ResolutionError(
        f"the newest {cli.name} releases are incomplete on npm: {'; '.join(skipped)}"
    )


def resolve_versions(
    requests: Mapping[str, str],
    *,
    platforms: Sequence[str],
    packument: Packument,
    bounds: Bounds | None = None,
) -> dict[str, str]:
    """Return the exact release to install for each requested CLI.

    ``requests`` maps a CLI name to ``latest`` (or empty) or an exact release.
    ``bounds`` returns Omnigent's ``(min_version, max_version_exclusive)`` for
    an install key; without it only MoonMind's own floors apply.
    """

    resolved: dict[str, str] = {}
    for name, request in requests.items():
        cli = CLIS.get(name)
        if cli is None:
            raise ResolutionError(f"unknown vendor CLI {name!r}")
        minimum, maximum = _effective_range(cli, bounds)
        if request in ("", "latest"):
            resolved[name] = _resolve_latest(
                cli, minimum, maximum, platforms, packument
            )
            continue
        if not _EXACT.fullmatch(request):
            raise ResolutionError(
                f"{cli.build_arg} must be latest or an exact release version "
                f"(got {request!r})"
            )
        if not _in_range(request, minimum, maximum):
            raise ResolutionError(
                f"{cli.build_arg} {request} is outside the supported range "
                f"{_range_text(minimum, maximum)}"
            )
        resolved[name] = request
    return resolved


def fetch_packument(name: str) -> Mapping | None:
    """Read one package's registry metadata; ``None`` when it does not exist."""

    url = f"{REGISTRY_URL}/{urllib.parse.quote(name, safe='@')}"
    request = urllib.request.Request(url, headers={"Accept": _PACKUMENT_ACCEPT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code < 500 or attempt == 2:
                raise ResolutionError(
                    f"npm registry read for {name} failed: {exc}"
                ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ResolutionError(
                    f"npm registry read for {name} failed: {exc}"
                ) from exc
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def omnigent_bounds() -> Bounds:
    """Return the CLI ranges the installed Omnigent's harnesses declare."""

    try:
        from omnigent.onboarding.harness_install import harness_install_spec
    except ImportError as exc:
        raise ResolutionError(
            "Omnigent install specs are unavailable; run inside the Omnigent host base"
        ) from exc

    def bounds(key: str) -> tuple[str | None, str | None]:
        spec = harness_install_spec(key)
        if spec is None:
            raise ResolutionError(f"Omnigent declares no install spec for {key!r}")
        return spec.min_version, spec.max_version_exclusive

    return bounds


def _default_platform() -> str:
    machine = host_platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}
    if machine not in arch:
        raise ResolutionError(f"unsupported build architecture {machine!r}")
    return f"linux-{arch[machine]}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "requests",
        nargs="+",
        metavar="CLI=REQUEST",
        help="CLI name and 'latest' or an exact release, e.g. claude=latest",
    )
    parser.add_argument(
        "--platform",
        action="append",
        dest="platforms",
        help="npm platform the release must ship, e.g. linux-arm64 (default: this machine)",
    )
    parser.add_argument(
        "--omnigent-bounds",
        action="store_true",
        help="apply the supported ranges declared by the installed Omnigent",
    )
    parser.add_argument("--json", type=Path, help="write the chosen releases as JSON")
    parser.add_argument(
        "--npm-specs",
        action="store_true",
        help="print npm install specs instead of NAME_version=VERSION lines",
    )
    args = parser.parse_args(argv)
    try:
        requests = dict(item.partition("=")[::2] for item in args.requests)
        resolved = resolve_versions(
            requests,
            platforms=args.platforms or [_default_platform()],
            packument=fetch_packument,
            bounds=omnigent_bounds() if args.omnigent_bounds else None,
        )
    except ResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        args.json.write_text(json.dumps(resolved, indent=2) + "\n", encoding="utf-8")
    if args.npm_specs:
        print(
            " ".join(
                f"{CLIS[name].package}@{version}" for name, version in resolved.items()
            )
        )
    else:
        for name, version in resolved.items():
            print(f"{name}_version={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
