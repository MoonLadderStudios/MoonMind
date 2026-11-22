"""Image builds choose the newest supported, fully published vendor CLIs."""

from __future__ import annotations

import json
import sys
import types

import pytest

from tools import vendor_cli_releases as releases

BOTH_PLATFORMS = ("linux-x64", "linux-arm64")

CLAUDE = "@anthropic-ai/claude-code"
CLAUDE_X64 = f"{CLAUDE}-linux-x64"
CLAUDE_ARM = f"{CLAUDE}-linux-arm64"
CODEX = "@openai/codex"
OPENCODE = "@opencode/cli"
OPENCODE_PLUGIN = "@opencode/plugin"


class Registry:
    """npm packuments by package name; an absent name is a 404."""

    def __init__(self, docs):
        self.docs = docs
        self.requests: list[str] = []

    def __call__(self, name):
        self.requests.append(name)
        return self.docs.get(name)


def _claude_docs(*, wrappers, x64, arm64, latest):
    return {
        CLAUDE: {
            "dist-tags": {"latest": latest},
            "versions": {
                version: {
                    "optionalDependencies": {
                        CLAUDE_X64: version,
                        CLAUDE_ARM: version,
                        f"{CLAUDE}-linux-x64-musl": version,
                        f"{CLAUDE}-darwin-arm64": version,
                    }
                }
                for version in wrappers
            },
        },
        CLAUDE_X64: {"versions": {version: {} for version in x64}},
        CLAUDE_ARM: {"versions": {version: {} for version in arm64}},
    }


def _opencode_docs(*, wrappers, plugin, latest):
    docs = {
        OPENCODE: {
            "dist-tags": {"latest": latest},
            "versions": {
                version: {
                    "optionalDependencies": {
                        f"{OPENCODE}-linux-x64": version,
                        f"{OPENCODE}-linux-arm64": version,
                        f"{OPENCODE}-linux-x64-baseline": version,
                    }
                }
                for version in wrappers
            },
        },
        OPENCODE_PLUGIN: {"versions": {version: {} for version in plugin}},
    }
    for platform in ("linux-x64", "linux-arm64"):
        docs[f"{OPENCODE}-{platform}"] = {
            "versions": {version: {} for version in wrappers}
        }
    return docs


def test_latest_waits_until_every_platform_binary_is_published():
    # npm silently skips a missing optional dependency, so a wrapper published
    # before its arm64 binary would install a CLI with nothing to run.
    registry = Registry(
        _claude_docs(
            wrappers=["2.1.329", "2.1.330", "2.1.331"],
            x64=["2.1.329", "2.1.330", "2.1.331"],
            arm64=["2.1.329", "2.1.330"],
            latest="2.1.331",
        )
    )

    assert releases.resolve_versions(
        {"claude": "latest"}, platforms=BOTH_PLATFORMS, packument=registry
    ) == {"claude": "2.1.330"}
    # A single-architecture image build only needs its own binary.
    assert releases.resolve_versions(
        {"claude": "latest"}, platforms=("linux-x64",), packument=registry
    ) == {"claude": "2.1.331"}


def test_codex_platform_builds_resolve_through_npm_aliases():
    def wrapper(version):
        return {
            "optionalDependencies": {
                f"{CODEX}-{platform}": f"npm:{CODEX}@{version}-{platform}"
                for platform in ("linux-x64", "linux-arm64", "darwin-arm64")
            }
        }

    versions = {
        "0.170.0": wrapper("0.170.0"),
        "0.171.0": wrapper("0.171.0"),
        # Newer than the latest tag or not stable: never selected.
        "0.172.0-alpha.1": wrapper("0.172.0-alpha.1"),
        "0.173.0": wrapper("0.173.0"),
    }
    for version in ("0.170.0", "0.171.0", "0.172.0-alpha.1", "0.173.0"):
        for platform in ("linux-x64", "linux-arm64"):
            versions[f"{version}-{platform}"] = {}
    registry = Registry(
        {CODEX: {"dist-tags": {"latest": "0.171.0"}, "versions": versions}}
    )

    assert releases.resolve_versions(
        {"codex": "latest"}, platforms=BOTH_PLATFORMS, packument=registry
    ) == {"codex": "0.171.0"}


def test_latest_stays_inside_the_range_the_omnigent_base_supports():
    registry = Registry(
        _opencode_docs(
            wrappers=["2.0.31", "2.0.32", "2.1.0", "2.3.2"],
            plugin=["2.0.31", "2.0.32", "2.1.0", "2.3.2"],
            latest="2.3.2",
        )
    )

    assert releases.resolve_versions(
        {"opencode": "latest"},
        platforms=BOTH_PLATFORMS,
        packument=registry,
        bounds=lambda key: ("2.0.18", "2.1.0") if key == "opencode" else (None, None),
    ) == {"opencode": "2.0.32"}
    # When the base widens its range, the next build follows it.
    assert releases.resolve_versions(
        {"opencode": "latest"},
        platforms=BOTH_PLATFORMS,
        packument=registry,
        bounds=lambda key: ("2.0.18", None),
    ) == {"opencode": "2.3.2"}


def test_opencode_waits_for_its_matching_plugin_sdk():
    # The image warms @opencode/plugin at the CLI's own version for offline
    # plugin startup, so that SDK release must exist too.
    registry = Registry(
        _opencode_docs(
            wrappers=["2.0.31", "2.0.32"], plugin=["2.0.31"], latest="2.0.32"
        )
    )

    assert releases.resolve_versions(
        {"opencode": "latest"}, platforms=BOTH_PLATFORMS, packument=registry
    ) == {"opencode": "2.0.31"}


def test_an_exact_request_is_used_without_reading_the_registry():
    def unreachable(name):
        raise AssertionError(f"registry read for {name}")

    assert releases.resolve_versions(
        {"codex": "0.170.0", "claude": "2.1.330"},
        platforms=BOTH_PLATFORMS,
        packument=unreachable,
    ) == {"codex": "0.170.0", "claude": "2.1.330"}


@pytest.mark.parametrize(
    ("requests", "bounds", "message"),
    [
        (
            {"opencode": "2.3.2"},
            lambda key: ("2.0.18", "2.1.0"),
            "OPENCODE_VERSION 2.3.2 is outside the supported range >=2.0.18, <2.1.0",
        ),
        # Anthropic rejects Opus 5.5 requests from Claude Code older than 2.1.280.
        (
            {"claude": "2.1.279"},
            lambda key: ("2.1.161", None),
            "CLAUDE_CLI_VERSION 2.1.279 is outside the supported range >=2.1.280",
        ),
    ],
)
def test_requests_outside_the_supported_range_fail_before_the_build(
    requests, bounds, message
):
    with pytest.raises(releases.ResolutionError, match=message):
        releases.resolve_versions(
            requests,
            platforms=BOTH_PLATFORMS,
            packument=Registry({}),
            bounds=bounds,
        )


def test_latest_never_selects_claude_below_the_opus_floor():
    registry = Registry(
        _claude_docs(
            wrappers=["2.1.200", "2.1.279"],
            x64=["2.1.200", "2.1.279"],
            arm64=["2.1.200", "2.1.279"],
            latest="2.1.279",
        )
    )

    with pytest.raises(releases.ResolutionError, match="no published claude release"):
        releases.resolve_versions(
            {"claude": "latest"}, platforms=BOTH_PLATFORMS, packument=registry
        )


def test_incomplete_recent_releases_fail_instead_of_silently_downgrading():
    # A renamed platform package upstream would make every recent release look
    # incomplete; walking back to an ancient release would hide that.
    wrappers = [f"2.1.{patch}" for patch in range(300, 310)]
    registry = Registry(
        _claude_docs(
            wrappers=wrappers, x64=wrappers, arm64=["2.1.300"], latest="2.1.309"
        )
    )

    with pytest.raises(releases.ResolutionError, match="incomplete") as failure:
        releases.resolve_versions(
            {"claude": "latest"}, platforms=BOTH_PLATFORMS, packument=registry
        )
    assert f"{CLAUDE_ARM}@2.1.309" in str(failure.value)


@pytest.mark.parametrize(
    "request_value", ["0.170", "^0.170.0", "latest\nimage_name=injected", "0.170.0 "]
)
def test_requests_must_be_latest_or_an_exact_release(request_value):
    with pytest.raises(
        releases.ResolutionError,
        match="CODEX_CLI_VERSION must be latest or an exact release version",
    ):
        releases.resolve_versions(
            {"codex": request_value}, platforms=BOTH_PLATFORMS, packument=Registry({})
        )


def test_unknown_cli_is_rejected():
    with pytest.raises(releases.ResolutionError, match="unknown vendor CLI 'gemini'"):
        releases.resolve_versions(
            {"gemini": "latest"}, platforms=BOTH_PLATFORMS, packument=Registry({})
        )


def test_omnigent_bounds_come_from_the_installed_harness_specs(monkeypatch):
    specs = {
        "openai": types.SimpleNamespace(
            min_version="0.137.0", max_version_exclusive=None
        ),
        "anthropic": types.SimpleNamespace(
            min_version="2.1.161", max_version_exclusive=None
        ),
        "opencode": types.SimpleNamespace(
            min_version="2.0.18", max_version_exclusive="2.1.0"
        ),
    }
    module = types.ModuleType("omnigent.onboarding.harness_install")
    module.harness_install_spec = specs.get
    monkeypatch.setitem(sys.modules, "omnigent", types.ModuleType("omnigent"))
    monkeypatch.setitem(
        sys.modules, "omnigent.onboarding", types.ModuleType("omnigent.onboarding")
    )
    monkeypatch.setitem(sys.modules, "omnigent.onboarding.harness_install", module)

    bounds = releases.omnigent_bounds()

    assert bounds("openai") == ("0.137.0", None)
    assert bounds("opencode") == ("2.0.18", "2.1.0")
    with pytest.raises(releases.ResolutionError, match="no install spec for 'pi'"):
        bounds("pi")


def test_omnigent_bounds_fail_when_the_base_cannot_provide_them(monkeypatch):
    monkeypatch.setitem(sys.modules, "omnigent.onboarding.harness_install", None)

    with pytest.raises(releases.ResolutionError, match="Omnigent install specs"):
        releases.omnigent_bounds()


def test_command_line_writes_outputs_specs_and_provenance(
    monkeypatch, tmp_path, capsys
):
    def unreachable(name):
        raise AssertionError(f"registry read for {name}")

    monkeypatch.setattr(releases, "fetch_packument", unreachable)
    provenance = tmp_path / "vendor-cli-versions.json"

    assert (
        releases.main(
            [
                "--platform",
                "linux-x64",
                "--json",
                str(provenance),
                "codex=0.170.0",
                "claude=2.1.330",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "codex_version=0.170.0\nclaude_version=2.1.330\n"
    assert json.loads(provenance.read_text()) == {
        "codex": "0.170.0",
        "claude": "2.1.330",
    }

    assert releases.main(["--npm-specs", "codex=0.170.0", "claude=2.1.330"]) == 0
    assert (
        capsys.readouterr().out
        == "@openai/codex@0.170.0 @anthropic-ai/claude-code@2.1.330\n"
    )


def test_command_line_reports_resolution_errors(capsys):
    assert releases.main(["codex=0.170"]) == 1
    assert "CODEX_CLI_VERSION must be latest" in capsys.readouterr().err
