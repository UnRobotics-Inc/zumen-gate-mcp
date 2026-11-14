"""Working copies of drawings as IR, in versions.

The AI always names the original drawing. The first edit fetches the drawing as IR from the API
(one call) and keeps it in <root>/.zumen-gate/<drawing>/v0001.ir.json.gz; every edit adds a version,
and undo goes back one. While a working copy exists, the other tools send it instead of the
original (use_edits=false sends the original). If the original file changes after the copy was
made, the tools say so.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"
MAX_VERSIONS = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ir(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("The IR is not a JSON object.")
    return doc


def write_ir(doc: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(gzip.compress(data, compresslevel=6, mtime=0))
    tmp.replace(path)


def _folder_name(relpath: str) -> str:
    name = Path(relpath).name or "drawing"
    safe = re.sub(r"[^\w.\-]+", "_", name, flags=re.UNICODE)[:80]
    digest = hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}"


@dataclass
class Workspace:
    folder: Path
    source: Path
    relpath: str
    state: dict[str, Any]

    @classmethod
    def locate(cls, work_dir: Path, root: Path, source: Path) -> Workspace:
        """The workspace of source (it may not exist yet: see exists)."""
        relpath = source.relative_to(root).as_posix()
        folder = work_dir / _folder_name(relpath)
        state: dict[str, Any] = {}
        state_path = folder / STATE_FILE
        if state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                state = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                state = {}
        return cls(folder=folder, source=source, relpath=relpath, state=state)

    # ---- state ----
    @property
    def exists(self) -> bool:
        return self.current > 0 and self.current_path() is not None

    @property
    def current(self) -> int:
        value = self.state.get("current", 0)
        return value if isinstance(value, int) and value > 0 else 0

    @property
    def versions(self) -> list[dict[str, Any]]:
        rows = self.state.get("versions")
        return rows if isinstance(rows, list) else []

    def current_path(self) -> Path | None:
        for row in self.versions:
            if row.get("n") == self.current:
                path = self.folder / str(row.get("file", ""))
                return path if path.is_file() else None
        return None

    def load(self) -> dict[str, Any]:
        path = self.current_path()
        if path is None:
            raise FileNotFoundError("No working copy yet.")
        return read_ir(path)

    def _save_state(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.folder / (STATE_FILE + ".part")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.folder / STATE_FILE)

    # ---- versions ----
    def start(self, doc: dict[str, Any], note: str, *, blank: bool = False) -> int:
        """First version: the IR fetched from the API, or a blank drawing."""
        self.state = {
            "source": self.relpath,
            "blank": blank,
            "created_at": _now(),
            "current": 0,
            "versions": [],
        }
        if not blank and self.source.is_file():
            stat = self.source.stat()
            self.state.update(
                source_sha256=sha256_of(self.source),
                source_size=stat.st_size,
                source_mtime=stat.st_mtime,
            )
        return self.add_version(doc, note)

    def add_version(self, doc: dict[str, Any], note: str) -> int:
        """Save doc as the next version; versions after the current one (undone) are dropped."""
        kept = [
            row
            for row in self.versions
            if isinstance(row.get("n"), int) and row["n"] <= self.current
        ]
        for row in self.versions:
            if row not in kept:
                (self.folder / str(row.get("file", ""))).unlink(missing_ok=True)
        number = self.current + 1
        filename = f"v{number:04d}.ir.json.gz"
        write_ir(doc, self.folder / filename)
        kept.append({"n": number, "file": filename, "created_at": _now(), "note": note[:200]})
        # Keep the first version (the drawing as fetched) and the latest ones
        while len(kept) > MAX_VERSIONS:
            victim = kept.pop(1)
            (self.folder / str(victim.get("file", ""))).unlink(missing_ok=True)
        self.state["versions"] = kept
        self.state["current"] = number
        self._save_state()
        return number

    def undo(self) -> int:
        """Go back one version. Returns the new current version (0 when nothing is left)."""
        numbers = sorted(row["n"] for row in self.versions if isinstance(row.get("n"), int))
        older = [n for n in numbers if n < self.current]
        if not older:
            raise ValueError("There is no earlier version to go back to.")
        self.state["current"] = older[-1]
        self._save_state()
        return self.state["current"]

    def discard(self) -> None:
        for row in self.versions:
            (self.folder / str(row.get("file", ""))).unlink(missing_ok=True)
        (self.folder / STATE_FILE).unlink(missing_ok=True)
        try:
            self.folder.rmdir()
        except OSError:
            pass
        self.state = {}

    def source_changed(self) -> bool:
        """True when the original drawing differs from the one the working copy came from."""
        if self.state.get("blank") or not self.source.is_file():
            return False
        recorded = self.state.get("source_sha256")
        if not isinstance(recorded, str):
            return False
        stat = self.source.stat()
        if stat.st_size == self.state.get("source_size") and stat.st_mtime == self.state.get(
            "source_mtime"
        ):
            return False
        return sha256_of(self.source) != recorded
