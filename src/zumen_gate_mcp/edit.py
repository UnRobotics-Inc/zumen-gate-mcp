"""Edits on an IR document (cad2d-ir JSON), done locally without calling the API.

Operations: set, delete, add, replace_text, add_layer, and create (a blank drawing).

Drawings that came from Jw_cad keep the original pen of each entity in metadata.jww, and the JWW
writer prefers it over the IR values so that an untouched drawing is written back as it was. When
an edit changes an entity's color, line type or line width, the matching Jw_cad values are removed
so that the writer takes the new IR value. Moving an entity to another layer, and new layers, need
nothing: the writer places layers itself.

Only the shape of the edits is checked here. The API checks the whole document against the IR
schema when it receives it.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

ID_PREFIX = "ZGM_E"
CURRENT_IR_VERSION = "0.3.0"

# Jw_cad values that hold an entity's color, line type and line width (see the module docstring)
JW_PEN_KEYS: dict[str, tuple[str, ...]] = {
    "color": ("pen_color", "custom_color"),
    "linetype": ("pen_style",),
    "lineweight_mm": ("pen_width",),
}
JW_SOURCES = ("jww", "jws")

COMMON_FIELDS = frozenset(
    {"layer", "color", "linetype", "lineweight_mm", "linetype_scale", "visible"}
)
KIND_FIELDS: dict[str, frozenset[str]] = {
    "LINE": frozenset({"p1", "p2"}),
    "CIRCLE": frozenset({"center", "radius"}),
    "ARC": frozenset({"center", "radius", "start_angle", "end_angle", "ccw"}),
    "POINT": frozenset({"position"}),
    "LWPOLYLINE": frozenset({"vertices", "closed"}),
    "TEXT": frozenset(
        {"insert", "height", "rotation", "text", "style", "halign", "valign", "width_factor"}
    ),
    "MTEXT": frozenset({"insert", "height", "rotation", "width", "text", "style", "attach"}),
    "INSERT": frozenset({"insert", "rotation", "scale"}),
}
REQUIRED: dict[str, tuple[str, ...]] = {
    "LINE": ("p1", "p2"),
    "CIRCLE": ("center", "radius"),
    "ARC": ("center", "radius", "start_angle", "end_angle"),
    "POINT": ("position",),
    "LWPOLYLINE": ("vertices",),
    "TEXT": ("insert", "height", "text"),
    "MTEXT": ("insert", "height", "text"),
}
ADDABLE_KINDS = frozenset(REQUIRED)
TEXT_KINDS = frozenset({"TEXT", "MTEXT"})
POINT_FIELDS = frozenset({"p1", "p2", "center", "position", "insert"})
POSITIVE_FIELDS = frozenset({"radius", "height"})
NUMBER_FIELDS = frozenset(
    {"start_angle", "end_angle", "rotation", "width_factor", "width", "linetype_scale"}
)
STRING_FIELDS = frozenset({"text", "style", "linetype", "halign", "valign", "attach"})
BOOL_FIELDS = frozenset({"ccw", "closed", "visible"})

# Jw_cad paper size codes (header.metadata.jww.paper_size) and their sizes in mm
JW_PAPERS: dict[str, tuple[int, tuple[float, float]]] = {
    "A4": (4, (297.0, 210.0)),
    "A3": (3, (420.0, 297.0)),
    "A2": (2, (594.0, 420.0)),
    "A1": (1, (841.0, 594.0)),
    "A0": (0, (1189.0, 841.0)),
    "2A": (8, (1682.0, 1189.0)),
    "3A": (9, (2378.0, 1682.0)),
    "4A": (10, (3364.0, 2378.0)),
    "5A": (11, (4756.0, 3364.0)),
}
UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "inch": 25.4, "ft": 304.8}


class EditError(ValueError):
    pass


@dataclass
class EditReport:
    changed: int = 0
    deleted: int = 0
    texts_replaced: int = 0
    added: list[dict[str, Any]] = field(default_factory=list)
    layers_added: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "changed": self.changed,
            "deleted": self.deleted,
            "texts_replaced": self.texts_replaced,
            "added": self.added,
        }
        if self.layers_added:
            out["layers_added"] = self.layers_added
        if self.notes:
            out["notes"] = self.notes
        return out


# ---- the document ----------------------------------------------------------------------------


def entities(doc: dict[str, Any]) -> list[Any]:
    items = doc.get("entities")
    if not isinstance(items, list):
        items = []
        doc["entities"] = items
    return items


def layer_table(doc: dict[str, Any]) -> dict[str, Any]:
    tables = doc.get("tables")
    if not isinstance(tables, dict):
        tables = {}
        doc["tables"] = tables
    layers = tables.get("layers")
    if not isinstance(layers, dict):
        layers = {}
        tables["layers"] = layers
    return layers


def source_format(doc: dict[str, Any]) -> str:
    source = doc.get("source")
    return str(source.get("format", "")).lower() if isinstance(source, dict) else ""


def _jw_meta(item: dict[str, Any]) -> dict[str, Any] | None:
    meta = item.get("metadata")
    jww = meta.get("jww") if isinstance(meta, dict) else None
    return jww if isinstance(jww, dict) else None


def drop_jw_pen(entity: dict[str, Any], prop: str) -> None:
    """After changing prop (color / linetype / lineweight_mm), let the JWW writer use the IR value."""
    jww = _jw_meta(entity)
    if jww is not None:
        for key in JW_PEN_KEYS[prop]:
            jww.pop(key, None)


def _ensure_layer(doc: dict[str, Any], name: str, report: EditReport) -> None:
    table = layer_table(doc)
    if name not in table:
        table[name] = {}
        report.layers_added.append(name)


def _next_ids(doc: dict[str, Any]) -> Iterable[str]:
    pattern = re.compile(rf"^{ID_PREFIX}(\d+)$")
    taken = set()
    highest = 0
    for e in entities(doc):
        if isinstance(e, dict) and e.get("id") is not None:
            ident = str(e["id"])
            taken.add(ident)
            match = pattern.match(ident)
            if match:
                highest = max(highest, int(match.group(1)))
    n = highest
    while True:
        n += 1
        candidate = f"{ID_PREFIX}{n:06d}"
        if candidate not in taken:
            yield candidate


# ---- real lengths ----------------------------------------------------------------------------


def mm_per_unit(doc: dict[str, Any], entity: dict[str, Any]) -> float | None:
    """How many real millimetres one IR unit is for entity, or None when unknown."""
    header = doc.get("header") if isinstance(doc.get("header"), dict) else {}
    units = str(header.get("units", "mm"))
    if units == "custom":
        unit_mm = header.get("unit_scale_to_mm")
        unit_mm = float(unit_mm) if isinstance(unit_mm, (int, float)) and unit_mm > 0 else None
    else:
        unit_mm = UNIT_MM.get(units)
    if unit_mm is None:
        return None
    if source_format(doc) not in JW_SOURCES:
        return unit_mm
    # Jw_cad: coordinates are millimetres on paper; each layer group has its own scale. A layer
    # without Jw_cad numbers (added by an edit) is placed by the JWW writer in the group being
    # written to (header.metadata.jww.write_layer_group), so it takes that group's scale.
    table = layer_table(doc)
    own = table.get(str(entity.get("layer", "")))
    own_jw = _jw_meta(own) if isinstance(own, dict) else None
    scale = _jw_scale(own_jw)
    if scale is None:
        group = own_jw.get("layer_group") if own_jw else None
        if not _jw_index(group):
            header_jw = (
                header.get("metadata", {}).get("jww")
                if isinstance(header.get("metadata"), dict)
                else None
            )
            group = header_jw.get("write_layer_group", 0) if isinstance(header_jw, dict) else 0
        for layer in table.values():
            jww = _jw_meta(layer) if isinstance(layer, dict) else None
            if jww and jww.get("layer_group") == group and _jw_scale(jww) is not None:
                scale = _jw_scale(jww)
                break
    if scale is None:
        scales = {
            s
            for layer in table.values()
            if (s := _jw_scale(_jw_meta(layer) if isinstance(layer, dict) else None))
        }
        scale = next(iter(scales)) if len(scales) == 1 else None
    return None if scale is None else unit_mm * scale


def _jw_scale(jww: dict[str, Any] | None) -> float | None:
    value = jww.get("group_scale") if jww else None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _jw_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 16


def ir_length(entity: dict[str, Any]) -> float | None:
    kind = str(entity.get("kind", "")).upper()
    try:
        if kind == "LINE":
            (x1, y1), (x2, y2) = entity["p1"], entity["p2"]
            return math.hypot(x2 - x1, y2 - y1)
        if kind == "CIRCLE":
            return 2 * math.pi * float(entity["radius"])
        if kind == "ARC":
            start, end = float(entity["start_angle"]), float(entity["end_angle"])
            sweep = (end - start) % 360.0 if entity.get("ccw", True) else (start - end) % 360.0
            return math.radians(sweep or 360.0) * float(entity["radius"])
        if kind == "LWPOLYLINE":
            vertices = entity["vertices"]
            pairs = list(zip(vertices, vertices[1:], strict=False))
            if entity.get("closed") and len(vertices) > 2:
                pairs.append((vertices[-1], vertices[0]))
            total = 0.0
            for a, b in pairs:
                chord = math.hypot(b[0] - a[0], b[1] - a[1])
                bulge = a[2] if len(a) > 2 else 0.0
                if bulge:
                    theta = 4 * math.atan(abs(bulge))
                    total += chord * theta / (2 * math.sin(theta / 2))
                else:
                    total += chord
            return total
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return None


# ---- checks ----------------------------------------------------------------------------------


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _point(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(map(_number, value))


def _vertex(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) in (2, 3) and all(map(_number, value))


def _color(value: Any) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        return 0 <= value <= 256
    return (
        isinstance(value, str)
        and re.fullmatch(r"#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})", value) is not None
    )


def _check_field(name: str, value: Any, where: str) -> Any:
    ok = True
    if name in POINT_FIELDS:
        ok = _point(value)
        value = [float(value[0]), float(value[1])] if ok else value
    elif name == "vertices":
        ok = isinstance(value, list) and len(value) >= 2 and all(map(_vertex, value))
    elif name in POSITIVE_FIELDS:
        ok = _number(value) and value > 0
    elif name in NUMBER_FIELDS:
        ok = _number(value)
    elif name in STRING_FIELDS:
        ok = isinstance(value, str)
    elif name in BOOL_FIELDS:
        ok = isinstance(value, bool)
    elif name == "layer":
        ok = isinstance(value, str) and value.strip() != ""
    elif name == "color":
        ok = _color(value)
    elif name == "lineweight_mm":
        ok = _number(value) and value >= 0
    elif name == "scale":
        ok = _number(value) or (isinstance(value, list) and all(map(_number, value)))
    if not ok:
        raise EditError(f"{where}: {name} has a wrong value ({str(value)[:60]}).")
    return value


# ---- selection -------------------------------------------------------------------------------


def _as_set(value: Any, name: str) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return set(value)
    raise EditError(f"where.{name} must be a string or a list of strings.")


def _entity_text(entity: dict[str, Any]) -> str:
    text = entity.get("text")
    return text if isinstance(text, str) else ""


def select(doc: dict[str, Any], op: dict[str, Any], where: str) -> list[int]:
    """Indexes of the entities named by op['ids'] or matched by op['where']."""
    ids = op.get("ids")
    cond = op.get("where")
    if ids is None and cond is None:
        raise EditError(f"{where}: give ids, or where with layer / kind / text.")
    if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
        raise EditError(f"{where}: ids must be a list of entity IDs.")
    layers = kinds = None
    text = None
    if cond is not None:
        if not isinstance(cond, dict) or not cond:
            raise EditError(f"{where}: where needs at least one of layer, kind, text.")
        unknown = set(cond) - {"layer", "kind", "text"}
        if unknown:
            raise EditError(
                f"{where}: where takes layer, kind and text (not {', '.join(sorted(unknown))}). "
                "To pick by area, get the IDs with query_drawing first."
            )
        layers = _as_set(cond.get("layer"), "layer")
        kinds = {k.upper() for k in (_as_set(cond.get("kind"), "kind") or set())} or None
        text = cond.get("text")
        if text is not None and (not isinstance(text, str) or not text):
            raise EditError(f"{where}: where.text must be a non-empty string.")
    wanted = set(ids) if ids is not None else None
    found: list[int] = []
    for index, entity in enumerate(entities(doc)):
        if not isinstance(entity, dict):
            continue
        if wanted is not None and str(entity.get("id")) not in wanted:
            continue
        if layers is not None and str(entity.get("layer", "")) not in layers:
            continue
        if kinds is not None and str(entity.get("kind", "")).upper() not in kinds:
            continue
        if text is not None and text not in _entity_text(entity):
            continue
        found.append(index)
    if wanted is not None:
        missing = wanted - {str(entities(doc)[i].get("id")) for i in found}
        if missing and cond is None:
            sample = ", ".join(sorted(missing)[:5])
            raise EditError(f"{where}: no entity has the ID {sample}.")
    return found


# ---- operations ------------------------------------------------------------------------------


def _op_set(doc: dict[str, Any], op: dict[str, Any], where: str, report: EditReport) -> None:
    fields = op.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise EditError(f'{where}: set needs fields, e.g. {{"layer": "walls"}}.')
    if "id" in fields or "kind" in fields:
        raise EditError(f"{where}: id and kind cannot be changed (delete and add instead).")
    targets = select(doc, op, where)
    for index in targets:
        entity = entities(doc)[index]
        kind = str(entity.get("kind", "")).upper()
        allowed = COMMON_FIELDS | KIND_FIELDS.get(kind, frozenset())
        for name, value in fields.items():
            if name not in allowed:
                raise EditError(
                    f"{where}: {kind} has no field {name} that can be set "
                    f"(fields: {', '.join(sorted(allowed))})."
                )
            value = _check_field(name, value, where)
            before = entity.get(name)
            if before == value:
                continue
            entity[name] = copy.deepcopy(value)
            if name in JW_PEN_KEYS:
                drop_jw_pen(entity, name)
            if name == "layer":
                _ensure_layer(doc, value, report)
        report.changed += 1


def _op_delete(doc: dict[str, Any], op: dict[str, Any], where: str, report: EditReport) -> None:
    targets = set(select(doc, op, where))
    if not targets:
        return
    items = entities(doc)
    doc["entities"] = [e for i, e in enumerate(items) if i not in targets]
    report.deleted += len(targets)


def _op_replace_text(
    doc: dict[str, Any], op: dict[str, Any], where: str, report: EditReport
) -> None:
    pattern = op.get("pattern")
    replacement = op.get("replacement")
    if not isinstance(pattern, str) or not pattern:
        raise EditError(f"{where}: replace_text needs pattern (the text to find).")
    if not isinstance(replacement, str):
        raise EditError(f"{where}: replace_text needs replacement (a string, may be empty).")
    use_regex = op.get("regex", False)
    if not isinstance(use_regex, bool):
        raise EditError(f"{where}: regex must be true or false.")
    try:
        compiled = re.compile(pattern if use_regex else re.escape(pattern))
    except re.error as exc:
        raise EditError(f"{where}: the pattern is not a valid regular expression ({exc}).") from exc
    if op.get("ids") is None and op.get("where") is None:
        targets = range(len(entities(doc)))
    else:
        targets = select(doc, op, where)
    for index in targets:
        entity = entities(doc)[index]
        if not isinstance(entity, dict):
            continue
        kind = str(entity.get("kind", "")).upper()
        if kind in TEXT_KINDS and isinstance(entity.get("text"), str):
            new, count = compiled.subn(replacement, entity["text"])
            if count:
                entity["text"] = new
                report.texts_replaced += count
        attributes = entity.get("attributes") if kind == "INSERT" else None
        if isinstance(attributes, list):
            for attribute in attributes:
                if isinstance(attribute, dict) and isinstance(attribute.get("text"), str):
                    new, count = compiled.subn(replacement, attribute["text"])
                    if count:
                        attribute["text"] = new
                        report.texts_replaced += count


def _op_add_layer(doc: dict[str, Any], op: dict[str, Any], where: str, report: EditReport) -> None:
    name = op.get("name")
    if not isinstance(name, str) or not name.strip():
        raise EditError(f"{where}: add_layer needs name.")
    table = layer_table(doc)
    if name in table:
        raise EditError(f"{where}: the layer {name} already exists.")
    entry: dict[str, Any] = {}
    for key in ("color", "linetype", "lineweight_mm"):
        if key in op:
            entry[key] = _check_field(key, op[key], where)
    table[name] = entry
    report.layers_added.append(name)


def _default_layer(doc: dict[str, Any]) -> str:
    table = layer_table(doc)
    if "0" in table:
        return "0"
    return next(iter(table), "0")


def _op_add(
    doc: dict[str, Any],
    op: dict[str, Any],
    where: str,
    report: EditReport,
    new_ids: Iterable[str],
) -> None:
    items = op.get("entities")
    if not isinstance(items, list) or not items:
        raise EditError(f"{where}: add needs entities (a list of IR entities).")
    ids = iter(new_ids)
    for n, raw in enumerate(items):
        at = f"{where}.entities[{n}]"
        if not isinstance(raw, dict):
            raise EditError(f"{at}: each entity is an object.")
        kind = str(raw.get("kind", "")).upper()
        if kind not in ADDABLE_KINDS:
            raise EditError(
                f"{at}: kind must be one of {', '.join(sorted(ADDABLE_KINDS))} (not {kind or 'empty'})."
            )
        allowed = COMMON_FIELDS | KIND_FIELDS[kind] | {"kind", "id"}
        unknown = set(raw) - allowed
        if unknown:
            raise EditError(f"{at}: {kind} does not take {', '.join(sorted(unknown))}.")
        missing = [name for name in REQUIRED[kind] if name not in raw]
        if missing:
            raise EditError(f"{at}: {kind} needs {', '.join(missing)}.")
        entity: dict[str, Any] = {"id": next(ids), "kind": kind}
        for name, value in raw.items():
            if name in ("kind", "id"):
                continue
            entity[name] = copy.deepcopy(_check_field(name, value, at))
        entity.setdefault("layer", _default_layer(doc))
        _ensure_layer(doc, entity["layer"], report)
        entities(doc).append(entity)
        row: dict[str, Any] = {"id": entity["id"], "kind": kind, "layer": entity["layer"]}
        length = ir_length(entity)
        if length is not None:
            row["length"] = round(length, 4)
            factor = mm_per_unit(doc, entity)
            row["length_mm"] = round(length * factor, 2) if factor is not None else None
        report.added.append(row)


# ---- blank drawings --------------------------------------------------------------------------


def parse_scale(value: Any) -> float:
    """'1/100', '1:50', 100 or '100' -> 100.0 (the denominator)."""
    if _number(value) and value > 0:
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace("：", ":").replace("／", "/")
        match = re.fullmatch(r"1\s*[/:]\s*(\d+(?:\.\d+)?)", text) or re.fullmatch(
            r"(\d+(?:\.\d+)?)", text
        )
        if match and float(match.group(1)) > 0:
            return float(match.group(1))
    raise EditError(f"create: scale must look like 1/100 (not {str(value)[:20]}).")


def blank_document(
    create: dict[str, Any], ir_version: str = CURRENT_IR_VERSION, locale: str = "en"
) -> tuple[dict[str, Any], list[str]]:
    """A blank drawing and notes for the AI on its coordinates (in locale, ja or en)."""
    if not isinstance(create, dict):
        raise EditError('create must be an object, e.g. {"format": "jww", "paper": "A3"}.')
    unknown = set(create) - {"format", "paper", "scale", "layers"}
    if unknown:
        raise EditError(
            f"create takes format, paper, scale and layers (not {', '.join(sorted(unknown))})."
        )
    fmt = str(create.get("format", "jww")).lower()
    if fmt not in ("jww", "dxf"):
        raise EditError("create: format is jww or dxf.")
    names = create.get("layers") or ["0"]
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        raise EditError("create: layers is a list of layer names.")
    if len(set(names)) != len(names):
        raise EditError("create: the layer names repeat.")
    doc: dict[str, Any] = {
        "format": "cad2d-ir",
        "version": ir_version,
        "header": {"units": "mm", "angle_unit": "deg", "coord_space": "world"},
        "source": {"format": fmt},
        "tables": {"layers": {}},
        "entities": [],
    }
    layers = doc["tables"]["layers"]
    if fmt == "dxf":
        for name in names:
            layers[name] = {}
        if locale == "ja":
            return doc, ["座標は実寸の mm です(モデル空間)。"]
        return doc, ["Coordinates are real-size millimetres (model space)."]
    paper = str(create.get("paper", "A3")).upper()
    if paper not in JW_PAPERS:
        raise EditError(f"create: paper is one of {', '.join(JW_PAPERS)}.")
    if len(names) > 256:
        raise EditError("create: Jw_cad has at most 256 layers (16 groups of 16).")
    scale = parse_scale(create.get("scale", "1/100"))
    code, (width, height) = JW_PAPERS[paper]
    doc["header"]["metadata"] = {"jww": {"paper_size": code, "write_layer_group": 0}}
    for index, name in enumerate(names):
        group, number = divmod(index, 16)
        layers[name] = {
            "metadata": {
                "jww": {
                    "layer_group": group,
                    "layer": number,
                    "group_scale": scale,
                    "original_name": name,
                }
            }
        }
    denominator = f"{scale:g}"
    if locale == "ja":
        return doc, [
            f"座標は用紙の上の mm です({paper}: {width:g} × {height:g} mm、原点は用紙の中心)。"
            f"実寸はその {denominator} 倍です(縮尺 1/{denominator})。実寸の 3,640 mm は用紙の上で "
            f"{3640 / scale:g} です。"
        ]
    return doc, [
        f"Coordinates are millimetres on the paper ({paper}: {width:g} x {height:g} mm, origin at "
        f"the centre of the sheet); real sizes are {denominator} times larger (scale 1/{denominator}). "
        f"A real 3,640 mm is {3640 / scale:g} on the paper."
    ]


# ---- the whole edit --------------------------------------------------------------------------

OPERATIONS = ("set", "delete", "add", "replace_text", "add_layer")


def apply_edits(doc: dict[str, Any], ops: list[Any]) -> tuple[dict[str, Any], EditReport]:
    """Apply ops to a copy of doc. All or nothing: a bad op raises EditError and changes nothing."""
    if not isinstance(ops, list):
        raise EditError("ops must be a list of operations.")
    work = copy.deepcopy(doc)
    report = EditReport()
    new_ids = _next_ids(work)
    for n, op in enumerate(ops):
        where = f"ops[{n}]"
        if not isinstance(op, dict):
            raise EditError(f"{where}: each operation is an object with op.")
        name = op.get("op")
        if name == "set":
            _op_set(work, op, where, report)
        elif name == "delete":
            _op_delete(work, op, where, report)
        elif name == "add":
            _op_add(work, op, where, report, new_ids)
        elif name == "replace_text":
            _op_replace_text(work, op, where, report)
        elif name == "add_layer":
            _op_add_layer(work, op, where, report)
        else:
            raise EditError(f"{where}: op is one of {', '.join(OPERATIONS)} (not {name!r}).")
    return work, report
