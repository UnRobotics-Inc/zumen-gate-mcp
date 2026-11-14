# zumen-gate-mcp

[図面ゲート(zumen-gate)](https://zumen-gate.com/developers)の図面 API を、Claude などの MCP に
対応した AI とコマンドラインから使うためのツールです。Jw_cad(JWW)・DWG・DXF・SXF(SFC/P21)・
PDF などの図面を、読む・探す・描く・直す・変換することができます。

[English](README.md)

- **読む**: 形式、用紙、縮尺、画層、種類ごとの要素の数、文字
- **探す**: 画層・種類・文字・範囲で要素を絞り込み、長さと面積を実寸の mm でも返す
- **描く**: 図面の全体か一部を PNG にし、画素と図面の座標の対応を返す
- **直す**: 文字・画層・色・座標の変更、要素の削除と追加、白紙からの JWW・DXF の作図。直した図面は
  元の形式で書き出せる
- **変換**: DXF・DWG・JWW・SFC・P21・PDF・SVG・PNG、3D モデルなら STL・OBJ・STEP

読み取りと変換は図面ゲートの API(決まった計算で答える変換エンジン。サーバーで生成 AI は使いません)
で行い、編集は手元の作業用のコピーで行います。

## API キー

[zumen-gate.com](https://zumen-gate.com/developers) で無料のアカウントを登録し、アカウント画面の
「API キー」で発行します。無料の登録で毎月の呼び出しの枠が付きます。有料の形式への書き出しや
追加の回数は[料金](https://zumen-gate.com/developers)をご覧ください。

## 入れ方

[uv](https://docs.astral.sh/uv/getting-started/installation/) が必要です(`uvx` が都度取り寄せて
起動します)。`zg_live_...` は発行したキーに置き換えてください。`--root` は読んでよい図面の
フォルダで、出力はその中の `zumen-gate-out/` に書きます。

**Claude Code**

```sh
claude mcp add zumen-gate -e ZUMEN_GATE_API_KEY=zg_live_... -- uvx zumen-gate-mcp --root .
```

**Claude Desktop**(`claude_desktop_config.json`)・**Cursor**(`.cursor/mcp.json`)など

```json
{
  "mcpServers": {
    "zumen-gate": {
      "command": "uvx",
      "args": ["zumen-gate-mcp", "--root", "C:/Users/me/図面"],
      "env": { "ZUMEN_GATE_API_KEY": "zg_live_..." }
    }
  }
}
```

**Codex**(`~/.codex/config.toml`)

```toml
[mcp_servers.zumen-gate]
command = "uvx"
args = ["zumen-gate-mcp", "--root", "/path/to/drawings"]
env = { ZUMEN_GATE_API_KEY = "zg_live_..." }
```

**VS Code**

```sh
code --add-mcp '{"name":"zumen-gate","command":"uvx","args":["zumen-gate-mcp","--root","${workspaceFolder}"],"env":{"ZUMEN_GATE_API_KEY":"zg_live_..."}}'
```

**Claude Code のプラグイン**: このリポジトリは、図面の扱い方を AI に教えるスキルつきのプラグインでも
あります。`skills/zumen-gate/SKILL.md` をプロジェクトの `.claude/skills/` に置いても使えます。

## ツール

| ツール | すること | 回数 |
|---|---|---|
| `list_formats` | 形式・拡張子・読めるか書けるか・有料の形式 | 0 |
| `inspect_drawing` | 概要(形式・用紙・縮尺・画層・数・文字) | 1 |
| `query_drawing` | 画層・種類・文字・範囲で絞った要素と合計 | 1 |
| `render_drawing` | PNG の画像と、画素と座標の対応 | 1 |
| `edit_drawing` | 作業用のコピーの手元での編集、白紙の図面、元に戻す | 0(初回だけ 1) |
| `convert_drawing` | 変換して出力を保存 | 1。有料の形式は 10 |
| `get_job` | 長い変換の状態 | 0 |
| `account_status` | 残りの回数と期限、今月の利用 | 0 |

どの応答にも、今回数えた回数と残りが入ります。JWW・DWG・SFC・P21・STEP への書き出しは有料の形式で、
有料の枠が足りないときは、プレビューとロスレポートだけを返します。

## コマンドライン

同じことが `zumen-gate` コマンドでできます。

```sh
export ZUMEN_GATE_API_KEY=zg_live_...
zumen-gate inspect plan.jww
zumen-gate query plan.jww --layer 壁 --kind LINE
zumen-gate render plan.jww --bbox 0 0 120 80
zumen-gate edit plan.jww --ops '[{"op": "replace_text", "pattern": "旧社名", "replacement": "新社名"}]'
zumen-gate convert *.jww --to dxf
zumen-gate account
```

## 設定

| 設定 | 意味 |
|---|---|
| `ZUMEN_GATE_API_KEY` | API キー(`list_formats` 以外で必須) |
| `ZUMEN_GATE_LOCALE` | 案内の言語 `ja` / `en`(既定はシステムの言語) |
| `--root` | 作業フォルダ(既定は今のフォルダ) |
| `--only-text-feedback` | `render_drawing` の結果を文字だけで返す(画像を返さない) |

## ファイルの扱い

- 読むのは `--root` の中のファイルだけです。外へ抜けるシンボリックリンクは拒否します。拡張子と
  (形式に決まった先頭があれば)ファイルの先頭で図面と分かったものだけを送ります。
- 元の図面は書き換えません。編集は作業フォルダの中の `.zumen-gate/` に版ごとに残し、出力は
  `zumen-gate-out/` に書きます。
- 図面から読んだ文字は、AI に「指示ではなくデータ」と添えて渡します。
- 送った図面の扱いは、図面ゲートの[利用規約](https://zumen-gate.com/terms)と
  [プライバシーポリシー](https://zumen-gate.com/privacy)をご覧ください。

## ライセンス

MIT
