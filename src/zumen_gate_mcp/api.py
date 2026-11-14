"""The zumen-gate API calls, one function each (formats, inspect, query, render, convert, usage)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import ApiClient, ApiError


@dataclass(frozen=True)
class Payload:
    """What a call sends: a drawing file (field 'file') or an IR document (field 'ir')."""

    field: str
    filename: str
    data: bytes

    def files(self) -> dict[str, tuple[str, bytes]]:
        return {self.field: (self.filename, self.data)}


class ZumenGateApi:
    def __init__(self, client: ApiClient) -> None:
        self.client = client
        self._formats: dict[str, Any] | None = None

    def formats(self) -> dict[str, Any]:
        """The format table. Needs no key and uses no calls; cached for the process."""
        if self._formats is None:
            self._formats = self.client.get_json("/v1/formats", auth=False)
        return self._formats

    def _read(self, op: str, payload: Payload, params: dict[str, Any]) -> dict[str, Any]:
        data = {"params": json.dumps(params, ensure_ascii=False)} if params else {}
        return self.client.post_json(f"/v1/{op}", files=payload.files(), data=data)

    def inspect(self, payload: Payload, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._read("inspect", payload, params or {})

    def query(self, payload: Payload, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._read("query", payload, params or {})

    def render(self, payload: Payload, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._read("render", payload, params or {})

    def convert(
        self,
        payload: Payload,
        target_format: str,
        *,
        target_version: str | None = None,
        target_scale: str | None = None,
        pages: str | None = None,
        source_scale: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Convert with the progress stream (the API keeps the connection open, up to 300 s)."""
        data = {"target_format": target_format, "stream": "true"}
        for key, value in (
            ("target_version", target_version),
            ("target_scale", target_scale),
            ("pages", pages),
            ("source_scale", source_scale),
        ):
            if value:
                data[key] = value
        for event in self.client.post_stream("/v1/convert", files=payload.files(), data=data):
            if on_event is not None:
                on_event(event)
            kind = event.get("type")
            if kind == "result" and isinstance(event.get("record"), dict):
                return event["record"]
            if kind == "error":
                status = event.get("status") if isinstance(event.get("status"), int) else 500
                raise ApiError.from_body(status, event)
        raise ApiError(0, "stream_ended", "The conversion stream ended without a result.")

    def usage(self) -> dict[str, Any]:
        return self.client.get_json("/v1/usage")

    def download(self, download_path: str, dest: Path) -> int:
        return self.client.download(download_path, dest)
