"""Compose host-mode adapters.

Placeholder seam for the Docker-Compose host launch mode. It shares the
``HostLauncher``/``LeaseManager`` ports with :mod:`docker_host`; the concrete
Compose launcher is extracted from the legacy runtime in a later phase of
MoonLadderStudios/MoonMind#3711. Keeping the package boundary now fixes the
dependency direction before the behavior moves.
"""
