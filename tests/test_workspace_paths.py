from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

from zumen_gate_mcp.paths import (
    PathError,
    check_drawing,
    output_dir,
    resolve_inside,
    safe_output_name,
    unique_path,
)
from zumen_gate_mcp.workspace import Workspace, read_ir


def test_versions_undo_and_redo_dropping(tmp_path, jww_doc):
    source = tmp_path / "plan.jww"
    source.write_bytes(b"JwwData." + b"\0" * 100)
    ws = Workspace.locate(tmp_path / ".zumen-gate", tmp_path, source)
    assert not ws.exists
    assert ws.start(jww_doc, "fetched") == 1
    jww_doc["entities"].pop()
    assert ws.add_version(jww_doc, "edit") == 2
    assert len(ws.load()["entities"]) == 2
    assert ws.undo() == 1
    assert len(ws.load()["entities"]) == 3
    # An edit after undo replaces the undone version
    assert ws.add_version(jww_doc, "edit again") == 2
    assert [row["note"] for row in ws.versions] == ["fetched", "edit again"]
    # Survives a new process (state on disk)
    again = Workspace.locate(tmp_path / ".zumen-gate", tmp_path, source)
    assert again.exists and again.current == 2
    with pytest.raises(ValueError):
        again.undo()
        again.undo()


def test_source_change_is_noticed(tmp_path, jww_doc):
    source = tmp_path / "plan.jww"
    source.write_bytes(b"JwwData." + b"\0" * 100)
    ws = Workspace.locate(tmp_path / ".zumen-gate", tmp_path, source)
    ws.start(jww_doc, "fetched")
    assert not ws.source_changed()
    source.write_bytes(b"JwwData." + b"\1" * 120)
    assert ws.source_changed()


def test_discard_removes_the_working_copy(tmp_path, jww_doc):
    source = tmp_path / "new.jww"
    ws = Workspace.locate(tmp_path / ".zumen-gate", tmp_path, source)
    ws.start(jww_doc, "create", blank=True)
    assert ws.exists and not ws.source_changed()
    ws.discard()
    assert not Workspace.locate(tmp_path / ".zumen-gate", tmp_path, source).exists


def test_ir_files_are_gzip(tmp_path, jww_doc):
    ws = Workspace.locate(tmp_path / ".zumen-gate", tmp_path, tmp_path / "a.jww")
    ws.start(jww_doc, "x", blank=True)
    path = ws.current_path()
    assert path is not None and path.read_bytes()[:2] == b"\x1f\x8b"
    assert read_ir(path)["source"]["format"] == "jww"


def test_paths_stay_inside_the_root(tmp_path):
    inside = tmp_path / "a" / "plan.jww"
    inside.parent.mkdir()
    inside.write_bytes(b"JwwData.x")
    assert resolve_inside(tmp_path, "a/plan.jww") == inside.resolve()
    assert resolve_inside(tmp_path, str(inside)) == inside.resolve()
    with pytest.raises(PathError, match="outside"):
        resolve_inside(tmp_path / "a", "../a/../../etc/passwd", must_exist=False)
    with pytest.raises(PathError, match="not found"):
        resolve_inside(tmp_path, "missing.jww")


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_symlinks_out_of_the_root_are_refused(tmp_path):
    outside = tmp_path / "outside.jww"
    outside.write_bytes(b"JwwData.x")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.jww").symlink_to(outside)
    with pytest.raises(PathError, match="outside"):
        resolve_inside(root, "link.jww")


def _file(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_drawings_are_recognised_by_extension_and_bytes(tmp_path):
    assert check_drawing(_file(tmp_path, "a.jww", b"JwwData.\x00")) == "file"
    assert check_drawing(_file(tmp_path, "a.dxf", b"  0\nSECTION\n")) == "file"
    assert check_drawing(_file(tmp_path, "a.pdf", b"%PDF-1.7")) == "file"
    assert check_drawing(_file(tmp_path, "a.ir.json.gz", gzip.compress(b"{}"))) == "ir"
    assert check_drawing(_file(tmp_path, "b.ir.json", b' {"format": "cad2d-ir"}')) == "ir"
    with pytest.raises(PathError, match="does not look like"):
        check_drawing(_file(tmp_path, "fake.jww", b"hello"))
    with pytest.raises(PathError, match="extension"):
        check_drawing(_file(tmp_path, "notes.txt", b"hello"))
    with pytest.raises(PathError, match="empty"):
        check_drawing(_file(tmp_path, "empty.dxf", b""))
    with pytest.raises(PathError, match="gzip"):
        check_drawing(_file(tmp_path, "c.ir.json.gz", b"{}"))


def test_outputs_never_overwrite(tmp_path):
    first = tmp_path / "plan.dxf"
    assert unique_path(first) == first
    first.write_text("x")
    assert unique_path(first) == tmp_path / "plan-2.dxf"
    ir = tmp_path / "plan.ir.json.gz"
    ir.write_text("x")
    assert unique_path(ir) == tmp_path / "plan-2.ir.json.gz"
    assert output_dir(tmp_path, tmp_path / "out", None) == tmp_path / "out"
    assert output_dir(tmp_path, tmp_path / "out", "dxf") == (tmp_path / "dxf").resolve()
    with pytest.raises(PathError, match="outside"):
        output_dir(tmp_path, tmp_path / "out", "../elsewhere")
    assert safe_output_name("../../evil.dxf") == "evil.dxf"
    assert safe_output_name("a\\b\\c.dxf") == "c.dxf"
