from __future__ import annotations

import copy
from typing import Any

import pytest

JWW_DOC: dict[str, Any] = {
    "format": "cad2d-ir",
    "version": "0.3.0",
    "header": {
        "units": "mm",
        "angle_unit": "deg",
        "coord_space": "world",
        "metadata": {"jww": {"paper_size": 3, "write_layer_group": 0}},
    },
    "source": {"format": "jww", "version": "700"},
    "tables": {
        "layers": {
            "壁": {"metadata": {"jww": {"layer_group": 0, "layer": 1, "group_scale": 100.0}}},
            "文字": {"metadata": {"jww": {"layer_group": 0, "layer": 2, "group_scale": 100.0}}},
        }
    },
    "entities": [
        {
            "id": "JWW_E00000001",
            "kind": "LINE",
            "layer": "壁",
            "color": 2,
            "linetype": "CONTINUOUS",
            "lineweight_mm": 0.25,
            "p1": [0.0, 0.0],
            "p2": [36.4, 0.0],
            "metadata": {
                "jww": {
                    "pen_color": 2,
                    "custom_color": 0,
                    "pen_style": 1,
                    "pen_width": 0,
                    "layer": 1,
                }
            },
        },
        {
            "id": "JWW_E00000002",
            "kind": "TEXT",
            "layer": "文字",
            "insert": [10.0, 10.0],
            "height": 3.0,
            "text": "旧社名 設計事務所",
            "metadata": {"jww": {"pen_color": 1}},
        },
        {
            "id": "JWW_E00000003",
            "kind": "CIRCLE",
            "layer": "壁",
            "center": [50.0, 50.0],
            "radius": 5.0,
        },
    ],
}


@pytest.fixture
def jww_doc() -> dict[str, Any]:
    return copy.deepcopy(JWW_DOC)
