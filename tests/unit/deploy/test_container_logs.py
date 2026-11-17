"""Deployment containers keep bounded logs."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _parse_size(value: str) -> int:
    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
    value = value.strip().lower()
    return int(value[:-1]) * units[value[-1]] if value[-1] in units else int(value)


def test_application_services_rotate_container_logs():
    """Docker keeps json-file logs forever unless each service bounds them.

    The controller is excluded: it is replaced only through the deployment
    handoff, never the application's recreation pass, and logs very little.
    """

    services = yaml.safe_load((ROOT / "docker-compose.yaml").read_text())["services"]
    for name, service in services.items():
        if name == "controller":
            continue
        logging = service.get("logging") or {}
        options = logging.get("options") or {}
        assert logging.get("driver") == "json-file", name
        retained = _parse_size(options["max-size"]) * int(options["max-file"])
        assert 0 < retained <= 512 * 1024**2, name
