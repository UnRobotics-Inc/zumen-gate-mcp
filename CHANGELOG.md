# Changelog

## 0.1.1

- The command line accepts `--root` and `--raw` after the command too
  (`zumen-gate formats --raw`).
- The package's repository and issue links point to the right place.

## 0.1.0

First release.

- MCP server over stdio with eight tools: `list_formats`, `inspect_drawing`, `query_drawing`,
  `render_drawing`, `edit_drawing`, `convert_drawing`, `get_job` and `account_status`.
- The `zumen-gate` command line with the same operations.
- Local editing of a versioned working copy (set, delete, add, replace_text, add_layer, undo) and
  blank JWW or DXF drawings. Changing the color, line type or line width of a Jw_cad entity also
  drops its original pen so that writing back to JWW keeps the change.
- Long conversions return a job ID after 40 seconds while the client keeps waiting.
- Answers are cut to fit the model's context, and text read from drawings is marked as data.
- A skill (`skills/zumen-gate/SKILL.md`) and a Claude Code plugin manifest.
