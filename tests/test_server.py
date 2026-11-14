"""The MCP layer: tool list, text and image content, and errors flagged as errors."""

from __future__ import annotations

import anyio
from mcp.client.client import Client

from zumen_gate_mcp.config import Config
from zumen_gate_mcp.server import build_server
from zumen_gate_mcp.tools import Tools

from .test_tools import FakeApi

TOOL_NAMES = {
    "list_formats",
    "inspect_drawing",
    "query_drawing",
    "render_drawing",
    "edit_drawing",
    "convert_drawing",
    "get_job",
    "account_status",
}


def _tools(tmp_path, jww_doc, *, only_text: bool = False) -> Tools:
    (tmp_path / "plan.jww").write_bytes(b"JwwData." + b"\0" * 32)
    config = Config("zg_live_k", "https://x", tmp_path, "en", only_text_feedback=only_text)
    return Tools(config, api=FakeApi(jww_doc))


def test_tools_and_content(tmp_path, jww_doc):
    async def main() -> None:
        async with Client(build_server(_tools(tmp_path, jww_doc))) as client:
            listed = {t.name for t in (await client.list_tools()).tools}
            assert listed == TOOL_NAMES
            result = await client.call_tool("render_drawing", {"path": "plan.jww"})
            assert not result.is_error
            assert [c.type for c in result.content] == ["text", "image"]
            assert result.content[1].mime_type == "image/png"
            result = await client.call_tool("inspect_drawing", {"path": "../x.jww"})
            assert result.is_error and "bad_path" in result.content[0].text
            result = await client.call_tool(
                "edit_drawing",
                {
                    "path": "plan.jww",
                    "ops": [{"op": "set", "where": {"layer": "壁"}, "fields": {"color": 5}}],
                },
            )
            assert not result.is_error and '"changed":2' in result.content[0].text

    anyio.run(main)


def test_the_command_runs_over_stdio(tmp_path):
    """The installed entry point starts and answers over stdio (no API needed to list tools)."""
    import os
    import sys

    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "zumen_gate_mcp.server", "--root", str(tmp_path)],
        env={**os.environ, "ZUMEN_GATE_API_KEY": "", "ZUMEN_GATE_LOCALE": "en"},
    )

    async def main() -> None:
        async with Client(params) as client:
            listed = {t.name for t in (await client.list_tools()).tools}
            assert listed == TOOL_NAMES
            result = await client.call_tool("account_status", {})
            assert result.is_error and "ZUMEN_GATE_API_KEY" in result.content[0].text

    anyio.run(main)


def test_only_text_feedback_drops_images(tmp_path, jww_doc):
    async def main() -> None:
        async with Client(build_server(_tools(tmp_path, jww_doc, only_text=True))) as client:
            result = await client.call_tool("render_drawing", {"path": "plan.jww"})
            assert [c.type for c in result.content] == ["text"]

    anyio.run(main)
