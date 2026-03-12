"""Local compatibility shim for environments without external dependencies.

The project normally uses the third-party ``httpx`` package.
This shim implements only the subset required by this repository's tests/CLI.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class TransportError(Exception):
    pass


class HTTPStatusError(Exception):
    def __init__(self, message: str, request: object, response: "Response") -> None:
        super().__init__(message)
        self.request = request
        self.response = response


@dataclass
class Response:
    status_code: int
    _body: bytes
    headers: dict[str, str]

    def json(self):
        return json.loads(self._body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPStatusError(
                f"HTTP {self.status_code}: {self.text[:200]}",
                request=None,
                response=self,
            )


class AsyncClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    async def aclose(self) -> None:
        return None

    async def get(self, url: str, params: dict | None = None) -> Response:
        import asyncio

        def _fetch() -> Response:
            try:
                if params:
                    qs = urllib.parse.urlencode(params)
                    sep = "&" if "?" in url else "?"
                    full_url = f"{url}{sep}{qs}"
                else:
                    full_url = url
                req = urllib.request.Request(full_url, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    headers = {k: v for k, v in resp.headers.items()}
                    return Response(status_code=resp.getcode(), _body=body, headers=headers)
            except urllib.error.HTTPError as exc:
                body = exc.read() if hasattr(exc, "read") else b""
                headers = dict(exc.headers.items()) if exc.headers else {}
                return Response(status_code=exc.code, _body=body, headers=headers)
            except Exception as exc:
                raise TransportError(str(exc)) from exc

        return await asyncio.to_thread(_fetch)
