from __future__ import annotations

from zumen_gate_mcp.edit import blank_document
from zumen_gate_mcp.shaping import estimate_tokens, fit_list, fit_text, summarize_inspect

INSPECT = {
    "format": {"input": "ir", "detected": "ir", "source_format": "jww", "source_version": "700"},
    "coordinates": {
        "units": "mm",
        "space": "paper",
        "real_mm_per_unit": 100.0,
        "scale_by_group": {"0": 100.0},
    },
    "paper": {"name": "A3", "code": 3},
    "entities": {"total": 41, "by_kind": {"LINE": 40, "TEXT": 1}},
    "layers": [{"name": "壁"}, {"name": "文字"}],
}


def test_japanese_text_counts_as_more_tokens():
    assert estimate_tokens("a" * 400) < estimate_tokens("あ" * 400)


def test_fit_list_keeps_what_fits_and_says_how_to_get_the_rest():
    body = {"matched": 1000, "items": [{"text": "x" * 500} for _ in range(1000)]}
    cut, note = fit_list(body, "items", "en", how="cut_how_query", budget=10_000)
    assert 0 < len(cut["items"]) < 1000
    assert estimate_tokens(str(cut)) < 12_000
    assert note is not None and "next_cursor" in note
    same, none = fit_list({"items": [1, 2]}, "items", "en")
    assert none is None and same["items"] == [1, 2]


def test_fit_text_cuts_long_text():
    text = fit_text("あ" * 50_000, "ja", budget=1_000)
    assert len(text) < 2_000 and "絞って" in text


def test_summary_names_the_source_format_of_an_ir_input():
    ja = summarize_inspect(INSPECT, "ja")
    assert ja.startswith("JWW(700)") and "1/100" in ja and "線 40" in ja and "画層 2" in ja
    en = summarize_inspect(INSPECT, "en")
    assert en.startswith("A JWW (700) drawing.") and "41 entities" in en


def test_blank_drawing_notes_follow_the_locale():
    _, notes = blank_document({"format": "jww", "scale": "1/50"}, locale="ja")
    assert "用紙の上" in notes[0] and "72.8" in notes[0]
    _, notes = blank_document({"format": "dxf"}, locale="ja")
    assert "実寸" in notes[0]
