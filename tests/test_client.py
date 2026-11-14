from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from zumen_gate_mcp.api import Payload, ZumenGateApi
from zumen_gate_mcp.client import ApiClient, ApiError
from zumen_gate_mcp.config import Config

URL = "https://api.example.test"


@respx.mock
def test_errors_carry_code_next_step_and_purchase():
    respx.post(f"{URL}/v1/inspect").mock(
        return_value=httpx.Response(
            402,
            json={
                "error": {
                    "code": "paid_quota_required",
                    "message": "Needs paid calls.",
                    "next": "Buy extra calls.",
                    "purchase": {"bundle": "https://buy.example/x"},
                },
                "usage": {"free_remaining": 3, "paid_remaining": 0},
                "request_id": "r1",
            },
        )
    )
    api = ZumenGateApi(ApiClient(Config("zg_live_test", URL, Path("."), "en")))
    with pytest.raises(ApiError) as caught:
        api.inspect(Payload("file", "a.dxf", b"0\nSECTION"))
    error = caught.value
    assert (error.status, error.code, error.next_step) == (
        402,
        "paid_quota_required",
        "Buy extra calls.",
    )
    assert error.purchase == {"bundle": "https://buy.example/x"}
    assert error.usage == {"free_remaining": 3, "paid_remaining": 0}
    assert error.request_id == "r1"


@respx.mock
def test_key_language_and_client_headers():
    route = respx.get(f"{URL}/v1/usage").mock(
        return_value=httpx.Response(200, json={"tier": "free"})
    )
    ApiClient(Config("zg_live_k", URL, Path("."), "ja")).get_json("/v1/usage")
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer zg_live_k"
    assert sent.headers["accept-language"] == "ja"
    assert sent.headers["x-zumen-client"].startswith("zumen-gate-mcp/")
    formats = respx.get(f"{URL}/v1/formats").mock(
        return_value=httpx.Response(200, json={"formats": []})
    )
    ApiClient(Config("zg_live_k", URL, Path("."), "ja")).get_json("/v1/formats", auth=False)
    assert "authorization" not in formats.calls.last.request.headers


def _sleeper():
    waits: list[float] = []
    return waits, waits.append


@respx.mock
def test_429_waits_retry_after_then_succeeds():
    route = respx.get(f"{URL}/v1/usage").mock(
        side_effect=[
            httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json={"error": {"code": "busy", "message": "busy"}},
            ),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    waits, sleep = _sleeper()
    client = ApiClient(Config("zg_live_k", URL, Path("."), "en"), sleep=sleep)
    assert client.get_json("/v1/usage") == {"ok": True}
    assert waits == [2.0]
    assert route.call_count == 2


@respx.mock
def test_long_retry_after_and_other_errors_are_not_retried():
    respx.get(f"{URL}/v1/usage").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "120"},
            json={"error": {"code": "rate_limited", "message": "slow down"}},
        )
    )
    waits, sleep = _sleeper()
    client = ApiClient(Config("zg_live_k", URL, Path("."), "en"), sleep=sleep)
    with pytest.raises(ApiError) as caught:
        client.get_json("/v1/usage")
    assert caught.value.code == "rate_limited" and caught.value.retry_after == 120.0
    assert waits == []
    route = respx.post(f"{URL}/v1/query").mock(
        return_value=httpx.Response(
            415, json={"error": {"code": "unsupported_format", "message": "no"}}
        )
    )
    with pytest.raises(ApiError):
        client.post_json("/v1/query", files={"file": ("a.xyz", b"x")}, data={})
    assert route.call_count == 1


@respx.mock
def test_downloads_follow_the_redirect_without_the_key(tmp_path):
    respx.get(f"{URL}/v1/files/job1/plan.dxf").mock(
        return_value=httpx.Response(307, headers={"Location": "https://storage.example/signed?x=1"})
    )
    storage = respx.get("https://storage.example/signed").mock(
        return_value=httpx.Response(200, content=b"DXFDATA")
    )
    client = ApiClient(Config("zg_live_secret", URL, Path("."), "en"))
    size = client.download("/v1/files/job1/plan.dxf", tmp_path / "out" / "plan.dxf")
    assert size == 7
    assert (tmp_path / "out" / "plan.dxf").read_bytes() == b"DXFDATA"
    assert "authorization" not in storage.calls.last.request.headers


@respx.mock
def test_convert_reads_the_ndjson_stream():
    lines = [
        {"type": "accepted", "request_id": "r"},
        {"type": "heartbeat", "elapsed": 5.0},
        {"type": "result", "record": {"job_id": "j", "outputs": []}},
    ]
    body = "\n".join(json.dumps(x) for x in lines) + "\n"
    route = respx.post(f"{URL}/v1/convert").mock(
        return_value=httpx.Response(200, content=body.encode())
    )
    seen: list[str] = []
    api = ZumenGateApi(ApiClient(Config("zg_live_k", URL, Path("."), "en")))
    record = api.convert(
        Payload("file", "a.dxf", b"0"),
        "pdf",
        target_scale="1/50",
        on_event=lambda e: seen.append(e["type"]),
    )
    assert record == {"job_id": "j", "outputs": []}
    assert seen == ["accepted", "heartbeat", "result"]
    sent = route.calls.last.request.content
    assert b'name="stream"' in sent and b'name="target_scale"' in sent


@respx.mock
def test_convert_stream_error_becomes_api_error():
    error = {"type": "error", "status": 422, "error": {"code": "bad_ir", "message": "broken"}}
    respx.post(f"{URL}/v1/convert").mock(
        return_value=httpx.Response(200, content=(json.dumps(error) + "\n").encode())
    )
    api = ZumenGateApi(ApiClient(Config("zg_live_k", URL, Path("."), "en")))
    with pytest.raises(ApiError) as caught:
        api.convert(Payload("ir", "a.ir.json.gz", b"x"), "jww")
    assert (caught.value.status, caught.value.code) == (422, "bad_ir")


def test_network_errors_are_api_errors():
    def boom(request):
        raise httpx.ConnectError("down", request=request)

    client = ApiClient(
        Config("zg_live_k", URL, Path("."), "en"), transport=httpx.MockTransport(boom)
    )
    with pytest.raises(ApiError) as caught:
        client.get_json("/v1/usage")
    assert caught.value.code == "network_error"
