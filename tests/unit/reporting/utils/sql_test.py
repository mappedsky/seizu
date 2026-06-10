from reporting.utils.sql import build_database_url


def test_build_database_url_overlays_and_encodes_credentials():
    url = build_database_url(
        "postgresql://db.example.com:5432/seizu?sslmode=require",
        user="service-user",
        password="p@ss:/word",
    )

    assert url.username == "service-user"
    assert url.password == "p@ss:/word"
    assert (
        url.render_as_string(hide_password=False)
        == "postgresql://service-user:p%40ss%3A%2Fword@db.example.com:5432/seizu?sslmode=require"
    )


def test_build_database_url_preserves_legacy_embedded_credentials():
    url = build_database_url("postgresql://legacy:secret@db.example.com:5432/seizu")

    assert url.username == "legacy"
    assert url.password == "secret"


def test_build_database_url_separate_credentials_override_embedded_values():
    url = build_database_url(
        "postgresql://legacy:secret@db.example.com:5432/seizu",
        user="managed-user",
        password="managed-secret",
    )

    assert url.username == "managed-user"
    assert url.password == "managed-secret"
