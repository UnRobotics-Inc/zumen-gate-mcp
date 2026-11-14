# zumen-gate-mcp

<!-- mcp-name: com.zumen-gate/zumen-gate-mcp -->

An MCP server and command-line tool for the [zumen-gate](https://zumen-gate.com/developers)
drawing API. It lets Claude and other MCP clients read, search, render, edit and convert CAD
drawings: Jw_cad (JWW), DWG, DXF, SXF (SFC/P21), PDF, and more.

[日本語の説明](README.ja.md)

- **Read**: format, paper, scale, layers, entity counts and texts of a drawing.
- **Search**: entities by layer, kind, text or area, with lengths and areas in real millimetres.
- **Render**: a PNG of the drawing or a part of it, with the pixel-to-coordinate mapping.
- **Edit**: change texts, layers, colors and coordinates, delete and add entities, or draw a new
  JWW or DXF from scratch, then write it back in its original format.
- **Convert**: between drawing formats (DXF, DWG, JWW, SFC, P21, PDF, SVG, PNG, and STL/OBJ/STEP for
  3D models).

Reading and converting run on the zumen-gate API with a deterministic CAD engine (no generative AI
on the server). Edits run locally on a working copy of the drawing.

## Get an API key

Register for free at [zumen-gate.com](https://zumen-gate.com/developers) and create a key under
*Account → API keys*. The free plan includes a monthly allowance of calls; see the
[price list](https://zumen-gate.com/developers) for paid outputs and more calls.

## Install

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (`uvx` runs the server
without a separate install). Replace `zg_live_...` with your key. `--root` is the folder whose
drawings the tools may read; outputs are written to `zumen-gate-out/` inside it.

**Claude Code**

```sh
claude mcp add zumen-gate -e ZUMEN_GATE_API_KEY=zg_live_... -- uvx zumen-gate-mcp --root .
```

**Claude Desktop** (`claude_desktop_config.json`), **Cursor** (`.cursor/mcp.json`) and other
clients that use the same format:

```json
{
  "mcpServers": {
    "zumen-gate": {
      "command": "uvx",
      "args": ["zumen-gate-mcp", "--root", "/path/to/drawings"],
      "env": { "ZUMEN_GATE_API_KEY": "zg_live_..." }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.zumen-gate]
command = "uvx"
args = ["zumen-gate-mcp", "--root", "/path/to/drawings"]
env = { ZUMEN_GATE_API_KEY = "zg_live_..." }
```

**VS Code**

```sh
code --add-mcp '{"name":"zumen-gate","command":"uvx","args":["zumen-gate-mcp","--root","${workspaceFolder}"],"env":{"ZUMEN_GATE_API_KEY":"zg_live_..."}}'
```

**Claude Code plugin**: this repository is also a plugin with a skill that teaches the model how to
work with drawings. Add it with `/plugin` from a marketplace that lists it, or copy
`skills/zumen-gate/SKILL.md` into your project's `.claude/skills/`.

## Tools

| Tool | What it does | Calls |
|---|---|---|
| `list_formats` | Formats, extensions, which can be read and written, paid outputs | 0 |
| `inspect_drawing` | Summary: format, paper, scale, layers, counts, texts | 1 |
| `query_drawing` | Entities matching layer / kind / text / area, with totals | 1 |
| `render_drawing` | PNG image and pixel-to-coordinate mapping | 1 |
| `edit_drawing` | Local edits on a working copy, blank drawings, undo | 0 (1 the first time) |
| `convert_drawing` | Convert and save the outputs | 1, or 10 for paid outputs |
| `get_job` | State of a long conversion | 0 |
| `account_status` | Calls left, expiry dates, this month's use | 0 |

Every answer includes the calls counted and the calls left. Writing JWW, DWG, SFC, P21 or STEP is a
paid output; without enough paid calls the conversion returns a preview and a loss report instead.

## Command line

The same operations are available as `zumen-gate`:

```sh
export ZUMEN_GATE_API_KEY=zg_live_...
zumen-gate inspect plan.jww
zumen-gate query plan.jww --layer 壁 --kind LINE
zumen-gate render plan.jww --bbox 0 0 120 80
zumen-gate edit plan.jww --ops '[{"op": "replace_text", "pattern": "Old Co.", "replacement": "New Co."}]'
zumen-gate convert *.jww --to dxf
zumen-gate account
```

## Settings

| Setting | Meaning |
|---|---|
| `ZUMEN_GATE_API_KEY` | Your API key (required, except for `list_formats`) |
| `ZUMEN_GATE_LOCALE` | `ja` or `en` for messages (default: from the system language) |
| `--root` | The working folder (default: the current folder) |
| `--only-text-feedback` | Return `render_drawing` as text only (no image) |

## How files are handled

- Only files inside `--root` are read; symbolic links that lead outside are refused. Files are sent
  only if their extension (and, where the format has one, their signature) says they are drawings.
- The original drawing is never modified. Edits are kept as versions in `.zumen-gate/` inside the
  working folder; outputs go to `zumen-gate-out/`.
- Text read from drawings is marked as data for the model, not instructions.
- See the zumen-gate [terms](https://zumen-gate.com/terms) and
  [privacy policy](https://zumen-gate.com/privacy) for how the API handles uploaded drawings.

## Development

```sh
uv sync
uv run pytest
uv run ruff check src tests
```

## License

MIT
