"""HTTP to the zumen-gate API: errors, the NDJSON stream of long conversions, and downloads.

Retries only what the API asks to be retried (429 and 503 with Retry-After). Any other failure is
returned as is: sending the same drawing again gives the same answer and may use calls.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .config import Config

CLIENT_NAME = f"zumen-gate-mcp/{__version__}"
RETRY_STATUSES = (429, 503)
MAX_RETRIES = 3
MAX_RETRY_WAIT_SEC = 60.0
# The API keeps a long conversion open for up to 300 seconds
READ_TIMEOUT_SEC = 330.0


class ApiError(Exception):
    """A failure the API reported (or a network failure, status 0)."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        next_step: str | None = None,
        purchase: dict[str, str] | None = None,
        usage: dict[str, Any] | None = None,
        retry_after: float | None = None,
        request_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.next_step = next_step
        self.purchase = purchase or {}
        self.usage = usage
        self.retry_after = retry_after
        self.request_id = request_id
        self.extra = extra or {}

    @classmethod
    def from_body(cls, status: int, body: Any, *, retry_after: float | None = None) -> ApiError:
        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict):
            return cls(status, "http_error", f"HTTP {status}", retry_after=retry_after)
        extra = {k: v for k, v in error.items() if k not in ("code", "message", "next", "purchase")}
        purchase = error.get("purchase") if isinstance(error.get("purchase"), dict) else None
        return cls(
            status,
            str(error.get("code") or "error"),
            str(error.get("message") or f"HTTP {status}"),
            next_step=error.get("next") if isinstance(error.get("next"), str) else None,
            purchase=purchase,
            usage=body.get("usage") if isinstance(body.get("usage"), dict) else None,
            retry_after=retry_after,
            request_id=body.get("request_id") if isinstance(body.get("request_id"), str) else None,
            extra=extra,
        )


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class ApiClient:
    """One HTTP client per process. Not thread-safe by itself: calls go through the lane."""

    def __init__(
        self,
        config: Config,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._sleep = sleep
        headers = {
            "User-Agent": CLIENT_NAME,
            "X-Zumen-Client": CLIENT_NAME,
            "Accept-Language": config.locale,
        }
        timeout = httpx.Timeout(10.0, read=READ_TIMEOUT_SEC, write=READ_TIMEOUT_SEC)
        # HTTP/2 lets the API receive bodies over 32 MiB (Cloud Run's HTTP/1 limit)
        self._http = httpx.Client(
            base_url=config.api_url,
            headers=headers,
            timeout=timeout,
            http2=transport is None,
            transport=transport,
        )
        # Downloads follow a redirect to temporary storage URLs: a separate client without the key
        self._plain = httpx.Client(
            headers={"User-Agent": CLIENT_NAME},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()
        self._plain.close()

    def _auth(self) -> dict[str, str]:
        if not self.config.api_key:
            return {}
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def _send(self, build: Callable[[], httpx.Request], *, stream: bool = False) -> httpx.Response:
        waited = 0.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._http.send(build(), stream=stream)
            except httpx.HTTPError as exc:
                raise ApiError(
                    0,
                    "network_error",
                    f"Could not reach {self.config.api_url}: {type(exc).__name__}",
                ) from exc
            if response.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                return response
            wait = _retry_after(response)
            if wait is None or waited + wait > MAX_RETRY_WAIT_SEC:
                return response
            response.close()
            self._sleep(wait)
            waited += wait
        raise AssertionError("unreachable")

    def _raise_for_error(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if not response.is_closed:
            response.read()
        raise ApiError.from_body(
            response.status_code, _json_or_none(response), retry_after=_retry_after(response)
        )

    def get_json(self, path: str, *, auth: bool = True) -> dict[str, Any]:
        def build() -> httpx.Request:
            return self._http.build_request("GET", path, headers=self._auth() if auth else {})

        response = self._send(build)
        self._raise_for_error(response)
        body = _json_or_none(response)
        if not isinstance(body, dict):
            raise ApiError(response.status_code, "bad_response", "The API sent no JSON.")
        return body

    def post_json(
        self, path: str, *, files: dict[str, tuple[str, bytes]], data: dict[str, str]
    ) -> dict[str, Any]:
        def build() -> httpx.Request:
            return self._http.build_request(
                "POST", path, headers=self._auth(), files=files, data=data
            )

        response = self._send(build)
        self._raise_for_error(response)
        body = _json_or_none(response)
        if not isinstance(body, dict):
            raise ApiError(response.status_code, "bad_response", "The API sent no JSON.")
        return body

    def post_stream(
        self, path: str, *, files: dict[str, tuple[str, bytes]], data: dict[str, str]
    ) -> Iterator[dict[str, Any]]:
        """POST and yield the NDJSON lines (accepted, heartbeat, then result or error)."""

        def build() -> httpx.Request:
            return self._http.build_request(
                "POST", path, headers=self._auth(), files=files, data=data
            )

        response = self._send(build, stream=True)
        try:
            self._raise_for_error(response)
            for line in response.iter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
        except httpx.HTTPError as exc:
            raise ApiError(
                0, "network_error", f"The connection to the API broke: {type(exc).__name__}"
            ) from exc
        finally:
            response.close()

    def download(self, path: str, dest: Path) -> int:
        """GET an output (the API answers with a redirect to storage) and write it to dest."""

        def build() -> httpx.Request:
            return self._http.build_request("GET", path, headers=self._auth())

        response = self._send(build)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            response.close()
            if not location:
                raise ApiError(response.status_code, "bad_response", "Redirect without a location.")
            # The storage URL is signed: never send the API key there
            try:
                response = self._plain.get(location)
            except httpx.HTTPError as exc:
                raise ApiError(
                    0, "network_error", f"Download failed: {type(exc).__name__}"
                ) from exc
            if response.status_code >= 400:
                raise ApiError(response.status_code, "download_failed", "The download failed.")
        else:
            self._raise_for_error(response)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(response.content)
        tmp.replace(dest)
        return len(response.content)
