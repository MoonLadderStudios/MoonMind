"""Read the native chat bootstrap as a browser reads its HTML metadata."""

import json
from html.parser import HTMLParser
from typing import Any


def read_native_ui_bootstrap(document: str) -> dict[str, Any]:
    class BootstrapParser(HTMLParser):
        payload: str | None = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if (
                tag == "meta"
                and attributes.get("id") == "moonmind-omnigent-chat-bootstrap"
            ):
                self.payload = attributes.get("data-bootstrap")

    parser = BootstrapParser()
    parser.feed(document)
    assert parser.payload is not None, "Native chat bootstrap metadata is missing"
    payload = json.loads(parser.payload)
    assert isinstance(payload, dict)
    return payload
