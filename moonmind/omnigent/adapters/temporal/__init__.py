"""Temporal adapters.

Placeholder seam for Temporal-facing adapters (activity wrappers that invoke
application use cases with compact, replay-safe payloads). The Temporal SDK is
confined to this package; the domain and application layers never import
``temporalio``. Concrete activity bindings are extracted in a later phase of
MoonLadderStudios/MoonMind#3711.
"""
