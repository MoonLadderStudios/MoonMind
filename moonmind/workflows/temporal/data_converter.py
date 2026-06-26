"""Shared Temporal payload conversion contract for MoonMind runtimes."""

from __future__ import annotations

from temporalio.contrib.pydantic import pydantic_data_converter

MOONMIND_TEMPORAL_DATA_CONVERTER = pydantic_data_converter

__all__ = ["MOONMIND_TEMPORAL_DATA_CONVERTER"]
