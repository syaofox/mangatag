"""
编辑压缩包内 ComicInfo.xml 的业务逻辑。
从 webui 抽离，供 FastAPI 与 Gradio 共用。
"""
import csv
import io
import os
import re
import tempfile
import zipfile
from typing import Any

from lxml import etree

try:
    import opencc
except ImportError:
    opencc = None

from update_archives_with_xml import list_archives

# ---------------------------------------------------------------------------
# XML / ZIP 读写
# ---------------------------------------------------------------------------


def read_xml_from_archive(archive_path: str) -> bytes | None:
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            target_name = None
            for info in zf.infolist():
                if info.filename == "ComicInfo.xml":
                    target_name = info.filename
                    break
            if target_name is None:
                for info in zf.infolist():
                    if info.filename.lower() == "comicinfo.xml":
                        target_name = info.filename
                        break
            if target_name is None:
                return None
            return zf.read(target_name)
    except Exception:
        return None


def parse_xml_fields(xml_bytes: bytes) -> dict[str, str]:
    try:
        root = etree.fromstring(xml_bytes)

        def get(tag: str) -> str:
            elem = root.find(tag)
            return (elem.text or "").strip() if elem is not None and elem.text else ""

        return {
            "Title": get("Title"),
            "Series": get("Series"),
            "Number": get("Number"),
            "Summary": get("Summary"),
            "Writer": get("Writer"),
            "Genre": get("Genre"),
            "Web": get("Web"),
            "PublishingStatusTachiyomi": get("PublishingStatusTachiyomi"),
            "SourceMihon": get("SourceMihon"),
            "PublicationYear": get("PublicationYear"),
            "PublicationMonth": get("PublicationMonth"),
        }
    except Exception:
        return {
            "Title": "",
            "Series": "",
            "Number": "",
            "Summary": "",
            "Writer": "",
            "Genre": "",
            "Web": "",
            "PublishingStatusTachiyomi": "",
            "SourceMihon": "",
            "PublicationYear": "",
            "PublicationMonth": "",
        }


XML_FIELD_TAGS = [
    "Title",
    "Series",
    "Number",
    "Summary",
    "Writer",
    "Genre",
    "Web",
    "PublishingStatusTachiyomi",
    "SourceMihon",
    "PublicationYear",
    "PublicationMonth",
]


def build_xml_from_fields(fields: dict[str, Any]) -> bytes:
    root = etree.Element("ComicInfo")
    for tag in XML_FIELD_TAGS:
        val = (fields.get(tag) or "").strip()
        if isinstance(val, str):
            etree.SubElement(root, tag).text = val
        else:
            etree.SubElement(root, tag).text = str(val)
    tree = etree.ElementTree(root)
    return etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding="UTF-8")


def write_xml_to_archive(archive_path: str, xml_bytes: bytes) -> bool:
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            dir_name = os.path.dirname(archive_path)
            fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="tmp_edit_", dir=dir_name)
            os.close(fd)
            try:
                with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zfw:
                    for info in zf.infolist():
                        if info.filename.lower() == "comicinfo.xml":
                            continue
                        data = zf.read(info.filename)
                        zfw.writestr(info, data)
                    zfw.writestr("ComicInfo.xml", xml_bytes)
                os.replace(tmp_path, archive_path)
                return True
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CSV 表头与目录扫描
# ---------------------------------------------------------------------------

CSV_HEADERS = [
    "FileName",
    "Title",
    "Series",
    "Number",
    "Summary",
    "Writer",
    "Genre",
    "Web",
    "PublishingStatusTachiyomi",
    "SourceMihon",
    "PublicationYear",
    "PublicationMonth",
]

ALL_MARK = "【选择全部】"


def list_dirs_with_archives(base_path: str) -> list[str]:
    """递归扫描 base_path，返回包含 .zip/.cbz 的子目录相对路径列表（用于下拉选择）。"""
    if not base_path or not os.path.isdir(base_path):
        return []

    def scan(path: str) -> list[str]:
        entries: list[str] = []
        try:
            for name in sorted(os.listdir(path)):
                full_path = os.path.join(path, name)
                if os.path.isdir(full_path):
                    has_archive = False
                    try:
                        for sub_name in os.listdir(full_path):
                            if sub_name.lower().endswith((".zip", ".cbz")):
                                has_archive = True
                                break
                    except Exception:
                        continue
                    if has_archive:
                        entries.append(os.path.relpath(full_path, base_path))
                    else:
                        entries.extend(scan(full_path))
        except Exception:
            pass
        return entries

    return scan(base_path)


def sort_archives(archives: list[str], sort_mode: str) -> list[str]:
    """sort_mode: 按数字大小顺序 | 按字母顺序 | 按Number列数字大小排序（需配合预读缓存）。"""
    if sort_mode == "按数字大小顺序":
        def key_func(path: str):
            name = os.path.basename(path)
            base = os.path.splitext(name)[0]
            m = re.match(r"^(\D*)(\d+)?", base)
            prefix = (m.group(1) if m else "").lower()
            num = int(m.group(2)) if m and m.group(2) else None
            has_num_flag = 0 if num is not None else 1
            num_val = num if num is not None else 0
            return (prefix, has_num_flag, num_val, name.lower())
        return sorted(archives, key=key_func)
    if sort_mode == "按字母顺序":
        return sorted(archives, key=lambda p: os.path.basename(p).lower())
    # 按Number列数字大小排序：由调用方先排序好再传入，这里直接返回
    return archives


def _sort_by_number_field(archives: list[str], cached_fields: dict[str, dict]) -> list[str]:
    def parse_num(val: Any) -> int | float | None:
        if val is None:
            return None
        s = str(val).strip()
        if s == "":
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None

    def key_num(path: str):
        fields = cached_fields.get(path)
        num_val = parse_num(fields.get("Number") if fields else None) if fields else None
        has_num_flag = 0 if (num_val is not None) else 1
        num_sort = num_val if num_val is not None else 0
        return (has_num_flag, num_sort, os.path.basename(path).lower())

    return sorted(archives, key=key_num)


def scan_archives(
    comic_dir: str,
    include_header: bool,
    sort_mode: str,
) -> tuple[str, str, list[str]]:
    """
    扫描目录中的 .cbz/.zip，读取 ComicInfo.xml 生成 CSV。
    返回 (csv_text, scan_log, archives_full_paths)。
    """
    logs: list[str] = []
    if not comic_dir or not os.path.isdir(comic_dir):
        return ("", "错误：目录不存在或为空", [])

    archives = list_archives(comic_dir)
    cached_fields: dict[str, dict] = {}

    if sort_mode == "按Number列数字大小排序":
        for ap in archives:
            try:
                xml_bytes = read_xml_from_archive(ap)
                if xml_bytes is not None:
                    cached_fields[ap] = parse_xml_fields(xml_bytes)
            except Exception:
                pass
        archives = _sort_by_number_field(archives, cached_fields)
    else:
        archives = sort_archives(archives, sort_mode)

    logs.append(f"发现压缩包：{len(archives)} 个，排序：{sort_mode}")

    output = io.StringIO()
    writer = csv.writer(output)
    if include_header:
        writer.writerow(CSV_HEADERS)

    for i, ap in enumerate(archives, start=1):
        base_name = os.path.basename(ap)
        try:
            fields = cached_fields.get(ap)
            if fields is None:
                xml_bytes = read_xml_from_archive(ap)
                if xml_bytes is not None:
                    fields = parse_xml_fields(xml_bytes)
            if fields is None:
                base = os.path.splitext(base_name)[0]
                series = os.path.basename(os.path.dirname(ap)) if os.path.dirname(ap) else ""
                writer.writerow([base_name, base, series, "", "", "", "", "", "", "", "", ""])
                logs.append(f"[{i}/{len(archives)}] 无 ComicInfo.xml -> 预填 Title='{base}', Series='{series}'")
            else:
                writer.writerow([
                    base_name,
                    fields.get("Title", ""),
                    fields.get("Series", ""),
                    fields.get("Number", ""),
                    fields.get("Summary", ""),
                    fields.get("Writer", ""),
                    fields.get("Genre", ""),
                    fields.get("Web", ""),
                    fields.get("PublishingStatusTachiyomi", ""),
                    fields.get("SourceMihon", ""),
                    fields.get("PublicationYear", ""),
                    fields.get("PublicationMonth", ""),
                ])
                logs.append(f"[{i}/{len(archives)}] 读取 ComicInfo.xml 成功 -> {base_name}")
        except Exception as e:
            writer.writerow([os.path.basename(ap)] + [""] * 11)
            logs.append(f"[{i}/{len(archives)}] 读取失败 -> {base_name}: {e}")

    return (output.getvalue(), "\n".join(logs), archives)


def strip_optional_header(rows: list[list[str]], include_header: bool) -> list[list[str]]:
    if not rows:
        return rows
    first_row = [c.strip() for c in rows[0]] if rows else []
    if (include_header and first_row[:1] == ["FileName"]) or (rows and first_row[:1] == ["FileName"]):
        return rows[1:]
    return rows


def prune_trailing_empty_rows(rows: list[list[str]]) -> list[list[str]]:
    pruned = list(rows)
    while pruned and all((c is None or str(c).strip() == "") for c in pruned[-1]):
        pruned.pop()
    return pruned


def save_archives(
    archives: list[str],
    csv_text: str,
    include_header: bool,
    check_count: bool,
) -> tuple[str, bool]:
    """
    将 CSV 内容写回各压缩包。返回 (save_log, success)。
    success=False 表示校验失败未执行写入。
    """
    logs: list[str] = []
    if not csv_text:
        return ("无可保存的内容", False)
    if not archives:
        return ("请先扫描目录以建立压缩包顺序", False)

    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    rows = strip_optional_header(rows, include_header)
    rows = prune_trailing_empty_rows(rows)

    row_map: dict[str, list[str]] = {}
    duplicates: set[str] = set()
    for r in rows:
        if not r:
            continue
        fn = (r[0] if len(r) > 0 else "").strip()
        if not fn:
            continue
        if fn in row_map:
            duplicates.add(fn)
        else:
            row_map[fn] = r

    if duplicates:
        return (f"CSV 文件名重复：{len(duplicates)} 个，例如 {sorted(duplicates)[:3]} ...。已取消保存。", False)

    archive_names = [os.path.basename(a) for a in archives]
    set_archives = set(archive_names)
    set_csv = set(row_map.keys())
    missing = sorted(set_archives - set_csv)
    extra = sorted(set_csv - set_archives)

    if check_count:
        if missing:
            return (f"CSV 缺少以下文件名（共 {len(missing)}）：{', '.join(missing[:3])} ...。已取消保存。", False)
        if extra:
            return (f"CSV 包含未在扫描列表中的文件名（共 {len(extra)}）：{', '.join(extra[:3])} ...。已取消保存。", False)
    else:
        if missing:
            logs.append(f"提示：CSV 缺少 {len(missing)} 个文件，将跳过未提供行的文件。如：{', '.join(missing[:3])} ...")
        if extra:
            logs.append(f"提示：CSV 包含 {len(extra)} 个额外行（非扫描文件），将忽略。如：{', '.join(extra[:3])} ...")

    total = len(archives)
    for idx, ap in enumerate(archives):
        name = os.path.basename(ap)
        row = row_map.get(name)
        if row is None:
            logs.append(f"[{idx+1}/{total}] 跳过：CSV 未提供对应行 -> {name}")
            continue
        if len(row) < 12:
            row = row + [""] * (12 - len(row))
        fields = {
            "Title": row[1],
            "Series": row[2],
            "Number": row[3],
            "Summary": row[4],
            "Writer": row[5],
            "Genre": row[6],
            "Web": row[7],
            "PublishingStatusTachiyomi": row[8],
            "SourceMihon": row[9],
            "PublicationYear": row[10],
            "PublicationMonth": row[11],
        }
        xml_bytes = build_xml_from_fields(fields)
        ok = write_xml_to_archive(ap, xml_bytes)
        if ok:
            logs.append(f"[{idx+1}/{total}] 已保存: {name}")
        else:
            logs.append(f"[{idx+1}/{total}] 失败: {name}")

    logs.append("保存完成")
    return ("\n".join(logs), True)


def export_csv(
    csv_text: str,
    include_header: bool,
    comic_dir: str,
    archives: list[str],
) -> tuple[bytes, str]:
    """若 csv_text 为空则从 archives 重新生成。返回 (csv_bytes, suggested_filename)。"""
    import datetime
    import uuid

    text = csv_text or ""
    if not text.strip() and archives:
        out = io.StringIO()
        w = csv.writer(out)
        if include_header:
            w.writerow(CSV_HEADERS)
        for ap in archives:
            base_name = os.path.basename(ap)
            try:
                xml_bytes = read_xml_from_archive(ap)
                if xml_bytes is None:
                    base = os.path.splitext(base_name)[0]
                    series = os.path.basename(os.path.dirname(ap)) or ""
                    w.writerow([base_name, base, series, "", "", "", "", "", "", "", "", ""])
                else:
                    fields = parse_xml_fields(xml_bytes)
                    w.writerow([
                        base_name,
                        fields.get("Title", ""),
                        fields.get("Series", ""),
                        fields.get("Number", ""),
                        fields.get("Summary", ""),
                        fields.get("Writer", ""),
                        fields.get("Genre", ""),
                        fields.get("Web", ""),
                        fields.get("PublishingStatusTachiyomi", ""),
                        fields.get("SourceMihon", ""),
                        fields.get("PublicationYear", ""),
                        fields.get("PublicationMonth", ""),
                    ])
            except Exception:
                w.writerow([base_name] + [""] * 11)
        text = out.getvalue()

    rows = list(csv.reader(io.StringIO(text or "")))
    if include_header and rows and [c.strip() for c in rows[0]] != CSV_HEADERS:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(CSV_HEADERS)
        for r in rows:
            w.writerow(r)
        data = out.getvalue().encode("utf-8")
    else:
        data = (text or "").encode("utf-8")

    dir_name = os.path.basename(comic_dir) if comic_dir else "comicinfo"
    safe_name = "".join(c for c in dir_name if c.isalnum() or c in "._- ").strip() or "comicinfo"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    filename = f"{safe_name}_{ts}_{uid}.csv"
    return (data, filename)


def import_csv_content(file_content: bytes | str, include_header: bool) -> str:
    """解析上传的 CSV 文件内容，返回 CSV 文本。若 include_header 为 False 且首行为表头则去掉。"""
    if isinstance(file_content, bytes):
        try:
            content = file_content.decode("utf-8")
        except Exception:
            content = file_content.decode("utf-8", errors="ignore")
    else:
        content = file_content or ""
    rows = list(csv.reader(io.StringIO(content)))
    if not include_header and rows and [c.strip() for c in rows[0]] == CSV_HEADERS:
        out = io.StringIO()
        w = csv.writer(out)
        for r in rows[1:]:
            w.writerow(r)
        return out.getvalue()
    return content


# ---------------------------------------------------------------------------
# 批量编辑
# ---------------------------------------------------------------------------


def extract_headers(csv_text: str) -> list[str]:
    rows = list(csv.reader(io.StringIO(csv_text or "")))
    if not rows:
        return []
    return [c.strip() for c in rows[0]]


def resolve_selected_columns(
    csv_text: str,
    include_header: bool,
    selected_columns: list[str],
) -> list[str]:
    if not selected_columns:
        return []
    headers = extract_headers(csv_text) if include_header else CSV_HEADERS
    candidates = [h for h in headers if h and h != "FileName"] or CSV_HEADERS[1:]
    if ALL_MARK in selected_columns:
        return candidates
    return [c for c in selected_columns if c in candidates]


def _batch_apply(
    csv_text: str,
    include_header: bool,
    selected_columns: list[str],
    row_mutator: Any,
) -> str:
    """row_mutator(row: list, indices: list[int]) -> list."""
    if not csv_text or not selected_columns:
        return csv_text
    try:
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        if not rows:
            return csv_text
        header = [c.strip() for c in rows[0]]
        name_to_idx = {name: idx for idx, name in enumerate(header)}
        indices: list[int] = []
        if include_header and header and header[:1] == ["FileName"]:
            for col in selected_columns:
                idx = name_to_idx.get(col)
                if idx is not None and idx != 0:
                    indices.append(idx)
        else:
            for col in selected_columns:
                try:
                    idx = CSV_HEADERS.index(col)
                except ValueError:
                    idx = None
                if idx is not None and idx != 0:
                    indices.append(idx)
        if not indices:
            return csv_text
        output = io.StringIO()
        writer = csv.writer(output)
        for i, row in enumerate(rows):
            is_header = i == 0 and include_header and header[:1] == ["FileName"]
            if is_header:
                writer.writerow(row)
                continue
            max_needed = max(indices)
            if len(row) <= max_needed:
                row = row + [""] * (max_needed + 1 - len(row))
            writer.writerow(row_mutator(row, indices))
        return output.getvalue()
    except Exception:
        return csv_text


def batch_set(
    csv_text: str,
    include_header: bool,
    columns: list[str],
    value: str,
) -> str:
    def mut(row: list, idxs: list[int]):
        for j in idxs:
            row[j] = value or ""
        return row
    cols = resolve_selected_columns(csv_text, include_header, columns)
    return _batch_apply(csv_text, include_header, cols, mut)


def batch_find_replace(
    csv_text: str,
    include_header: bool,
    columns: list[str],
    find_str: str,
    replace_str: str,
) -> str:
    find_s = find_str or ""
    rep_s = replace_str or ""
    if find_s == "":
        return csv_text

    def mut(row: list, idxs: list[int]):
        for j in idxs:
            row[j] = (row[j] or "").replace(find_s, rep_s)
        return row
    cols = resolve_selected_columns(csv_text, include_header, columns)
    return _batch_apply(csv_text, include_header, cols, mut)


def batch_prefix(
    csv_text: str,
    include_header: bool,
    columns: list[str],
    prefix: str,
) -> str:
    pre = prefix or ""

    def mut(row: list, idxs: list[int]):
        for j in idxs:
            row[j] = pre + (row[j] or "")
        return row
    cols = resolve_selected_columns(csv_text, include_header, columns)
    return _batch_apply(csv_text, include_header, cols, mut)


def batch_suffix(
    csv_text: str,
    include_header: bool,
    columns: list[str],
    suffix: str,
) -> str:
    suf = suffix or ""

    def mut(row: list, idxs: list[int]):
        for j in idxs:
            row[j] = (row[j] or "") + suf
        return row
    cols = resolve_selected_columns(csv_text, include_header, columns)
    return _batch_apply(csv_text, include_header, cols, mut)


def batch_convert(
    csv_text: str,
    include_header: bool,
    columns: list[str],
    mode: str,
) -> str:
    """mode: 't2s' 繁体转简体 或 's2t' 简体转繁体"""
    if opencc is None:
        return csv_text
    try:
        converter = opencc.OpenCC(mode)
    except Exception:
        return csv_text

    def mut(row: list, idxs: list[int]):
        for j in idxs:
            if row[j]:
                row[j] = converter.convert(row[j])
        return row
    cols = resolve_selected_columns(csv_text, include_header, columns)
    return _batch_apply(csv_text, include_header, cols, mut)


def batch_convert_all(csv_text: str, include_header: bool, mode: str) -> str:
    if opencc is None:
        return csv_text
    try:
        converter = opencc.OpenCC(mode)
    except Exception:
        return csv_text
    headers = extract_headers(csv_text) if include_header else CSV_HEADERS
    cols = [h for h in headers if h and h != "FileName"] or CSV_HEADERS[1:]

    def mut(row: list, idxs: list[int]):
        for j in idxs:
            if row[j]:
                row[j] = converter.convert(row[j])
        return row
    return _batch_apply(csv_text, include_header, cols, mut)
