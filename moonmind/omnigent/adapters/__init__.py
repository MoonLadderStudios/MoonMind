"""Omnigent adapters: concrete implementations of ports.

Adapters translate between the outside world (PostgreSQL, provider HTTP/streams,
Temporal, Docker/Compose, workspace, artifacts, publication) and the canonical
domain observations/outcomes. Provider-native vocabulary is confined to this
layer. Adapters may import infrastructure (SQLAlchemy, httpx, Docker, settings);
the domain, application, and port layers may not.
"""
