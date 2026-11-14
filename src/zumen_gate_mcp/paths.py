"""Which local files may be sent, and where outputs go.

Only files inside --root are read (symbolic links that lead outside are refused), and only files
that look like drawings by their extension and, where the format has one, their leading bytes.
The original drawing is never overwritten: outputs go to a separate folder.
"""

from __future__ import annotations

from pathlib import Path

IR_SUFFIXES = (".ir.json.gz", ".ir.json")
# Used when the format table cannot be fetched (it has the same extensions)
FALLBACK_EXTENSIONS = frozenset(
    {
        ".jww", ".jwc", ".jws", ".dwg", ".dxf", ".sfc", ".p21", ".dgn", ".dwf", ".dwfx",
        ".bi", ".mi", ".idw", ".exb", ".hgl", ".hp2", ".hpg", ".hpgl", ".pcl", ".plt", ".prn",
        ".pdf", ".step", ".stp", ".iges", ".igs", ".stl", ".obj", ".sldprt", ".sldasm",
        ".icd", ".x_b", ".x_t", ".xmt_bin", ".xmt_txt", ".ipt", ".edrw", ".eprt", ".easm",
        ".slddrw",
    }
)  # fmt: skip
# Leading bytes of formats that have a fixed signature
MAGIC: dict[str, tuple[bytes, ...]] = {
    ".jww": (b"JwwData.",),
    ".dwg": (b"AC10", b"AC1.", b"AC2"),
    ".pdf": (b"%PDF-",),
    ".p21": (b"ISO-10303-21",),
    ".step": (b"ISO-10303-21",),
    ".stp": (b"ISO-10303-21",),
}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class PathError(ValueError):
    pass


def resolve_inside(root: Path, value: str, *, must_exist: bool = True) -> Path:
    """The real path of value (relative to root or absolute), refused if it leaves root."""
    if not isinstance(value, str) or not value.strip():
        raise PathError("Give the path of a drawing file.")
    candidate = Path(value.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        real = candidate.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise PathError(f"File not found: {value}") from exc
    except OSError as exc:
        raise PathError(f"Cannot open {value}: {exc.strerror or exc}") from exc
    root_real = root.resolve()
    if real != root_real and root_real not in real.parents:
        raise PathError(
            f"{value} is outside the working folder ({root_real}). Only files inside it can be "
            "used (start the server with --root to change the folder)."
        )
    return real


def is_ir_name(path: Path) -> bool:
    return path.name.lower().endswith(IR_SUFFIXES)


def drawing_suffix(path: Path) -> str:
    name = path.name.lower()
    for suffix in IR_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return path.suffix.lower()


def check_drawing(path: Path, extensions: frozenset[str] | set[str] | None = None) -> str:
    """Return 'file' or 'ir' (the API field to send it in), or raise PathError."""
    if not path.is_file():
        raise PathError(f"Not a file: {path}")
    size = path.stat().st_size
    if size == 0:
        raise PathError(f"The file is empty: {path.name}")
    if size > MAX_UPLOAD_BYTES:
        raise PathError(f"{path.name} is larger than 100 MB, the API's limit.")
    suffix = drawing_suffix(path)
    with path.open("rb") as handle:
        head = handle.read(64)
    if suffix in IR_SUFFIXES:
        gz = head.startswith(b"\x1f\x8b")
        if suffix == ".ir.json.gz" and not gz:
            raise PathError(f"{path.name} is not gzip data.")
        if suffix == ".ir.json" and not head.lstrip().startswith(b"{"):
            raise PathError(f"{path.name} is not a JSON document.")
        return "ir"
    allowed = extensions or FALLBACK_EXTENSIONS
    if suffix not in allowed:
        raise PathError(
            f"{path.name}: the extension {suffix or '(none)'} is not a drawing format the API "
            "reads. Call list_formats for the formats."
        )
    signatures = MAGIC.get(suffix)
    if signatures and not any(head.startswith(sig) for sig in signatures):
        raise PathError(f"{path.name} does not look like a {suffix} file (its first bytes differ).")
    if suffix == ".dxf" and not _looks_like_dxf(head):
        raise PathError(f"{path.name} does not look like a DXF file.")
    return "file"


def _looks_like_dxf(head: bytes) -> bool:
    if head.startswith(b"AutoCAD Binary DXF"):
        return True
    text = head.lstrip(b"\xef\xbb\xbf").lstrip()
    # ASCII DXF starts with the group code 0 (or a comment, 999)
    return text.startswith((b"0", b"999"))


def unique_path(path: Path) -> Path:
    """path, or path with -2, -3 ... before the suffix if a file already has that name."""
    if not path.exists():
        return path
    # Keep a multi-part suffix whole (plan.ir.json.gz -> plan-2.ir.json.gz)
    suffix = path.name[-len(drawing_suffix(path)) :] if drawing_suffix(path) else ""
    stem = path.name[: len(path.name) - len(suffix)] if suffix else path.name
    for n in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise PathError(f"Too many files named like {path.name}")


def output_dir(root: Path, default: Path, value: str | None) -> Path:
    """The folder for outputs: the default, or a folder inside root."""
    if not value:
        return default
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    real = candidate.resolve(strict=False)
    root_real = root.resolve()
    if real != root_real and root_real not in real.parents:
        raise PathError(f"The output folder {value} is outside the working folder ({root_real}).")
    return real


def safe_output_name(name: str) -> str:
    """A file name from the API, reduced to its last part (no folders)."""
    base = Path(name.replace("\\", "/")).name
    return base if base not in ("", ".", "..") else "output"
