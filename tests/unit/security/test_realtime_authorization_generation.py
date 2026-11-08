from copy import deepcopy

from tests.unit.security.test_assessment_authorizations import authorization, saved
from tests.unit.security.test_realtime_authority import realtime


def test_type_only_payload_change_revokes_previously_reviewed_authority():
    item = authorization()
    target = item["scope"]["targets"][0]
    target["webRoutes"] = [{"path": "/", "methods": ["GET", "POST"]}]
    target["realtime"] = realtime()
    original = saved(item)
    changed = deepcopy(original["authorizations"][0])
    changed["scope"]["targets"][0]["realtime"]["cases"][0]["payload"]["enabled"] = 1
    result = saved(changed, original)
    assert (
        result["authorizations"][0]["generation"]
        != original["authorizations"][0]["generation"]
    )
