"""
FastAPI + HTMX 前端：编辑压缩包内 ComicInfo.xml。
"""
import os
import re
import time
import uuid
import urllib.parse
from pathlib import Path
import csv
import io

from starlette.responses import StreamingResponse

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

try:
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None

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
    preview_rename_by_rule,
    rename_archives_by_rule,
    scan_archives,
    save_archives,
    save_archives_streaming,
    opencc,
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


try:
    if opencc is not None:
        _OPENCC_T2S = opencc.OpenCC("t2s")
        _OPENCC_S2T = opencc.OpenCC("s2t")
    else:
        _OPENCC_T2S = None
        _OPENCC_S2T = None
except Exception:
    _OPENCC_T2S = None
    _OPENCC_S2T = None


def _normalize_t_s(text: str) -> set[str]:
    """
    将字符串规范为一组用于模糊匹配的形式：
    - 保留原文及其小写
    - 若安装了 opencc，则同时加入繁->简 与 简->繁 的转换结果及其小写
    """
    forms: set[str] = set()
    if not text:
        return forms
    forms.add(text)
    forms.add(text.lower())
    # 若可用，则加入繁简转换结果
    for converter in (_OPENCC_T2S, _OPENCC_S2T):
        if converter is None:
            continue
        try:
            converted = converter.convert(text)
        except Exception:
            continue
        if converted:
            forms.add(converted)
            forms.add(converted.lower())
    return forms


def _match_dir_name(rel_path: str, query: str) -> bool:
    """判断目录相对路径在简体/繁体/拼音等综合形式下是否匹配查询字符串。"""
    query = (query or "").strip().lower()
    if not query:
        return True
    search_val = _build_search_value(rel_path) or ""
    return query in search_val.lower()


def _build_search_value(rel_path: str) -> str:
    """
    构造用于 datalist 匹配的 value：
    - 包含原始目录名
    - 若安装了 opencc，则追加繁->简、简->繁等多种形式
    - 若安装了 pypinyin，则追加整串拼音与首字母缩写
    这样浏览器原生匹配时，输入简体/繁体/拼音都能命中。
    """
    forms = _normalize_t_s(rel_path) or {rel_path}
    if not isinstance(forms, set):
        forms = set(forms)

    # 拼音形式
    if lazy_pinyin is not None:
        try:
            py_list = lazy_pinyin(rel_path, errors="ignore") or []
            if py_list:
                full_py = " ".join(py_list)
                abbr_py = "".join(p[0] for p in py_list if p)
                for s in (full_py, abbr_py):
                    if not s:
                        continue
                    forms.add(s)
                    forms.add(s.lower())
        except Exception:
            pass
    ordered: list[str] = []
    for s in forms:
        if not s:
            continue
        if s not in ordered:
            ordered.append(s)
    return " ".join(ordered) if ordered else rel_path


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


def _get_version() -> str:
    """从 pyproject.toml 解析 version 字段。"""
    pyproject = BASE_DIR / "pyproject.toml"
    if not pyproject.exists():
        return "dev"
    try:
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        return m.group(1) if m else "dev"
    except Exception:
        return "dev"
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


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
            "version": _get_version(),
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


@app.get("/api/dirs-search")
async def api_dirs_search(base_path: str = "", q: str = "", limit: int = 50):
    """
    根据基路径和查询字符串返回匹配的漫画子目录列表。
    匹配时对简体/繁体不敏感：输入简体可匹配繁体目录名，反之亦然。
    """
    allowed_base = ensure_allowed_path(base_path) if base_path else None
    if not allowed_base or not os.path.isdir(allowed_base):
        return JSONResponse({"entries": []})
    raw_entries = list_dirs_with_archives(allowed_base)
    q = (q or "").strip()
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 50
    if limit_int <= 0:
        limit_int = 50

    def _wrap(rel: str) -> dict[str, str]:
        return {"rel": rel, "search": _build_search_value(rel)}

    # 无关键字时返回前 N 条（带复合搜索 value）
    if not q:
        entries = [_wrap(rel) for rel in raw_entries[:limit_int]]
        return JSONResponse({"entries": entries})

    matched: list[dict[str, str]] = []
    for rel in raw_entries:
        if _match_dir_name(rel, q):
            matched.append(_wrap(rel))
            if len(matched) >= limit_int:
                break
    return JSONResponse({"entries": matched})


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
    # 基于当前 CSV 内容构建「扫描时」的原始行映射，用于后续保存时判断是否改动
    orig_rows: dict[str, list[str]] = {}
    try:
        reader = csv.reader(io.StringIO(csv_text or ""))
        rows = list(reader)
        start_idx = 1 if include and rows else 0
        for r in rows[start_idx:]:
            if not r:
                continue
            fn = (r[0] if len(r) > 0 else "").strip()
            if not fn:
                continue
            orig_rows[fn] = r
    except Exception:
        orig_rows = {}

    # 用服务端缓存存 archives 与原始行，避免 session cookie 过大导致保存时 session 为空
    scan_token = uuid.uuid4().hex
    _SCAN_CACHE[scan_token] = {
        "archives": archives,
        "comic_dir": allowed,
        "orig_rows": orig_rows,
        "ts": time.time(),
    }
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

    # 构建「扫描时」的原始行映射
    orig_rows: dict[str, list[str]] = {}
    try:
        reader = csv.reader(io.StringIO(csv_text or ""))
        rows = list(reader)
        start_idx = 1 if include and rows else 0
        for r in rows[start_idx:]:
            if not r:
                continue
            fn = (r[0] if len(r) > 0 else "").strip()
            if not fn:
                continue
            orig_rows[fn] = r
    except Exception:
        orig_rows = {}

    # 缓存 archives 与原始行，避免存入 session
    scan_token = uuid.uuid4().hex
    _SCAN_CACHE[scan_token] = {
        "archives": archives,
        "comic_dir": allowed,
        "orig_rows": orig_rows,
        "ts": time.time(),
    }
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
    cache_entry = _SCAN_CACHE.get(scan_token) or {}
    archives, _ = _get_archives_from_token(scan_token)
    if not archives:
        archives = session.get("archives") or []
    if not archives:
        session["save_log"] = "请先扫描目录以建立压缩包顺序。"
        return templates.TemplateResponse(
            "partials/save_log.html",
            {
                "request": request,
                "save_log": session["save_log"],
                "scan_log": session.get("scan_log", ""),
            },
        )
    if not ensure_archives_allowed(archives):
        session["save_log"] = "错误：扫描到的压缩包路径不在允许范围内。"
        return templates.TemplateResponse(
            "partials/save_log.html",
            {
                "request": request,
                "save_log": session["save_log"],
                "scan_log": session.get("scan_log", ""),
            },
        )
    # 若表单未带上 csv_text（如 HTMX 未包含到），此时视为无可保存内容，由 save_archives 负责给出提示
    include = include_header.lower() in ("1", "true", "yes", "on")
    check = check_count.lower() in ("1", "true", "yes", "on")
    orig_rows = cache_entry.get("orig_rows") or None
    save_log, _ = save_archives(archives, csv_text or "", include, check, orig_rows)
    session["save_log"] = save_log
    return templates.TemplateResponse(
        "partials/save_log.html",
        {
            "request": request,
            "save_log": save_log,
            "scan_log": session.get("scan_log", ""),
        },
    )


def _save_stream_generator(
    archives: list[str],
    csv_text: str,
    include: bool,
    check: bool,
    original_rows: dict[str, list[str]] | None,
):
    """生成逐行日志，每行末尾带换行，便于前端按行追加。"""
    for line in save_archives_streaming(archives, csv_text, include, check, original_rows):
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
async def post_save_stream(request: Request):
    """
    流式保存：每处理完一个文档即返回一行日志，前端可逐条显示。
    为避免 multipart 的单字段 1MB 限制，这里使用 JSON 请求体而非表单。
    预期 JSON 结构：
      {
        "csv_text": str,
        "include_header": bool | str,
        "check_count": bool | str,
        "scan_token": str
      }
    """
    session = request.session

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    scan_token = str(payload.get("scan_token") or "")
    csv_text = str(payload.get("csv_text") or "")
    include_raw = payload.get("include_header", True)
    check_raw = payload.get("check_count", True)

    cache_entry = _SCAN_CACHE.get(scan_token) or {}
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

    include = str(include_raw).lower() in ("1", "true", "yes", "on")
    check = str(check_raw).lower() in ("1", "true", "yes", "on")
    orig_rows = cache_entry.get("orig_rows") or None
    return StreamingResponse(
        _save_stream_generator(archives, csv_text or "", include, check, orig_rows),
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
    comic_dir: str = Form(""),
):
    """用当前提交的 csv_text 生成 CSV 下载，避免 session 为空导致内容为空。"""
    session = request.session
    # 优先使用表单传入的章节目录（当前页面的章节压缩包目录），否则回退到 session 中的 comic_dir
    used_dir = (comic_dir or "").strip() or session.get("comic_dir", "")
    archives = session.get("archives") or []
    include = include_header.lower() in ("1", "true", "yes", "on")
    data, filename = export_csv(csv_text or "", include, used_dir, archives)
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
    fr_regex: str = Form(""),
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
        use_regex = (fr_regex or "").lower() in ("1", "true", "yes", "on")
        out = batch_find_replace(csv_text, include, cols, fr_find, fr_replace, use_regex)
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


@app.post("/batch-rename-preview")
async def post_batch_rename_preview(request: Request):
    """
    预览批量改名结果，不执行实际重命名。
    返回 JSON：ok, preview: [(old_name, new_name), ...], error?
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    scan_token = str(payload.get("scan_token") or "")
    csv_text = str(payload.get("csv_text") or "")
    include_raw = payload.get("include_header", True)
    rule = str(payload.get("rule") or "").strip()
    ws_replace_enabled = payload.get("ws_replace_enabled", True)
    ws_replace_char = str(payload.get("ws_replace_char") or "_") if ws_replace_enabled else ""
    conflict_mode = str(payload.get("conflict_mode") or "suffix")

    if not rule:
        return JSONResponse({"ok": False, "error": "规则不能为空"}, status_code=400)

    archives, _ = _get_archives_from_token(scan_token)
    if not archives:
        return JSONResponse({"ok": False, "error": "请先扫描目录"}, status_code=400)

    include = str(include_raw).lower() in ("1", "true", "yes", "on")
    preview_list, err = preview_rename_by_rule(
        archives=archives,
        csv_text=csv_text,
        include_header=include,
        rule=rule,
        ws_replace_char=ws_replace_char,
        conflict_mode=conflict_mode,
    )

    if err:
        return JSONResponse({"ok": False, "error": err}, status_code=400)

    return JSONResponse({"ok": True, "preview": preview_list})


@app.post("/batch-rename")
async def post_batch_rename(request: Request):
    """
    批量改名：根据规则重命名物理文件并更新 CSV 的 FileName 列。
    接受 JSON：csv_text, include_header, scan_token, rule, ws_replace_char, conflict_mode。
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    scan_token = str(payload.get("scan_token") or "")
    csv_text = str(payload.get("csv_text") or "")
    include_raw = payload.get("include_header", True)
    rule = str(payload.get("rule") or "").strip()
    ws_replace_enabled = payload.get("ws_replace_enabled", True)
    ws_replace_char = str(payload.get("ws_replace_char") or "_") if ws_replace_enabled else ""
    conflict_mode = str(payload.get("conflict_mode") or "suffix")

    if not rule:
        return JSONResponse({"ok": False, "error": "规则不能为空"}, status_code=400)

    cache_entry = _SCAN_CACHE.get(scan_token) or {}
    archives, comic_dir = _get_archives_from_token(scan_token)
    if not archives:
        return JSONResponse({"ok": False, "error": "请先扫描目录以建立压缩包顺序"}, status_code=400)
    if not ensure_archives_allowed(archives):
        return JSONResponse({"ok": False, "error": "扫描到的压缩包路径不在允许范围内"}, status_code=400)

    include = str(include_raw).lower() in ("1", "true", "yes", "on")
    if not comic_dir:
        return JSONResponse({"ok": False, "error": "章节目录不存在"}, status_code=400)

    new_csv_text, log, new_archives = rename_archives_by_rule(
        archives=archives,
        comic_dir=comic_dir,
        csv_text=csv_text,
        include_header=include,
        rule=rule,
        ws_replace_char=ws_replace_char,
        conflict_mode=conflict_mode,
    )

    if log.startswith("错误："):
        return JSONResponse({"ok": False, "error": log, "log": log}, status_code=400)

    orig_rows: dict[str, list[str]] = {}
    try:
        reader = csv.reader(io.StringIO(new_csv_text or ""))
        rows = list(reader)
        start_idx = 1 if include and rows and rows[0][:1] == ["FileName"] else 0
        for r in rows[start_idx:]:
            if not r:
                continue
            fn = (r[0] if len(r) > 0 else "").strip()
            if not fn:
                continue
            orig_rows[fn] = r
    except Exception:
        orig_rows = {}

    _SCAN_CACHE[scan_token] = {
        **cache_entry,
        "archives": new_archives,
        "orig_rows": orig_rows,
        "ts": time.time(),
    }

    return JSONResponse({"ok": True, "csv_text": new_csv_text, "log": log})
