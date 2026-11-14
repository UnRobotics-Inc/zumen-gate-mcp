"""The eight tools as plain functions. The MCP server and the CLI are thin layers over these."""

from __future__ import annotations

import base64
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api import Payload, ZumenGateApi
from .client import ApiClient, ApiError
from .config import DEVELOPERS_URL, Config
from .edit import CURRENT_IR_VERSION, EditError, apply_edits, blank_document
from .jobs import FAILED, RUNNING, Job, JobRunner
from .paths import (
    FALLBACK_EXTENSIONS,
    PathError,
    check_drawing,
    output_dir,
    resolve_inside,
    safe_output_name,
    unique_path,
)
from .queue import Lane, LaneBusy
from .shaping import drawing_text_note, dumps, fit_list, fit_text, summarize_inspect
from .workspace import Workspace, read_ir

MESSAGES = {
    "ja": {
        "no_key": (
            "環境変数 ZUMEN_GATE_API_KEY に API キーが入っていません。キーは無料のアカウント登録で発行"
            "できます({url})。発行したら MCP サーバーの設定の env に ZUMEN_GATE_API_KEY を入れてください。"
        ),
        "busy": (
            "前の呼び出し({holder})が終わるのを待っています。get_job で終わりを確かめてから、もう一度"
            "呼んでください。"
        ),
        "changed": (
            "元のファイルは、作業用の IR を作った後に変わっています。作業用の IR(v{version})を送りました。"
            "新しい元のファイルから始め直すときは edit_drawing を reset=true で呼んでください。"
        ),
        "still_running": (
            "変換はまだ続いています({elapsed} 秒)。このプロセスが接続を保って待っています。get_job に "
            "job_id を渡すと、状態と終わった後の出力の場所が分かります。"
        ),
        "fetching_ir": (
            "図面を IR として取り寄せています(1 回)。get_job で終わりを確かめてから、同じ ops で "
            "edit_drawing をもう一度呼んでください。"
        ),
        "unknown_job": "その job_id はこのプロセスにありません(サーバーを起動し直すと消えます)。",
        "locked": (
            "{fmt} の書き出しには、追加の枠・単発パス・Pro の残りが 10 回分要ります。プレビューと"
            "ロスレポートだけを返しました。購入: {urls}"
        ),
        "created": "白紙の図面を作りました。",
        "fetched": "作業用の IR を作りました(図面を IR として取り寄せ、1 回)。",
        "undone": "1 つ前の版(v{version})に戻しました。",
        "reset": "作業用の IR を消しました。次の編集は元のファイルから始めます。",
    },
    "en": {
        "no_key": (
            "ZUMEN_GATE_API_KEY is not set. Get a free API key by registering at {url}, then put "
            "ZUMEN_GATE_API_KEY in the env of this MCP server's settings."
        ),
        "busy": (
            "Waiting for the previous call ({holder}) to finish. Check it with get_job, then call "
            "again."
        ),
        "changed": (
            "The original file changed after the working copy was made. The working copy (v{version}) "
            "was sent. To start again from the new file, call edit_drawing with reset=true."
        ),
        "still_running": (
            "The conversion is still running ({elapsed} s). This process keeps the connection open. "
            "Pass job_id to get_job for its state and, once done, the output paths."
        ),
        "fetching_ir": (
            "Fetching the drawing as IR (1 call). Check it with get_job, then call edit_drawing again "
            "with the same ops."
        ),
        "unknown_job": "This process has no job with that ID (jobs are lost when the server restarts).",
        "locked": (
            "Writing {fmt} needs 10 calls left from extra calls, a day pass or Pro. Only the preview "
            "and the loss report were returned. Buy: {urls}"
        ),
        "created": "Created a blank drawing.",
        "fetched": "Made a working copy (fetched the drawing as IR, 1 call).",
        "undone": "Went back to version v{version}.",
        "reset": "Removed the working copy. The next edit starts from the original file.",
    },
}


@dataclass
class ToolOutput:
    text: str
    image: bytes | None = None
    is_error: bool = False


class Tools:
    def __init__(self, config: Config, api: ZumenGateApi | None = None) -> None:
        self.config = config
        self.api = api or ZumenGateApi(ApiClient(config))
        self.lane = Lane()
        self.jobs = JobRunner(self.lane, config.long_wait_sec)
        # IR fetches still running, by drawing (a second edit must not fetch again)
        self._fetching: dict[Path, Job] = {}

    # ---- helpers -----------------------------------------------------------------------------

    def _msg(self, key: str, **values: Any) -> str:
        table = MESSAGES.get(self.config.locale, MESSAGES["en"])
        return table[key].format(**values)

    def _ok(self, body: dict[str, Any], image: bytes | None = None) -> ToolOutput:
        return ToolOutput(fit_text(dumps(body), self.config.locale), image=image)

    def _fail(self, body: dict[str, Any]) -> ToolOutput:
        return ToolOutput(dumps(body), is_error=True)

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.config.root).as_posix()
        except ValueError:
            return str(path)

    def _extensions(self) -> frozenset[str]:
        try:
            table = self.api.formats()
        except ApiError:
            return FALLBACK_EXTENSIONS
        found = {
            ext.lower()
            for row in table.get("formats", [])
            if isinstance(row, dict) and (row.get("read") or row.get("read_model"))
            for ext in row.get("extensions") or []
            if isinstance(ext, str)
        }
        # Formats the table lists without extensions are still recognised by their file name
        return frozenset(found | FALLBACK_EXTENSIONS)

    def _ir_version(self) -> str:
        try:
            versions = self.api.formats().get("ir", {}).get("accepted_versions")
        except ApiError:
            return CURRENT_IR_VERSION
        return versions[0] if isinstance(versions, list) and versions else CURRENT_IR_VERSION

    def _need_key(self) -> ToolOutput | None:
        if self.config.api_key:
            return None
        return self._fail(
            {"error": {"code": "key_missing", "message": self._msg("no_key", url=DEVELOPERS_URL)}}
        )

    def _api_error(self, exc: ApiError) -> ToolOutput:
        error: dict[str, Any] = {"status": exc.status, "code": exc.code, "message": exc.message}
        if exc.next_step:
            error["next"] = exc.next_step
        if exc.purchase:
            error["purchase"] = exc.purchase
        if exc.retry_after is not None:
            error["retry_after_sec"] = exc.retry_after
        if exc.code in ("key_missing", "key_invalid", "key_revoked"):
            error["keys"] = DEVELOPERS_URL
        for key, value in exc.extra.items():
            error.setdefault(key, value)
        body: dict[str, Any] = {"error": error}
        if exc.usage:
            body["usage"] = exc.usage
        if exc.request_id:
            body["request_id"] = exc.request_id
        return self._fail(body)

    def _local_error(self, exc: Exception, code: str) -> ToolOutput:
        return self._fail({"error": {"code": code, "message": str(exc)}})

    def _busy(self, exc: LaneBusy) -> ToolOutput:
        running = [
            {"job_id": j.id, "what": j.label, "elapsed_sec": j.elapsed} for j in self.jobs.running()
        ]
        body: dict[str, Any] = {
            "error": {"code": "client_busy", "message": self._msg("busy", holder=exc.holder or "?")}
        }
        if running:
            body["running_jobs"] = running
        return self._fail(body)

    def _guard(self, run: Any) -> ToolOutput:
        """Run a tool body and turn the expected failures into tool errors."""
        try:
            return run()
        except ApiError as exc:
            return self._api_error(exc)
        except PathError as exc:
            return self._local_error(exc, "bad_path")
        except EditError as exc:
            return self._local_error(exc, "bad_edit")
        except LaneBusy as exc:
            return self._busy(exc)

    def _payload(self, path: str, use_edits: bool) -> tuple[Payload, dict[str, Any]]:
        """What to send for path: the working copy (if any and use_edits) or the file itself."""
        target = resolve_inside(self.config.root, path, must_exist=False)
        workspace = Workspace.locate(self.config.work_dir, self.config.root, target)
        if use_edits and workspace.exists:
            ir_path = workspace.current_path()
            assert ir_path is not None
            sent: dict[str, Any] = {"sent": "working_copy", "version": workspace.current}
            if workspace.source_changed():
                sent["warning"] = self._msg("changed", version=workspace.current)
            name = f"{target.name}.ir.json.gz"
            return Payload("ir", name, ir_path.read_bytes()), sent
        if not target.exists():
            raise PathError(f"File not found: {path}")
        field = check_drawing(target, self._extensions())
        return Payload(field, target.name, target.read_bytes()), {"sent": "original"}

    def _with_lane(self, label: str, call: Any) -> Any:
        with self.lane.hold(label, self.config.long_wait_sec):
            return call()

    @staticmethod
    def _strip(body: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in body.items() if k != "request_id"}

    # ---- list_formats ------------------------------------------------------------------------

    def list_formats(self) -> ToolOutput:
        def run() -> ToolOutput:
            table = self.api.formats()
            rows = []
            for row in table.get("formats", []):
                if not isinstance(row, dict):
                    continue
                rows.append(
                    {
                        "id": row.get("id"),
                        "name": row.get("label"),
                        "extensions": row.get("extensions"),
                        "read_2d": bool(row.get("read")),
                        "read_3d": bool(row.get("read_model")),
                        "write": bool(row.get("write")),
                        "writes_3d_to": row.get("write_model") or [],
                        "paid_output": bool(row.get("paid_output")),
                        "beta": bool(row.get("beta")),
                    }
                )
            return self._ok(
                {
                    "formats": rows,
                    "extra_outputs": table.get("api_targets", []),
                    "paid_outputs": table.get("paid_targets", []),
                    "calls_per_use": table.get("costs", {}),
                    "prices": DEVELOPERS_URL,
                }
            )

        return self._guard(run)

    # ---- inspect / query / render ------------------------------------------------------------

    def inspect_drawing(
        self, path: str, use_edits: bool = True, pages: str | None = None, sheet: str | None = None
    ) -> ToolOutput:
        missing = self._need_key()
        if missing:
            return missing

        def run() -> ToolOutput:
            payload, sent = self._payload(path, use_edits)
            params = {k: v for k, v in (("pages", pages), ("sheet", sheet)) if v}
            body = self._with_lane(
                f"inspect {Path(path).name}", lambda: self.api.inspect(payload, params)
            )
            out: dict[str, Any] = {"summary": summarize_inspect(body, self.config.locale), **sent}
            if (body.get("texts") or {}).get("sample"):
                out["note"] = drawing_text_note(self.config.locale)
            out.update(self._strip(body))
            return self._ok(out)

        return self._guard(run)

    def query_drawing(
        self,
        path: str,
        layer: str | list[str] | None = None,
        kind: str | list[str] | None = None,
        text: str | None = None,
        text_regex: bool = False,
        bbox: list[float] | None = None,
        bbox_mode: str | None = None,
        ids: list[str] | None = None,
        include_entities: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
        use_edits: bool = True,
    ) -> ToolOutput:
        missing = self._need_key()
        if missing:
            return missing

        def run() -> ToolOutput:
            payload, sent = self._payload(path, use_edits)
            params: dict[str, Any] = {}
            for key, value in (
                ("layer", layer),
                ("kind", kind),
                ("text", text),
                ("bbox", bbox),
                ("bbox_mode", bbox_mode),
                ("ids", ids),
                ("limit", limit),
                ("cursor", cursor),
            ):
                if value is not None:
                    params[key] = value
            if text_regex:
                params["text_regex"] = True
            if include_entities:
                params["include_entities"] = True
            body = self._with_lane(
                f"query {Path(path).name}", lambda: self.api.query(payload, params)
            )
            out: dict[str, Any] = {**sent, **self._strip(body)}
            items = out.get("items") if isinstance(out.get("items"), list) else []
            if any(isinstance(i, dict) and ("text" in i or "entity" in i) for i in items):
                out["note"] = drawing_text_note(self.config.locale)
            out, cut = fit_list(out, "items", self.config.locale, how="cut_how_query")
            if cut:
                out["truncated"] = cut
            return self._ok(out)

        return self._guard(run)

    def render_drawing(
        self,
        path: str,
        bbox: list[float] | None = None,
        layers: list[str] | None = None,
        highlight_ids: list[str] | None = None,
        width_px: int | None = None,
        use_edits: bool = True,
    ) -> ToolOutput:
        missing = self._need_key()
        if missing:
            return missing

        def run() -> ToolOutput:
            payload, sent = self._payload(path, use_edits)
            params: dict[str, Any] = {}
            for key, value in (
                ("bbox", bbox),
                ("layers", layers),
                ("highlight_ids", highlight_ids),
                ("width_px", width_px),
            ):
                if value is not None:
                    params[key] = value
            body = self._with_lane(
                f"render {Path(path).name}", lambda: self.api.render(payload, params)
            )
            image = dict(body.get("image") or {})
            encoded = image.pop("base64", None)
            png = base64.b64decode(encoded) if isinstance(encoded, str) else None
            out: dict[str, Any] = {**sent, **self._strip(body), "image": image}
            if png:
                stem = Path(path).name
                target = unique_path(self.config.out_dir / f"{stem}.render.png")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(png)
                out["saved"] = self._rel(target)
            shown = None if self.config.only_text_feedback else png
            return self._ok(out, image=shown)

        return self._guard(run)

    # ---- edit --------------------------------------------------------------------------------

    def edit_drawing(
        self,
        path: str,
        ops: list[dict[str, Any]] | None = None,
        create: dict[str, Any] | None = None,
        undo: bool = False,
        reset: bool = False,
    ) -> ToolOutput:
        def run() -> ToolOutput:
            target = resolve_inside(self.config.root, path, must_exist=False)
            workspace = Workspace.locate(self.config.work_dir, self.config.root, target)
            if reset:
                workspace.discard()
                return self._ok({"reset": True, "message": self._msg("reset")})
            if undo:
                version = workspace.undo()
                return self._ok(
                    {"version": version, "message": self._msg("undone", version=f"{version:04d}")}
                )
            notes: list[str] = []
            if create is not None:
                if target.exists():
                    raise EditError(
                        f"{path} already exists. Edit it without create, or give a new file name."
                    )
                if workspace.exists:
                    raise EditError(
                        f"{path} already has a working copy (v{workspace.current}). Edit it without "
                        "create, or call edit_drawing with reset=true first."
                    )
                doc, notes = blank_document(create, self._ir_version(), self.config.locale)
                doc, report = apply_edits(doc, ops or [])
                version = workspace.start(doc, _describe(ops, "create"), blank=True)
                body = {"version": version, "message": self._msg("created"), **report.as_dict()}
                body["coordinates"] = notes
                body["next"] = _next_hint(self.config.locale)
                return self._ok(body)
            fetched = False
            if not workspace.exists:
                missing = self._need_key()
                if missing:
                    return missing
                job = self._fetch_ir(path, target, workspace)
                if job.state == RUNNING:
                    return self._ok(
                        {"job_id": job.id, "state": RUNNING, "message": self._msg("fetching_ir")}
                    )
                if job.state == FAILED:
                    raise job.error if job.error else RuntimeError("fetch failed")
                workspace = Workspace.locate(self.config.work_dir, self.config.root, target)
                fetched = True
            body: dict[str, Any] = {}
            if fetched:
                body["message"] = self._msg("fetched")
            if workspace.source_changed():
                body["warning"] = self._msg("changed", version=workspace.current)
            if ops:
                doc, report = apply_edits(workspace.load(), ops)
                version = workspace.add_version(doc, _describe(ops, "edit"))
                body.update({"version": version, **report.as_dict()})
                body["next"] = _next_hint(self.config.locale)
            else:
                body["version"] = workspace.current
            return self._ok(body)

        return self._guard(run)

    def _fetch_ir(self, path: str, target: Path, workspace: Workspace) -> Job:
        pending = self._fetching.get(target)
        if pending is not None and pending.state == RUNNING:
            pending.wait(self.config.long_wait_sec)
            return pending
        if not target.exists():
            raise PathError(f"File not found: {path} (to start a new drawing, pass create).")
        field = check_drawing(target, self._extensions())
        payload = Payload(field, target.name, target.read_bytes())

        def work(job: Job) -> dict[str, Any]:
            record = self.api.convert(payload, "ir", on_event=_progress(job))
            outputs = [o for o in record.get("outputs", []) if isinstance(o, dict)]
            ir_out = next((o for o in outputs if o.get("format") == "ir"), None)
            if ir_out is None or not ir_out.get("download_path"):
                raise ApiError(500, "no_ir", "The API returned no IR for this drawing.")
            scratch = self.config.work_dir / f".fetch-{job.id}.ir.json.gz"
            try:
                self.api.download(str(ir_out["download_path"]), scratch)
                doc = read_ir(scratch)
            finally:
                scratch.unlink(missing_ok=True)
            workspace.start(doc, "fetched from the API")
            return {"version": workspace.current, "usage": record.get("usage")}

        job = self.jobs.start(f"fetch IR of {target.name}", work)
        self._fetching[target] = job
        return job

    # ---- convert -----------------------------------------------------------------------------

    def convert_drawing(
        self,
        path: str,
        to: str,
        version: str | None = None,
        scale: str | None = None,
        pages: str | None = None,
        use_edits: bool = True,
        out_dir: str | None = None,
    ) -> ToolOutput:
        missing = self._need_key()
        if missing:
            return missing

        def run() -> ToolOutput:
            target_format = (to or "").strip().lower().lstrip(".")
            if not target_format:
                raise PathError("Give the output format in to, e.g. dxf, pdf, jww or ir.")
            payload, sent = self._payload(path, use_edits)
            folder = output_dir(self.config.root, self.config.out_dir, out_dir)
            stem = Path(path).name

            def work(job: Job) -> dict[str, Any]:
                record = self.api.convert(
                    payload,
                    target_format,
                    target_version=version,
                    target_scale=scale,
                    pages=pages,
                    on_event=_progress(job),
                )
                return self._save_outputs(record, folder, sent)

            job = self.jobs.start(f"convert {stem} to {target_format}", work)
            if job.state == RUNNING:
                return self._ok(
                    {
                        "job_id": job.id,
                        "state": RUNNING,
                        "elapsed_sec": job.elapsed,
                        "message": self._msg("still_running", elapsed=job.elapsed),
                    }
                )
            return self._job_output(job)

        return self._guard(run)

    def _save_outputs(
        self, record: dict[str, Any], folder: Path, sent: dict[str, Any]
    ) -> dict[str, Any]:
        saved = []
        for output in record.get("outputs", []):
            if not isinstance(output, dict) or not output.get("download_path"):
                continue
            name = safe_output_name(str(output.get("filename") or output.get("format") or "output"))
            dest = unique_path(folder / name)
            size = self.api.download(str(output["download_path"]), dest)
            saved.append(
                {"format": output.get("format"), "path": self._rel(dest), "size_bytes": size}
            )
        body: dict[str, Any] = {**sent, "outputs": saved}
        if record.get("locked_outputs"):
            body["locked_outputs"] = record["locked_outputs"]
            urls = record.get("purchase") or {}
            body["locked"] = self._msg(
                "locked",
                fmt=str(record.get("target_format", "")).upper(),
                urls=", ".join(f"{k}: {v}" for k, v in urls.items()) or DEVELOPERS_URL,
            )
            if urls:
                body["purchase"] = urls
        loss = record.get("loss_report")
        if isinstance(loss, dict):
            body["loss"] = _loss_summary(loss)
        for key in ("detected_format", "target_format", "elapsed_ms", "usage"):
            if key in record:
                body[key] = record[key]
        return body

    def _job_output(self, job: Job) -> ToolOutput:
        if job.state == RUNNING:
            return self._ok(
                {
                    "job_id": job.id,
                    "state": RUNNING,
                    "elapsed_sec": job.elapsed,
                    "progress": job.progress,
                }
            )
        if job.state == FAILED:
            error = job.error
            if isinstance(error, ApiError):
                return self._api_error(error)
            if isinstance(error, (PathError, EditError)):
                return self._local_error(error, "bad_request")
            if isinstance(error, LaneBusy):
                return self._busy(error)
            return self._fail({"error": {"code": "client_error", "message": repr(error)}})
        result = job.result if isinstance(job.result, dict) else {"result": job.result}
        return self._ok(
            {"job_id": job.id, "state": job.state, "elapsed_sec": job.elapsed, **result}
        )

    def get_job(self, job_id: str) -> ToolOutput:
        job = self.jobs.get(job_id)
        if job is None:
            return self._fail(
                {"error": {"code": "unknown_job", "message": self._msg("unknown_job")}}
            )
        return self._job_output(job)

    # ---- account -----------------------------------------------------------------------------

    def account_status(self) -> ToolOutput:
        missing = self._need_key()
        if missing:
            return missing

        def run() -> ToolOutput:
            body = self.api.usage()
            out = self._strip(body)
            out["prices"] = DEVELOPERS_URL
            running = [{"job_id": j.id, "what": j.label} for j in self.jobs.running()]
            if running:
                out["running_jobs"] = running
            return self._ok(out)

        return self._guard(run)


def _progress(job: Job) -> Any:
    def on_event(event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "accepted":
            job.progress = "accepted by the API"
        elif kind == "heartbeat":
            job.progress = f"converting ({event.get('elapsed', '?')} s)"

    return on_event


def _describe(ops: list[dict[str, Any]] | None, prefix: str) -> str:
    counts = Counter(str(op.get("op")) for op in ops or [] if isinstance(op, dict))
    detail = ", ".join(f"{name}×{n}" for name, n in counts.items())
    return f"{prefix}: {detail}" if detail else prefix


def _next_hint(locale: str) -> str:
    if locale == "ja":
        return "render_drawing で描いて確かめ、convert_drawing で元の形式などに書き出します。"
    return "Check it with render_drawing, then write it with convert_drawing."


def _loss_summary(report: dict[str, Any], limit: int = 15) -> dict[str, Any]:
    entries = [e for e in report.get("entries", []) if isinstance(e, dict)]
    rows = []
    for entry in entries[:limit]:
        row = {k: entry[k] for k in ("severity", "code", "message", "count") if k in entry}
        rows.append(row)
    out: dict[str, Any] = {"entries": rows}
    if len(entries) > limit:
        out["more"] = len(entries) - limit
    for key in ("summary", "counts", "status"):
        if key in report:
            out[key] = report[key]
    return out


__all__ = ["MESSAGES", "ToolOutput", "Tools"]
