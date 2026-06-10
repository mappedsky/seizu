from sqlalchemy.engine import URL, make_url


def build_database_url(
    base_url: str,
    *,
    user: str = "",
    password: str = "",
    drivername: str | None = None,
) -> URL:
    """Build a SQLAlchemy URL with optional separately managed credentials."""
    url = make_url(base_url)
    if drivername is not None:
        url = url.set(drivername=drivername)
    if user:
        url = url.set(username=user)
    if password:
        url = url.set(password=password)
    return url
