"""Tests del contrato OpenAPI del login con email (FEAT-005, CA-16)."""

from __future__ import annotations

from app.main import app


def test_ca16_openapi_documents_both_identifiers_and_examples() -> None:
    """CA-16: `LoginRequest` exige solo `password`, tiene dos ejemplos y respuestas intactas."""
    spec = app.openapi()
    login_request = spec["components"]["schemas"]["LoginRequest"]

    assert sorted(login_request["required"]) == ["password"]
    assert set(login_request["properties"]) == {"email", "username", "password"}
    assert len(login_request["examples"]) == 2
    assert [sorted(example) for example in login_request["examples"]] == [
        ["email", "password"],
        ["password", "username"],
    ]

    email_description = login_request["properties"]["email"]["description"]
    username_description = login_request["properties"]["username"]["description"]
    for description in (email_description, username_description):
        assert "email" in description
        assert "username" in description

    login = spec["paths"]["/api/v1/auth/login"]["post"]
    assert sorted(login["responses"]) == ["200", "401", "403", "422", "429"]
