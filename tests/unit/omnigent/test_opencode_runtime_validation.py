"""Pinned OpenCode runtime model-catalog validation."""

from __future__ import annotations

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.opencode_runtime_validation import _validated_models


def test_validated_models_returns_only_observed_opencode_go_models() -> None:
    assert _validated_models("opencode-go/gpt-5.6-luna\nother-provider/model\n") == [
        "opencode-go/gpt-5.6-luna"
    ]


@pytest.mark.parametrize("catalog", ["", [], {}, "other-provider/model"])
def test_validated_models_fail_closed_without_provider_evidence(catalog) -> None:
    with pytest.raises(HarnessPlatformError) as exc:
        _validated_models(catalog)

    assert (
        exc.value.code == HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE
    )
