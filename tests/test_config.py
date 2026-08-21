from astor.config import Settings


def test_bare_postgres_scheme_is_normalized_to_psycopg():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@host:5432/db"


def test_bare_postgresql_scheme_is_normalized_to_psycopg():
    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@host:5432/db"


def test_explicit_driver_is_left_untouched():
    url = "postgresql+psycopg://astor:astor@localhost:5432/astor"
    assert Settings(database_url=url).database_url == url


def test_query_string_survives_normalization():
    s = Settings(database_url="postgres://u:p@host/db?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:p@host/db?sslmode=require"


def test_non_postgres_url_is_left_untouched():
    assert Settings(database_url="sqlite:///./x.db").database_url == "sqlite:///./x.db"
