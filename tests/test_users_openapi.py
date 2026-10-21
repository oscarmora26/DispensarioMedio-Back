"""Contrato OpenAPI de FEAT-004 (CA-38)."""

from __future__ import annotations

from app.main import app

EXPECTED_PATHS = [
    "/api/v1/users",
    "/api/v1/users/{user_id}",
    "/api/v1/users/{user_id}/reset-password",
]


def test_ca38_openapi_declares_users_contract() -> None:
    """CA-38: paths, tag, seguridad, documentacion y codigos por endpoint."""
    spec = app.openapi()
    paths = spec["paths"]

    users_paths = sorted(path for path in paths if path.startswith("/api/v1/users"))
    assert users_paths == EXPECTED_PATHS

    tags = sorted(
        {
            tag
            for path in users_paths
            for method in paths[path].values()
            for tag in method.get("tags", [])
        }
    )
    assert tags == ["users"]

    operations = [
        (path, verb, method) for path in users_paths for verb, method in paths[path].items()
    ]
    assert len(operations) == 6
    for path, verb, method in operations:
        assert method.get("security"), (path, verb)
        assert method.get("summary"), (path, verb)
        assert method.get("description"), (path, verb)

    assert "content" in paths["/api/v1/users"]["get"]["responses"]["200"]
    assert "content" in paths["/api/v1/users"]["post"]["responses"]["201"]
    assert sorted(paths["/api/v1/users"]["post"]["responses"]) == [
        "201",
        "400",
        "401",
        "403",
        "409",
        "422",
    ]
    assert sorted(paths["/api/v1/users/{user_id}"]["delete"]["responses"]) == [
        "204",
        "400",
        "401",
        "403",
        "404",
        "422",
    ]

    expected = {
        ("/api/v1/users", "get"): ["200", "401", "403", "422"],
        ("/api/v1/users", "post"): ["201", "400", "401", "403", "409", "422"],
        ("/api/v1/users/{user_id}", "get"): ["200", "401", "403", "404", "422"],
        ("/api/v1/users/{user_id}", "put"): [
            "200",
            "400",
            "401",
            "403",
            "404",
            "409",
            "422",
        ],
        ("/api/v1/users/{user_id}", "delete"): [
            "204",
            "400",
            "401",
            "403",
            "404",
            "422",
        ],
        ("/api/v1/users/{user_id}/reset-password", "post"): [
            "200",
            "401",
            "403",
            "404",
            "422",
        ],
    }
    for (path, verb), codes in expected.items():
        assert sorted(paths[path][verb]["responses"]) == codes, (path, verb)

    assert "BearerAuth" in spec["components"]["securitySchemes"]

    user_schema = spec["components"]["schemas"]["UserRead"]["properties"]
    assert sorted(user_schema) == ["doctor_id", "email", "id", "is_active", "role", "username"]
