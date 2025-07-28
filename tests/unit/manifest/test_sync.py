from moonmind.manifest.sync import compute_content_hash, detect_change, ManifestChange
from moonmind.schemas import Manifest


YAML1 = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  readers:
    - type: Dummy
"""

YAML2 = YAML1.replace("Dummy", "Other")


def test_detect_change_states():
    m1 = Manifest.model_validate_yaml(YAML1)
    m2 = Manifest.model_validate_yaml(YAML2)

    assert detect_change(None, m1) is ManifestChange.NEW

    h1 = compute_content_hash(m1)
    assert detect_change(h1, m1) is ManifestChange.UNCHANGED
    assert detect_change(h1, m2) is ManifestChange.MODIFIED
