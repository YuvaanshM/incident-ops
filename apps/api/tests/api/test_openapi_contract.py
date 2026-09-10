"""Stable Phase 3 OpenAPI conventions."""

from collections.abc import Mapping

import pytest

from incident_ops.config import Settings
from incident_ops.main import create_app


@pytest.fixture
def openapi_document(test_settings: Settings) -> dict[str, object]:
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url=test_settings.database_url,
        development_auth_enabled=True,
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["testserver"],
    )
    return create_app(settings).openapi()


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/incidents", "post"),
        ("/api/v1/incidents/{incident_id}", "patch"),
        ("/api/v1/incidents/{incident_id}/actions", "post"),
        ("/api/v1/actions/{action_id}/approve", "post"),
        ("/api/v1/actions/{action_id}/reject", "post"),
        ("/api/v1/demo/jobs/{job_id}/simulate-failure", "post"),
    ],
)
def test_mutations_publish_required_idempotency_header(
    openapi_document: dict[str, object],
    path: str,
    method: str,
) -> None:
    paths = openapi_document["paths"]
    assert isinstance(paths, Mapping)
    path_item = paths[path]
    assert isinstance(path_item, Mapping)
    operation = path_item[method]
    assert isinstance(operation, Mapping)
    parameters = operation["parameters"]
    assert isinstance(parameters, list)

    idempotency = next(
        parameter
        for parameter in parameters
        if isinstance(parameter, Mapping) and parameter.get("name") == "Idempotency-Key"
    )
    assert idempotency["in"] == "header"
    assert idempotency["required"] is True


def test_command_schemas_reject_unknown_top_level_fields(
    openapi_document: dict[str, object],
) -> None:
    components = openapi_document["components"]
    assert isinstance(components, Mapping)
    schemas = components["schemas"]
    assert isinstance(schemas, Mapping)

    for name in ("IncidentCreate", "IncidentUpdate", "ActionRequestCreate", "ActionReject"):
        schema = schemas[name]
        assert isinstance(schema, Mapping)
        assert schema["additionalProperties"] is False


def test_operation_ids_are_unique_and_demo_runner_is_documented_only_for_development(
    openapi_document: dict[str, object],
    test_settings: Settings,
) -> None:
    paths = openapi_document["paths"]
    assert isinstance(paths, Mapping)
    operation_ids = [
        operation["operationId"]
        for path_item in paths.values()
        if isinstance(path_item, Mapping)
        for method, operation in path_item.items()
        if method in {"get", "post", "patch", "put", "delete"} and isinstance(operation, Mapping)
    ]
    assert len(operation_ids) == len(set(operation_ids))
    assert "/api/v1/demo/jobs/{job_id}/simulate-failure" in paths

    production_settings = Settings(
        _env_file=None,
        app_env="production",
        database_url=test_settings.database_url,
        api_public_url="https://api.example.test",
        web_public_url="https://app.example.test",
        cors_origins=["https://app.example.test"],
        allowed_hosts=["api.example.test"],
        oidc_issuer_url="https://incident-ops.example/",
        oidc_audience="https://api.example.test",
        oidc_jwks_url="https://incident-ops.example/.well-known/jwks.json",
    )
    production_paths = create_app(production_settings).openapi()["paths"]
    assert "/api/v1/demo/jobs/{job_id}/simulate-failure" not in production_paths
