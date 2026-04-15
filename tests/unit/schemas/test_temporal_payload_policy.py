from __future__ import annotations

import math

import pytest

from moonmind.schemas.temporal_payload_policy import validate_compact_temporal_mapping


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_validate_compact_temporal_mapping_rejects_non_standard_floats(
    value: float,
) -> None:
    with pytest.raises(ValueError, match="JSON serializable"):
        validate_compact_temporal_mapping(
            {"providerScore": value},
            field_name="metadata",
        )

