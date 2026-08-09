from api_service.main import app


def test_legacy_api_first_routes_are_not_registered() -> None:
    paths = {
        route.path
        for route in app.routes
        if isinstance(getattr(route, "path", None), str)
    }

    assert "/v1/chat/completions" not in paths
    assert "/v1/responses" not in paths
    assert "/v1/models/" not in paths
    assert not any(path.startswith("/v1/documents") for path in paths)


def test_legacy_api_first_routes_are_absent_from_openapi() -> None:
    paths = app.openapi()["paths"]

    assert not any(
        path == "/v1/responses"
        or path.startswith("/v1/chat")
        or path.startswith("/v1/models")
        or path.startswith("/v1/documents")
        for path in paths
    )
