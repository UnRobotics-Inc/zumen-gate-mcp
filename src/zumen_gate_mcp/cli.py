"""The command-line tool: the same operations as the MCP tools.

    zumen-gate inspect plan.jww
    zumen-gate query plan.jww --layer 壁 --kind LINE
    zumen-gate render plan.jww --bbox 0 0 100 80
    zumen-gate edit plan.jww --ops '[{"op": "replace_text", "pattern": "旧", "replacement": "新"}]'
    zumen-gate convert *.jww --to dxf
    zumen-gate account

The API key comes from the ZUMEN_GATE_API_KEY environment variable.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .config import load_config
from .tools import ToolOutput, Tools


def _print(output: ToolOutput, raw: bool) -> int:
    text = output.text
    if not raw:
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pass
    stream = sys.stderr if output.is_error else sys.stdout
    print(text, file=stream)
    return 1 if output.is_error else 0


def _expand(paths: list[str]) -> list[str]:
    """Expand wildcards ourselves too (Windows shells do not)."""
    out: list[str] = []
    for value in paths:
        matches = sorted(glob.glob(value)) if any(ch in value for ch in "*?[") else []
        out.extend(matches or [value])
    return out


def _json_arg(value: str | None, name: str) -> Any:
    if value is None:
        return None
    text = value
    if not value.lstrip().startswith(("[", "{")) and Path(value).is_file():
        text = Path(value).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SystemExit(f"{name}: not valid JSON ({exc})") from exc


def build_parser() -> argparse.ArgumentParser:
    root_help = "the working folder (default: current folder)"
    raw_help = "print the JSON on one line"
    parser = argparse.ArgumentParser(prog="zumen-gate", description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None, help=root_help)
    parser.add_argument("--raw", action="store_true", help=raw_help)
    parser.add_argument("--version", action="version", version=f"zumen-gate {__version__}")
    # --root and --raw also work after the command (zumen-gate formats --raw). SUPPRESS keeps a
    # subcommand from resetting a value given before the command.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help=root_help)
    common.add_argument("--raw", action="store_true", default=argparse.SUPPRESS, help=raw_help)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, parents=[common])

    command("formats", "list the formats (no key needed)")

    p = command("inspect", "summarize a drawing (1 call)")
    p.add_argument("path")
    p.add_argument("--pages")
    p.add_argument("--sheet")
    p.add_argument("--original", action="store_true", help="send the file, not the working copy")

    p = command("query", "list matching entities (1 call)")
    p.add_argument("path")
    p.add_argument("--layer", action="append")
    p.add_argument("--kind", action="append")
    p.add_argument("--text")
    p.add_argument("--regex", action="store_true", help="--text is a regular expression")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    p.add_argument("--within", action="store_true", help="only entities entirely inside --bbox")
    p.add_argument("--ids", nargs="+")
    p.add_argument("--entities", action="store_true", help="include each entity in IR form")
    p.add_argument("--limit", type=int)
    p.add_argument("--cursor")
    p.add_argument("--original", action="store_true")

    p = command("render", "draw as PNG into the output folder (1 call)")
    p.add_argument("path")
    p.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    p.add_argument("--layers", nargs="+")
    p.add_argument("--highlight", nargs="+")
    p.add_argument("--width", type=int)
    p.add_argument("--original", action="store_true")

    p = command("edit", "edit the working copy locally")
    p.add_argument("path")
    p.add_argument("--ops", help="the operations as JSON, or a JSON file")
    p.add_argument("--create", help='start a blank drawing: JSON like {"format": "jww"}')
    p.add_argument("--undo", action="store_true")
    p.add_argument("--reset", action="store_true")

    p = command("convert", "convert drawings into the output folder")
    p.add_argument("paths", nargs="+")
    p.add_argument("--to", required=True)
    p.add_argument("--version", dest="target_version")
    p.add_argument("--scale")
    p.add_argument("--pages")
    p.add_argument("--out-dir")
    p.add_argument("--original", action="store_true")

    command("account", "calls left and this month's use")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.root)
    # The command line waits for long conversions instead of handing back a job ID
    tools = Tools(replace(config, long_wait_sec=3600.0))
    raw = args.raw
    command = args.command
    if command == "formats":
        return _print(tools.list_formats(), raw)
    if command == "inspect":
        return _print(
            tools.inspect_drawing(
                args.path, use_edits=not args.original, pages=args.pages, sheet=args.sheet
            ),
            raw,
        )
    if command == "query":
        return _print(
            tools.query_drawing(
                args.path,
                layer=args.layer,
                kind=args.kind,
                text=args.text,
                text_regex=args.regex,
                bbox=args.bbox,
                bbox_mode="within" if args.within else None,
                ids=args.ids,
                include_entities=args.entities,
                limit=args.limit,
                cursor=args.cursor,
                use_edits=not args.original,
            ),
            raw,
        )
    if command == "render":
        output = tools.render_drawing(
            args.path,
            bbox=args.bbox,
            layers=args.layers,
            highlight_ids=args.highlight,
            width_px=args.width,
            use_edits=not args.original,
        )
        return _print(replace(output, image=None), raw)
    if command == "edit":
        return _print(
            tools.edit_drawing(
                args.path,
                ops=_json_arg(args.ops, "--ops"),
                create=_json_arg(args.create, "--create"),
                undo=args.undo,
                reset=args.reset,
            ),
            raw,
        )
    if command == "convert":
        status = 0
        paths = _expand(args.paths)
        for n, path in enumerate(paths, 1):
            if len(paths) > 1:
                print(f"[{n}/{len(paths)}] {path}", file=sys.stderr)
            status |= _print(
                tools.convert_drawing(
                    path,
                    args.to,
                    version=args.target_version,
                    scale=args.scale,
                    pages=args.pages,
                    use_edits=not args.original,
                    out_dir=args.out_dir,
                ),
                raw,
            )
        return status
    if command == "account":
        return _print(tools.account_status(), raw)
    return 2


if __name__ == "__main__":
    sys.exit(main())
