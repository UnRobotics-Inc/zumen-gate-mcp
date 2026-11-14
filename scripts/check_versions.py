"""Check that every place that names the version agrees (and, with --tag, the release tag).

    python scripts/check_versions.py
    python scripts/check_versions.py --tag v0.1.0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="", help="the release tag, e.g. v0.1.0 (empty: skip)")
    args = parser.parse_args()
    init = (ROOT / "src" / "zumen_gate_mcp" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE)
    if not match:
        print("no __version__ in src/zumen_gate_mcp/__init__.py", file=sys.stderr)
        return 1
    version = match.group(1)
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    found = {
        "src/zumen_gate_mcp/__init__.py": version,
        "server.json version": server.get("version"),
        "server.json package": server.get("packages", [{}])[0].get("version"),
        ".claude-plugin/plugin.json": plugin.get("version"),
    }
    if args.tag:
        found["tag"] = args.tag.removeprefix("v")
    if "## " + version not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        print(f"CHANGELOG.md has no section for {version}", file=sys.stderr)
        return 1
    wrong = {where: value for where, value in found.items() if value != version}
    if wrong:
        for where, value in wrong.items():
            print(f"{where}: {value} (expected {version})", file=sys.stderr)
        return 1
    print(f"version {version} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
