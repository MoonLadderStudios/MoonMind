"""Daemon-visible mount adapter: POSIX/Windows/WSL without blind rewrites."""
from conftest import load

import pytest


def test_windows_drive_path_maps_to_desktop_host_namespace(controller_path):
    mounts = load("mounts")
    assert (
        mounts.daemon_visible_host_path("C:\\repo\\data")
        == "/run/desktop/mnt/host/c/repo/data"
    )
    assert mounts.daemon_visible_host_path("D:/x") == "/run/desktop/mnt/host/d/x"


def test_wsl_distro_drive_path_keeps_the_distro_spelling(controller_path):
    """Docker Desktop resolves a WSL distro client's binds in that distro.

    There ``/run/desktop/mnt/host`` does not exist and becomes an empty
    directory, so the distro's own ``/mnt/<drive>`` spelling must pass through.
    """
    mounts = load("mounts")
    assert mounts.daemon_visible_host_path("/mnt/c/repo/data") == "/mnt/c/repo/data"


def test_desktop_path_takes_the_wsl_spelling_this_client_sees(
    controller_path, tmp_path, monkeypatch
):
    mounts = load("mounts")
    monkeypatch.setattr(mounts, "WSL_DRIVE_ROOT", tmp_path)
    visible = tmp_path / "d" / "code" / "state"
    visible.mkdir(parents=True)
    assert mounts.wsl_distro_host_path("/run/desktop/mnt/host/d/code/state") == str(
        visible
    )
    # A client without that drive mount (a raw-daemon container) keeps the
    # daemon-namespace spelling, as do paths outside the Desktop namespace.
    assert (
        mounts.wsl_distro_host_path("/run/desktop/mnt/host/e/absent")
        == "/run/desktop/mnt/host/e/absent"
    )
    assert mounts.wsl_distro_host_path("/var/run/docker.sock") == "/var/run/docker.sock"


def test_longer_mnt_mounts_and_posix_paths_pass_through(controller_path):
    mounts = load("mounts")
    assert mounts.daemon_visible_host_path("/mnt/data/vol") == "/mnt/data/vol"
    assert mounts.daemon_visible_host_path("/var/lib/moonmind") == "/var/lib/moonmind"
    # Already-namespaced Desktop paths are not rewritten again.
    assert (
        mounts.daemon_visible_host_path("/run/desktop/mnt/host/c/repo")
        == "/run/desktop/mnt/host/c/repo"
    )


def test_missing_required_host_source_is_an_error_not_an_empty_dir(
    controller_path, tmp_path
):
    mounts = load("mounts")
    missing = tmp_path / "does-not-exist"
    with pytest.raises(mounts.MissingBindSourceError):
        mounts.resolve_bind_source(str(missing), required=True)
    assert not missing.exists(), "must not auto-create an empty bind directory"


def test_optional_missing_source_resolves_to_none(controller_path, tmp_path):
    mounts = load("mounts")
    assert (
        mounts.resolve_bind_source(str(tmp_path / "absent"), required=False) is None
    )
