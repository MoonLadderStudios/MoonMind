"""Standalone MoonMind deployment controller (issue #4500).

Stdlib-only. Normal execution must not import MoonMind application modules
and must not require the API, DB, Temporal, artifact service, provider
manager, Omnigent, or an LLM. Release configuration arrives as data.
"""

__version__ = "0.1.0"

CONTROLLER_PROJECT = "moonmind-controller"
CONTROLLER_SERVICE = "controller"
DEFAULT_PORT = 8472
