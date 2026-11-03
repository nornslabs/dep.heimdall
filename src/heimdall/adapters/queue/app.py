"""The procrastinate app the worker runs; modules register their tasks on it."""

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url

PLAIN_POSTGRES_DRIVER = "postgresql"


def create_queue_app(database_url: str, *, schema: str | None = None) -> App:
    """Build the procrastinate app over psycopg, pointed at Heimdall's database.

    Args:
        database_url: Postgres URL from settings (any ``postgresql`` driver suffix is dropped).
        schema: Schema to put first on ``search_path``; tests use a throwaway one.

    Returns:
        A procrastinate App; open it with ``open_async()`` before running workers.
    """
    conninfo = (
        make_url(database_url)
        .set(drivername=PLAIN_POSTGRES_DRIVER)
        .render_as_string(hide_password=False)
    )
    connect_kwargs: dict[str, str] = {}
    if schema is not None:
        connect_kwargs["options"] = f"-c search_path={schema}"
    return App(connector=PsycopgConnector(conninfo=conninfo, kwargs=connect_kwargs))
