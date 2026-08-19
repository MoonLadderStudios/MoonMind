"""Omnigent adapters: concrete implementations of the control-plane ports.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

Adapters implement the narrow protocols declared in
:mod:`moonmind.omnigent.ports` for concrete infrastructure (PostgreSQL, in-memory
test doubles, provider transports, hosts, workspaces, artifacts, publication).
Provider-native and infrastructure-native vocabulary stays at this boundary and
is translated into canonical domain records and outcomes.

The canonical production persistence adapters currently live in
:mod:`moonmind.omnigent.control_plane.repositories`; this package provides the
in-memory reference adapters used by the shared port-contract suite so that
in-memory and production adapters are proven interchangeable behind one
interface. Allowed dependency directions are documented in
``docs/Omnigent/Architecture.md``.
"""
