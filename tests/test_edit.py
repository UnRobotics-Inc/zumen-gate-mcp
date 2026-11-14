from __future__ import annotations

import math

import pytest

from zumen_gate_mcp.edit import (
    EditError,
    apply_edits,
    blank_document,
    ir_length,
    mm_per_unit,
    parse_scale,
)


def _by_id(doc, ident):
    return next(e for e in doc["entities"] if e["id"] == ident)


def test_changing_color_drops_the_jw_pen_so_the_jww_writer_uses_it(jww_doc):
    doc, report = apply_edits(
        jww_doc, [{"op": "set", "ids": ["JWW_E00000001"], "fields": {"color": 5}}]
    )
    line = _by_id(doc, "JWW_E00000001")
    assert line["color"] == 5
    assert "pen_color" not in line["metadata"]["jww"]
    assert "custom_color" not in line["metadata"]["jww"]
    # Only the matching values go: line type and width stay as they were
    assert line["metadata"]["jww"]["pen_style"] == 1
    assert line["metadata"]["jww"]["pen_width"] == 0
    assert report.changed == 1
    # The input document is untouched
    assert _by_id(jww_doc, "JWW_E00000001")["metadata"]["jww"]["pen_color"] == 2


def test_setting_the_same_value_keeps_the_jw_pen(jww_doc):
    doc, _ = apply_edits(jww_doc, [{"op": "set", "ids": ["JWW_E00000001"], "fields": {"color": 2}}])
    assert _by_id(doc, "JWW_E00000001")["metadata"]["jww"]["pen_color"] == 2


@pytest.mark.parametrize(
    ("field", "value", "dropped"),
    [("linetype", "DASHED", "pen_style"), ("lineweight_mm", 0.5, "pen_width")],
)
def test_line_type_and_width_drop_their_jw_values(jww_doc, field, value, dropped):
    doc, _ = apply_edits(
        jww_doc, [{"op": "set", "ids": ["JWW_E00000001"], "fields": {field: value}}]
    )
    jw = _by_id(doc, "JWW_E00000001")["metadata"]["jww"]
    assert dropped not in jw
    assert jw["pen_color"] == 2


def test_moving_to_a_new_layer_adds_it_and_keeps_jw_values(jww_doc):
    doc, report = apply_edits(
        jww_doc,
        [{"op": "set", "where": {"layer": "壁", "kind": "LINE"}, "fields": {"layer": "設備"}}],
    )
    line = _by_id(doc, "JWW_E00000001")
    assert line["layer"] == "設備"
    assert "設備" in doc["tables"]["layers"]
    assert report.layers_added == ["設備"]
    # The writer places the layer; the entity keeps its pen
    assert line["metadata"]["jww"]["pen_color"] == 2
    # The circle on the same layer did not match kind LINE
    assert _by_id(doc, "JWW_E00000003")["layer"] == "壁"


def test_delete_by_ids_and_where(jww_doc):
    doc, report = apply_edits(jww_doc, [{"op": "delete", "where": {"kind": "CIRCLE"}}])
    assert [e["id"] for e in doc["entities"]] == ["JWW_E00000001", "JWW_E00000002"]
    assert report.deleted == 1
    doc, report = apply_edits(doc, [{"op": "delete", "ids": ["JWW_E00000002"]}])
    assert [e["id"] for e in doc["entities"]] == ["JWW_E00000001"]


def test_selection_errors(jww_doc):
    with pytest.raises(EditError, match="ids, or where"):
        apply_edits(jww_doc, [{"op": "delete"}])
    with pytest.raises(EditError, match="at least one"):
        apply_edits(jww_doc, [{"op": "delete", "where": {}}])
    with pytest.raises(EditError, match="query_drawing"):
        apply_edits(jww_doc, [{"op": "delete", "where": {"bbox": [0, 0, 1, 1]}}])
    with pytest.raises(EditError, match="no entity has the ID"):
        apply_edits(jww_doc, [{"op": "delete", "ids": ["NOPE"]}])


def test_replace_text_literal_and_regex(jww_doc):
    doc, report = apply_edits(
        jww_doc, [{"op": "replace_text", "pattern": "旧社名", "replacement": "新社名"}]
    )
    assert _by_id(doc, "JWW_E00000002")["text"] == "新社名 設計事務所"
    assert report.texts_replaced == 1
    doc, report = apply_edits(
        jww_doc,
        [
            {
                "op": "replace_text",
                "pattern": r"^\S+",
                "replacement": "X",
                "regex": True,
                "where": {"layer": "文字"},
            }
        ],
    )
    assert _by_id(doc, "JWW_E00000002")["text"] == "X 設計事務所"
    with pytest.raises(EditError, match="regular expression"):
        apply_edits(
            jww_doc, [{"op": "replace_text", "pattern": "(", "replacement": "", "regex": True}]
        )


def test_replace_text_in_block_attributes(jww_doc):
    jww_doc["entities"].append(
        {
            "id": "INS1",
            "kind": "INSERT",
            "layer": "文字",
            "block": "TITLE",
            "insert": [0, 0],
            "attributes": [{"tag": "NAME", "text": "旧社名", "insert": [0, 0], "height": 2.5}],
        }
    )
    doc, report = apply_edits(
        jww_doc, [{"op": "replace_text", "pattern": "旧社名", "replacement": "新社名"}]
    )
    assert _by_id(doc, "INS1")["attributes"][0]["text"] == "新社名"
    assert report.texts_replaced == 2


def test_add_gives_ids_and_real_lengths(jww_doc):
    doc, report = apply_edits(
        jww_doc,
        [
            {
                "op": "add",
                "entities": [
                    {"kind": "LINE", "layer": "壁", "p1": [0, 10], "p2": [36.4, 10]},
                    {"kind": "TEXT", "layer": "文字", "insert": [0, 0], "height": 2.5, "text": "A"},
                ],
            },
            {"op": "add", "entities": [{"kind": "CIRCLE", "center": [0, 0], "radius": 1}]},
        ],
    )
    ids = [row["id"] for row in report.added]
    assert ids == ["ZGM_E000001", "ZGM_E000002", "ZGM_E000003"]
    line = report.added[0]
    assert line["length"] == pytest.approx(36.4)
    # 1/100: 36.4 mm on paper is 3,640 mm in real size
    assert line["length_mm"] == pytest.approx(3640.0)
    assert "length" not in report.added[1]
    # No layer given: the first layer of the table
    assert report.added[2]["layer"] == "壁"
    added = _by_id(doc, "ZGM_E000001")
    assert added["p1"] == [0.0, 10.0] and "metadata" not in added


def test_add_checks_the_shape(jww_doc):
    with pytest.raises(EditError, match="kind must be one of"):
        apply_edits(jww_doc, [{"op": "add", "entities": [{"kind": "SPLINE"}]}])
    with pytest.raises(EditError, match="needs p2"):
        apply_edits(jww_doc, [{"op": "add", "entities": [{"kind": "LINE", "p1": [0, 0]}]}])
    with pytest.raises(EditError, match="p1 has a wrong value"):
        apply_edits(
            jww_doc, [{"op": "add", "entities": [{"kind": "LINE", "p1": [0], "p2": [1, 1]}]}]
        )
    with pytest.raises(EditError, match="does not take"):
        apply_edits(
            jww_doc,
            [{"op": "add", "entities": [{"kind": "LINE", "p1": [0, 0], "p2": [1, 1], "bulge": 1}]}],
        )


def test_add_layer_and_its_duplicate(jww_doc):
    doc, report = apply_edits(jww_doc, [{"op": "add_layer", "name": "設備", "color": 5}])
    assert doc["tables"]["layers"]["設備"] == {"color": 5}
    assert report.layers_added == ["設備"]
    with pytest.raises(EditError, match="already exists"):
        apply_edits(jww_doc, [{"op": "add_layer", "name": "壁"}])


def test_edits_are_all_or_nothing(jww_doc):
    before = [dict(e) for e in jww_doc["entities"]]
    with pytest.raises(EditError):
        apply_edits(
            jww_doc,
            [
                {"op": "delete", "ids": ["JWW_E00000001"]},
                {"op": "teleport"},
            ],
        )
    assert [e["id"] for e in jww_doc["entities"]] == [e["id"] for e in before]


def test_set_rejects_unknown_fields(jww_doc):
    with pytest.raises(EditError, match="has no field radius"):
        apply_edits(jww_doc, [{"op": "set", "ids": ["JWW_E00000001"], "fields": {"radius": 3}}])
    with pytest.raises(EditError, match="cannot be changed"):
        apply_edits(jww_doc, [{"op": "set", "ids": ["JWW_E00000001"], "fields": {"kind": "ARC"}}])


def test_blank_jww_drawing():
    doc, notes = blank_document(
        {"format": "jww", "paper": "A3", "scale": "1/100", "layers": ["壁", "文字"]}
    )
    assert doc["source"] == {"format": "jww"}
    assert doc["header"]["metadata"]["jww"] == {"paper_size": 3, "write_layer_group": 0}
    walls = doc["tables"]["layers"]["壁"]["metadata"]["jww"]
    assert walls == {"layer_group": 0, "layer": 0, "group_scale": 100.0, "original_name": "壁"}
    assert doc["tables"]["layers"]["文字"]["metadata"]["jww"]["layer"] == 1
    assert doc["entities"] == []
    assert "36.4" in notes[0]
    # Drawn in the same call
    added, report = apply_edits(
        doc,
        [
            {
                "op": "add",
                "entities": [{"kind": "LINE", "layer": "壁", "p1": [0, 0], "p2": [36.4, 0]}],
            }
        ],
    )
    assert report.added[0]["length_mm"] == pytest.approx(3640.0)


def test_blank_dxf_drawing_is_real_size():
    doc, notes = blank_document({"format": "dxf", "layers": ["0", "walls"]})
    assert doc["source"] == {"format": "dxf"}
    assert set(doc["tables"]["layers"]) == {"0", "walls"}
    _, report = apply_edits(
        doc, [{"op": "add", "entities": [{"kind": "LINE", "p1": [0, 0], "p2": [3640, 0]}]}]
    )
    assert report.added[0]["length_mm"] == pytest.approx(3640.0)
    assert "real-size" in notes[0]


def test_blank_drawing_checks():
    with pytest.raises(EditError, match="paper"):
        blank_document({"format": "jww", "paper": "B9"})
    with pytest.raises(EditError, match="scale"):
        blank_document({"format": "jww", "scale": "big"})
    with pytest.raises(EditError, match="format"):
        blank_document({"format": "sfc"})
    with pytest.raises(EditError, match="repeat"):
        blank_document({"layers": ["a", "a"]})


@pytest.mark.parametrize(
    ("value", "expected"), [("1/100", 100.0), ("1:50", 50.0), (200, 200.0), ("1／30", 30.0)]
)
def test_parse_scale(value, expected):
    assert parse_scale(value) == expected


def test_lengths_of_arcs_and_polylines():
    assert ir_length(
        {"kind": "ARC", "center": [0, 0], "radius": 2, "start_angle": 0, "end_angle": 90}
    ) == pytest.approx(math.pi)
    # A half circle as one bulge segment (bulge 1)
    half = ir_length({"kind": "LWPOLYLINE", "vertices": [[0, 0, 1.0], [2, 0]]})
    assert half == pytest.approx(math.pi)
    square = ir_length(
        {"kind": "LWPOLYLINE", "vertices": [[0, 0], [1, 0], [1, 1], [0, 1]], "closed": True}
    )
    assert square == pytest.approx(4.0)


def test_mm_per_unit_for_units_and_unknown_scales(jww_doc):
    assert mm_per_unit({"header": {"units": "m"}, "source": {"format": "dxf"}}, {}) == 1000.0
    assert mm_per_unit({"header": {"units": "unitless"}, "source": {"format": "dxf"}}, {}) is None
    jw = jww_doc["tables"]["layers"]["文字"]["metadata"]["jww"]
    jw.update(layer_group=1, group_scale=50.0)
    assert mm_per_unit(jww_doc, {"layer": "文字"}) == 50.0
    # A new layer goes to the group being written to (0 here): its scale
    assert mm_per_unit(jww_doc, {"layer": "設備"}) == 100.0
    jww_doc["header"]["metadata"]["jww"]["write_layer_group"] = 1
    assert mm_per_unit(jww_doc, {"layer": "設備"}) == 50.0


def test_new_layers_in_a_full_jw_layer_table_take_the_write_group_scale(jww_doc):
    """The API lists all 256 Jw_cad layers; unused groups have scale 1."""
    layers = jww_doc["tables"]["layers"]
    for g in range(16):
        for n in range(16):
            name = f"{g:X}-{n:X}"
            scale = 100.0 if g == 0 else 1.0
            layers.setdefault(
                name, {"metadata": {"jww": {"layer_group": g, "layer": n, "group_scale": scale}}}
            )
    doc, report = apply_edits(
        jww_doc,
        [
            {
                "op": "add",
                "entities": [{"kind": "CIRCLE", "layer": "設備", "center": [0, 0], "radius": 5}],
            }
        ],
    )
    assert report.added[0]["length_mm"] == pytest.approx(2 * math.pi * 5 * 100, rel=1e-4)
