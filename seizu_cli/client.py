"""HTTP client wrapper for the Seizu REST API.

Handles Bearer token auth so commands do not need to think about it.
"""

from typing import Any

import requests


class APIError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class SeizuClient:
    """Thin requests wrapper that handles Bearer auth."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        if token:
            self._session.headers.update({"Authorization": f"Bearer {token}"})

    def _raise_for_status(self, resp: requests.Response) -> None:
        if not resp.ok:
            if resp.status_code == 401:
                raise APIError(
                    401,
                    "Unauthorized. Run 'seizu login' to authenticate, or pass --token / set SEIZU_TOKEN.",
                )
            try:
                body = resp.json()
                message = body.get("error") or body.get("message") or resp.text
            except Exception:
                message = resp.text
            raise APIError(resp.status_code, str(message))

    def get(self, path: str, **kwargs: Any) -> Any:
        resp = self._session.get(f"{self.base_url}{path}", timeout=30, **kwargs)
        self._raise_for_status(resp)
        return resp.json()

    def post(self, path: str, **kwargs: Any) -> Any:
        resp = self._session.post(f"{self.base_url}{path}", timeout=30, **kwargs)
        self._raise_for_status(resp)
        return resp.json()

    def put(self, path: str, **kwargs: Any) -> Any:
        resp = self._session.put(f"{self.base_url}{path}", timeout=30, **kwargs)
        self._raise_for_status(resp)
        return resp.json()

    def delete(self, path: str, **kwargs: Any) -> Any:
        resp = self._session.delete(f"{self.base_url}{path}", timeout=30, **kwargs)
        self._raise_for_status(resp)
        if resp.content:
            return resp.json()
        return None

    def get_bytes(self, path: str, **kwargs: Any) -> bytes:
        resp = self._session.get(f"{self.base_url}{path}", timeout=30, **kwargs)
        self._raise_for_status(resp)
        return resp.content
