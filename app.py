"""
FastAPI + HTMX 前端：编辑压缩包内 ComicInfo.xml。
"""
import os
import time
import uuid
import urllib.parse
from pathlib import Path

from starlette.responses import StreamingResponse

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from edit_archive_xml import (
    ALL_MARK,
    CSV_HEADERS,
    batch_convert,
    batch_convert_all,
    batch_find_replace,
    batch_prefix,
    batch_set,
    batch_suffix,
    export_csv,
    extract_headers,
    import_csv_content,
    list_dirs_with_archives,
    scan_archives,
    save_archives,
    save_archives_streaming,
)

# 允许的根目录（逗号分隔）；未配置时不做限制，仅校验路径存在（适合本地使用）
ALLOWED_BASE_PATHS_STR = os.environ.get("ALLOWED_BASE_PATHS", "").strip()
ALLOWED_BASE_PATHS: list[str] = [
    p.strip() for p in ALLOWED_BASE_PATHS_STR.split(",") if p.strip()
]


def ensure_allowed_path(path: str) -> str | None:
    """将路径规范为绝对路径并校验：若配置了 ALLOWED_BASE_PATHS 则必须在某条根目录下，否则仅要求路径存在。"""
    if not path or not path.strip():
        return None
    abs_path = os.path.abspath(os.path.normpath(path.strip()))
    if not os.path.exists(abs_path):
        return None
    if not ALLOWED_BASE_PATHS:
        return abs_path
    for base in ALLOWED_BASE_PATHS:
        base_abs = os.path.abspath(os.path.normpath(base))
        try:
            common = os.path.commonpath([abs_path, base_abs])
            if common == base_abs or abs_path == base_abs:
                return abs_path
        except ValueError:
            continue
    return None


def check_scan_dir(path: str) -> tuple[str | None, str]:
    """校验扫描目录，返回 (规范后的路径, 错误信息)。路径可用时错误信息为空字符串。"""
    if not path or not path.strip():
        return None, "请填写章节压缩包目录。"
    abs_path = os.path.abspath(os.path.normpath(path.strip()))
    if not os.path.exists(abs_path):
        return None, f"目录不存在：{abs_path}"
    if not os.path.isdir(abs_path):
        return None, f"路径不是目录：{abs_path}"
    if ALLOWED_BASE_PATHS:
        if ensure_allowed_path(path) is None:
            return None, f"路径不在允许范围内（ALLOWED_BASE_PATHS）。请配置环境变量或将该目录加入白名单：{abs_path}"
    return abs_path, ""


def ensure_archives_allowed(archives: list[str]) -> bool:
    """检查 session 中的 archives 路径均在白名单内。"""
    for ap in archives:
        if ensure_allowed_path(ap) is None:
            return False
    return True


# 扫描结果服务端缓存，避免 archives 列表过大导致 session cookie 超限、保存时 session 为空
# key: token, value: {"archives": [...], "comic_dir": str, "ts": float}
_SCAN_CACHE: dict[str, dict] = {}
_CACHE_TTL_SEC = 3600 * 24  # 24 小时


def _get_archives_from_token(token: str) -> tuple[list[str], str]:
    """从 token 取 archives；返回 (archives, comic_dir)。无效则 ([], "")。"""
    if not token or not token.strip():
        return [], ""
    entry = _SCAN_CACHE.get(token.strip())
    if not entry:
        return [], ""
    if time.time() - entry.get("ts", 0) > _CACHE_TTL_SEC:
        del _SCAN_CACHE[token.strip()]
        return [], ""
    return entry.get("archives") or [], entry.get("comic_dir") or ""


app = FastAPI(title="MangaTag - 编辑压缩包内 XML")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "mangatag-edit-xml-secret-change-in-production"),
)
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面：编辑压缩包内 XML。启动时不显示上次的日志，始终清空。"""
    default_base_path = ALLOWED_BASE_PATHS[0] if ALLOWED_BASE_PATHS else ""
    return templates.TemplateResponse(
        "edit_xml.html",
        {
            "request": request,
            "csv_text": "",
            "scan_log": "",
            "save_log": "",
            "csv_headers": CSV_HEADERS,
            "all_mark": ALL_MARK,
            "sort_choices": ["按数字大小顺序", "按字母顺序", "按Number列数字大小排序"],
            "default_base_path": default_base_path,
        },
    )


def _browse_root() -> str:
    """获取浏览器的根路径。"""
    if ALLOWED_BASE_PATHS:
        return os.path.abspath(os.path.normpath(ALLOWED_BASE_PATHS[0]))
    return os.path.abspath(os.getcwd())


@app.get("/api/browse")
async def api_browse(path: str = ""):
    """列出指定路径下的子目录，用于文件夹浏览。返回 JSON。"""
    if path and path.strip():
        current = ensure_allowed_path(path.strip())
        if not current or not os.path.isdir(current):
            return JSONResponse({"error": "路径无效或不在允许范围内"}, status_code=400)
    else:
        current = _browse_root()
        if not os.path.isdir(current):
            return JSONResponse({"error": "根目录不存在"}, status_code=400)

    parent = os.path.dirname(current) if current != os.path.dirname(current) else None
    if ALLOWED_BASE_PATHS:
        base_abs = os.path.abspath(os.path.normpath(ALLOWED_BASE_PATHS[0]))
        if parent and parent != current:
            try:
                if os.path.commonpath([parent, base_abs]) != base_abs and parent != base_abs:
                    parent = None
            except ValueError:
                parent = None
        if current == base_abs:
            parent = None

    entries: list[dict] = []
    try:
        for name in sorted(os.listdir(current)):
            full = os.path.join(current, name)
            if os.path.isdir(full):
                if ALLOWED_BASE_PATHS and ensure_allowed_path(full) is None:
                    continue
                entries.append({"name": name, "path": full})
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({
        "current": current,
        "parent": parent,
        "entries": entries,
    })


@app.get("/api/dirs", response_class=HTMLResponse)
async def api_dirs(request: Request, base_path: str = ""):
    """返回包含 .zip/.cbz 的子目录列表（仅包含在允许根目录范围内的子目录）。"""
    allowed_base = ensure_allowed_path(base_path) if base_path else None
    if not allowed_base or not os.path.isdir(allowed_base):
        return HTMLResponse(
            '<option value="">-- 路径无效或不在允许范围内 --</option>'
        )
    raw_entries = list_dirs_with_archives(allowed_base)
    options = ['<option value="">-- 选择 --</option>']
    for rel in raw_entries:
        full = os.path.normpath(os.path.join(allowed_base, rel))
        if ensure_allowed_path(full) is None:
            continue
        esc = rel.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        options.append(f'<option value="{esc}">{rel}</option>')
    return HTMLResponse("\n".join(options))


@app.post("/scan", response_class=HTMLResponse)
async def post_scan(
    request: Request,
    comic_dir: str = Form(""),
    include_header: str = Form("true"),
    sort_mode: str = Form("按数字大小顺序"),
):
    """扫描目录，生成 CSV 并写入 session；返回扫描日志与 CSV 区的 OOB 片段。"""
    session = request.session
    include = include_header.lower() in ("1", "true", "yes", "on")
    allowed, err = check_scan_dir(comic_dir)
    if not allowed:
        session["scan_log"] = "错误：" + (err or "目录不存在或不在允许范围内。")
        session["last_csv"] = ""
        session["archives"] = []
        session["comic_dir"] = ""
        return templates.TemplateResponse(
            "partials/scan_result.html",
            {
                "request": request,
                "scan_log": session["scan_log"],
                "csv_text": "",
                "scan_token": "",
                "csv_headers": CSV_HEADERS,
            },
        )
    csv_text, scan_log, archives = scan_archives(allowed, include, sort_mode)
    session["scan_log"] = scan_log
    session["comic_dir"] = allowed
    # 用服务端缓存存 archives，避免 session cookie 过大导致保存时 session 为空
    scan_token = uuid.uuid4().hex
    _SCAN_CACHE[scan_token] = {"archives": archives, "comic_dir": allowed, "ts": time.time()}
    return templates.TemplateResponse(
        "partials/scan_result.html",
        {
            "request": request,
            "scan_log": scan_log,
            "csv_text": csv_text,
            "scan_token": scan_token,
            "csv_headers": CSV_HEADERS,
        },
    )


@app.post("/scan-stream")
async def post_scan_stream(
    request: Request,
    comic_dir: str = Form(""),
    include_header: str = Form("true"),
    sort_mode: str = Form("按数字大小顺序"),
):
    """仅返回扫描日志的流式输出（逐行文本），便于观察长时间扫描进度。"""
    session = request.session
    include = include_header.lower() in ("1", "true", "yes", "on")
    allowed, err = check_scan_dir(comic_dir)
    if not allowed:
        session["scan_log"] = "错误：" + (err or "目录不存在或不在允许范围内。")
        session["last_csv"] = ""
        session["archives"] = []
        session["comic_dir"] = ""

        def err_gen():
            msg = session["scan_log"]
            yield (msg + "\n").encode("utf-8")

        return StreamingResponse(err_gen(), media_type="text/plain; charset=utf-8")

    def gen():
        # 这里会完整执行一次扫描，然后将扫描日志按行输出。
        # 注意：/scan 本身也会执行一次扫描以生成 CSV 与缓存，因此同一次操作会扫描两次。
        # 若后续需要进一步优化，可考虑重构为共享一次扫描结果。
        _, scan_log, _ = scan_archives(allowed, include, sort_mode)
        for line in (scan_log or "").splitlines():
            yield (line + "\n").encode("utf-8")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/scan-json")
async def post_scan_json(
    request: Request,
    comic_dir: str = Form(""),
    include_header: str = Form("true"),
    sort_mode: str = Form("按数字大小顺序"),
):
    """
    扫描目录，返回 JSON 结果，供前端直接刷新 CSV 表格：
    { ok, error?, csv_text?, scan_log?, scan_token? }。
    """
    session = request.session
    include = include_header.lower() in ("1", "true", "yes", "on")
    allowed, err = check_scan_dir(comic_dir)
    if not allowed:
        msg = "错误：" + (err or "目录不存在或不在允许范围内。")
        session["scan_log"] = msg
        session["comic_dir"] = ""
        return JSONResponse({"ok": False, "error": msg}, status_code=400)

    csv_text, scan_log, archives = scan_archives(allowed, include, sort_mode)
    session["scan_log"] = scan_log
    session["comic_dir"] = allowed
    # 缓存 archives，避免存入 session
    scan_token = uuid.uuid4().hex
    _SCAN_CACHE[scan_token] = {"archives": archives, "comic_dir": allowed, "ts": time.time()}
    return JSONResponse(
        {
            "ok": True,
            "csv_text": csv_text,
            "scan_log": scan_log,
            "scan_token": scan_token,
        }
    )


@app.post("/save", response_class=HTMLResponse)
async def post_save(
    request: Request,
    csv_text: str = Form(""),
    include_header: str = Form("true"),
    check_count: str = Form("true"),
    scan_token: str = Form(""),
):
    """将 CSV 写回压缩包，返回保存日志片段。优先从 scan_token 取 archives，避免 session cookie 过大导致为空。"""
    session = request.session
    archives, _ = _get_archives_from_token(scan_token)
    if not archives:
        archives = session.get("archives") or []
    if not archives:
        session["save_log"] = "请先扫描目录以建立压缩包顺序。"
        return templates.TemplateResponse(
            "partials/save_log.html",
            {"request": request, "save_log": session["save_log"]},
        )
    if not ensure_archives_allowed(archives):
        session["save_log"] = "错误：扫描到的压缩包路径不在允许范围内。"
        return templates.TemplateResponse(
            "partials/save_log.html",
            {"request": request, "save_log": session["save_log"]},
        )
    # 若表单未带上 csv_text（如 HTMX 未包含到），此时视为无可保存内容，由 save_archives 负责给出提示
    include = include_header.lower() in ("1", "true", "yes", "on")
    check = check_count.lower() in ("1", "true", "yes", "on")
    save_log, _ = save_archives(archives, csv_text or "", include, check)
    session["save_log"] = save_log
    return templates.TemplateResponse(
        "partials/save_log.html",
        {"request": request, "save_log": save_log},
    )


def _save_stream_generator(archives: list[str], csv_text: str, include: bool, check: bool):
    """生成逐行日志，每行末尾带换行，便于前端按行追加。"""
    for line in save_archives_streaming(archives, csv_text, include, check):
        yield (line + "\n").encode("utf-8")


def _build_content_disposition(filename: str) -> str:
    """
    构造 Content-Disposition，兼容包含中文等非 ASCII 文件名。
    - 若文件名可用 latin-1 编码，则直接使用 filename=
    - 否则使用 RFC 5987 格式的 filename*，并提供 ASCII 回退名
    """
    if not filename:
        return 'attachment; filename="export.csv"'
    try:
        filename.encode("latin-1")
        return f'attachment; filename="{filename}"'
    except UnicodeEncodeError:
        quoted = urllib.parse.quote(filename, encoding="utf-8", safe="")
        # 回退名必须是 ASCII，避免再次触发编码错误
        fallback = "export.csv"
        return f"attachment; filename={fallback}; filename*=UTF-8''{quoted}"


@app.post("/save-stream")
async def post_save_stream(
    request: Request,
    csv_text: str = Form(""),
    include_header: str = Form("true"),
    check_count: str = Form("true"),
    scan_token: str = Form(""),
):
    """流式保存：每处理完一个文档即返回一行日志，前端可逐条显示。"""
    session = request.session
    archives, _ = _get_archives_from_token(scan_token)
    if not archives:
        archives = session.get("archives") or []
    if not archives:
        def err():
            yield "请先扫描目录以建立压缩包顺序。\n".encode("utf-8")
        return StreamingResponse(err(), media_type="text/plain; charset=utf-8")
    if not ensure_archives_allowed(archives):
        def err():
            yield "错误：扫描到的压缩包路径不在允许范围内。\n".encode("utf-8")
        return StreamingResponse(err(), media_type="text/plain; charset=utf-8")
    # 若表单未带上 csv_text，此时视为无可保存内容，由 save_archives_streaming 负责给出提示
    include = include_header.lower() in ("1", "true", "yes", "on")
    check = check_count.lower() in ("1", "true", "yes", "on")
    return StreamingResponse(
        _save_stream_generator(archives, csv_text or "", include, check),
        media_type="text/plain; charset=utf-8",
    )


@app.get("/export", response_class=Response)
async def get_export(request: Request):
    """从 session 取 comic_dir/archives，生成 CSV 下载（兼容旧链接）。不再依赖 last_csv，避免大 CSV 存入 Cookie。"""
    session = request.session
    comic_dir = session.get("comic_dir", "")
    archives = session.get("archives") or []
    include_header = True
    # 传入空 csv_text，让 export_csv 根据 archives 重新生成 CSV 内容
    data, filename = export_csv("", include_header, comic_dir, archives)
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": _build_content_disposition(filename),
        },
    )


@app.post("/export", response_class=Response)
async def post_export(
    request: Request,
    csv_text: str = Form(""),
    include_header: str = Form("true"),
):
    """用当前提交的 csv_text 生成 CSV 下载，避免 session 为空导致内容为空。"""
    session = request.session
    comic_dir = session.get("comic_dir", "")
    archives = session.get("archives") or []
    include = include_header.lower() in ("1", "true", "yes", "on")
    data, filename = export_csv(csv_text or "", include, comic_dir, archives)
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": _build_content_disposition(filename),
        },
    )


@app.post("/import", response_class=HTMLResponse)
async def post_import(
    request: Request,
    import_file: UploadFile | None = None,
    include_header: str = Form("true"),
):
    """上传 CSV 文件，解析后直接返回 CSV 编辑区片段（不再写入 session，避免 Cookie 过大）。"""
    include = include_header.lower() in ("1", "true", "yes", "on")
    csv_text = ""
    if import_file and import_file.filename and import_file.filename.lower().endswith((".csv", ".txt")):
        try:
            body = await import_file.read()
            csv_text = import_csv_content(body, include)
        except Exception:
            csv_text = ""
    return templates.TemplateResponse(
        "partials/csv_area_import.html",
        {"request": request, "csv_text": csv_text or "", "csv_headers": CSV_HEADERS},
    )


@app.post("/batch-edit", response_class=HTMLResponse)
async def post_batch_edit(
    request: Request,
    csv_text: str = Form(""),
    include_header: str = Form("true"),
    action: str = Form(""),
    batch_set_val: str = Form(""),
    fr_find: str = Form(""),
    fr_replace: str = Form(""),
    prefix_val: str = Form(""),
    suffix_val: str = Form(""),
):
    """批量编辑 CSV：batch_set / find_replace / prefix / suffix / t2s / s2t。返回更新后的 CSV 区片段。"""
    form = await request.form()
    cols = form.getlist("columns") if "columns" in form else []
    include = include_header.lower() in ("1", "true", "yes", "on")
    out = csv_text
    if action == "batch_set":
        out = batch_set(csv_text, include, cols, batch_set_val)
    elif action == "find_replace":
        out = batch_find_replace(csv_text, include, cols, fr_find, fr_replace)
    elif action == "prefix":
        out = batch_prefix(csv_text, include, cols, prefix_val)
    elif action == "suffix":
        out = batch_suffix(csv_text, include, cols, suffix_val)
    elif action == "t2s":
        if cols:
            out = batch_convert(csv_text, include, cols, "t2s")
        else:
            out = batch_convert_all(csv_text, include, "t2s")
    elif action == "s2t":
        if cols:
            out = batch_convert(csv_text, include, cols, "s2t")
        else:
            out = batch_convert_all(csv_text, include, "s2t")
    return templates.TemplateResponse(
        "partials/csv_area.html",
        {"request": request, "csv_text": out, "csv_headers": CSV_HEADERS},
    )
