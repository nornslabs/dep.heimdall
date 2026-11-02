import pytest

from heimdall.adapters.db.engine import to_async_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
    ],
)
def test_to_async_url_uses_asyncpg_driver(url: str, expected: str) -> None:
    assert to_async_url(url) == expected


@pytest.mark.parametrize("url", ["sqlite:///x.db", "postgresql+psycopg://u:p@h/d"])
def test_to_async_url_rejects_other_databases(url: str) -> None:
    with pytest.raises(ValueError, match="Postgres"):
        to_async_url(url)
