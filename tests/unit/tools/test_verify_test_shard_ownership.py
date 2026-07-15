from tools.verify_test_shard_ownership import CollectedTest, ownership_errors


def _test(*markers: str, path: str = "tests/unit/test_example.py") -> CollectedTest:
    return CollectedTest("node", path, frozenset(markers))


def test_each_exclusive_unit_owner_is_valid() -> None:
    tests = [
        _test("unit_fast"),
        _test("slow"),
        _test("temporal_boundary"),
        _test("component"),
        _test("reliability_journey", path="tests/integration/reliability/test_x.py"),
        _test("integration", "integration_ci", path="tests/integration/test_x.py"),
    ]

    assert ownership_errors(tests) == []


def test_missing_and_overlapping_owners_fail() -> None:
    errors = ownership_errors(
        [
            _test(),
            _test("unit_fast", "slow"),
            _test(
                "integration_ci",
                "reliability_journey",
                path="tests/integration/reliability/test_x.py",
            ),
        ]
    )

    assert any("no CI owner" in error for error in errors)
    assert any("unit_fast has an exclusive marker" in error for error in errors)
    assert any("integration_ci and reliability_journey overlap" in error for error in errors)
