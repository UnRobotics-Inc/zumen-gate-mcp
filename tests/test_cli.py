from __future__ import annotations

import pytest

from zumen_gate_mcp.cli import build_parser


@pytest.mark.parametrize(
    "argv",
    [
        ["--raw", "--root", "/drawings", "formats"],
        ["formats", "--raw", "--root", "/drawings"],
        ["--raw", "formats", "--root", "/drawings"],
    ],
)
def test_root_and_raw_work_before_or_after_the_command(argv):
    args = build_parser().parse_args(argv)
    assert (args.command, args.raw, args.root) == ("formats", True, "/drawings")


def test_defaults_without_the_options():
    args = build_parser().parse_args(["inspect", "plan.jww"])
    assert (args.raw, args.root, args.path) == (False, None, "plan.jww")


def test_convert_keeps_its_version_option():
    args = build_parser().parse_args(
        ["convert", "a.jww", "b.jww", "--to", "dwg", "--version", "R2000"]
    )
    assert (args.paths, args.to, args.target_version) == (["a.jww", "b.jww"], "dwg", "R2000")
