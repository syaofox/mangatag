import asyncio
import os
from urllib.parse import urljoin
import gradio as gr
from lxml import etree
import zipfile

from manhuagui import ManhuaguiScraper, parse_manga_url as parse_mh_url
from baozimanhua import BaoziScraper, parse_manga_url as parse_bz_url
from update_xml_numbers import (
    parse_prefix_number,
    find_comicinfo_xml,
    update_number_in_xml,
)
from update_archives_with_xml import (
    discover_xmls,
    list_archives,
    best_match,
    update_archive_with_xml,
)


async def run_scrape(site: str, manga_input: str, limit: int | None):
    # 选择站点与解析器
    if site == "Baozimh":
        scraper = BaoziScraper()
        parse_url = parse_bz_url
        site_label = "包子漫画"
    else:
        scraper = ManhuaguiScraper()
        parse_url = parse_mh_url
        site_label = "漫画柜"
    logs: list[str] = []

    def log(msg: str):
        logs.append(msg)
        return "\n".join(logs)

    try:
        manga_relative_url = parse_url(manga_input)
        yield log(f"[{site_label}] 解析的漫画URL: {manga_relative_url}")
    except Exception as e:
        yield log(f"错误: {e}")
        return

    import aiohttp

    async with aiohttp.ClientSession() as session:
        yield log(f"[{site_label}] 开始提取漫画详细信息...")
        manga_info = await scraper.get_manga_details(manga_relative_url, session)
        if not manga_info:
            yield log(f"[{site_label}] 漫画信息提取失败，请检查URL或网络连接。")
            return

        yield log(f"[{site_label}] 漫画信息提取成功。")
        series = manga_info.get("series", "Unknown")
        yield log(f"系列：{series}")
        yield log(f"作者：{manga_info.get('writer','')}")

        cover_url = manga_info.get('cover_url', '')
        if cover_url:
            manga_name = series.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            outputs_root = os.path.join("outputs", manga_name)
            os.makedirs(outputs_root, exist_ok=True)

            from urllib.parse import urlparse
            parsed = urlparse(cover_url)
            basename = os.path.basename(parsed.path)
            ext = os.path.splitext(basename)[1] or ".jpg"
            cover_path = os.path.join(outputs_root, f"cover{ext}")
            ok = await scraper.download_cover(cover_url, cover_path, session)
            if ok:
                yield log(f"封面已保存到 {cover_path}")
            else:
                yield log(f"[{site_label}] 封面下载失败，继续处理章节...")

        yield log(f"[{site_label}] 开始提取章节列表...")
        chapters = await scraper.get_chapter_list(manga_relative_url, session)
        if not chapters:
            yield log(f"[{site_label}] 章节列表为空或提取失败。")
            return

        yield log(f"[{site_label}] 找到 {len(chapters)} 个章节。")
        if limit:
            chapters = chapters[:limit]
            yield log(f"[{site_label}] 限制处理前 {len(chapters)} 个章节。")

        for i, chapter in enumerate(reversed(chapters)):
            full_chapter_url = urljoin(scraper.base_url, chapter['url'])
            yield log(f"[{site_label}] 正在处理章节：{chapter['title']} (URL: {full_chapter_url})")
            chapter_number = str(i + 1).zfill(3)
            xml_content = scraper.create_xml_file(manga_info, chapter['title'], chapter_number, full_chapter_url)

            manga_name = series.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            chapter_name = chapter_number + '-' + chapter['title'].replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
            output_dir = os.path.join("outputs", manga_name, chapter_name)
            os.makedirs(output_dir, exist_ok=True)
            xml_file_path = os.path.join(output_dir, "ComicInfo.xml")
            with open(xml_file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            yield log(f"[{site_label}] XML 文件已保存到 {xml_file_path}")

        yield log(f"[{site_label}] 所有章节的XML文件已生成。")


async def ui_run(site, manga_input, limit):
    limit_val = None
    try:
        if limit is not None and str(limit).strip() != "":
            limit_val = int(limit)
    except Exception:
        limit_val = None

    async for chunk in run_scrape(site, manga_input.strip(), limit_val):
        yield chunk


with gr.Blocks(title="MangaTag | Manhuagui/Baozimh") as demo:
    with gr.Tabs():
        with gr.Tab("抓取与生成XML"):
            gr.Markdown("**MangaTag - 抓取与XML生成（支持 漫画柜 / 包子漫画）**")
            with gr.Row():
                site_dd = gr.Dropdown(label="站点", choices=["Manhuagui", "Baozimh"], value="Manhuagui")
                manga_input = gr.Textbox(label="漫画URL或编号", placeholder="如 https://tw.manhuagui.com/comic/1055/ 或 1055")
                limit = gr.Number(label="限制章节数(可选)", precision=0)
            run_btn = gr.Button("开始")
            output = gr.Textbox(label="日志输出", lines=20)
            run_btn.click(fn=ui_run, inputs=[site_dd, manga_input, limit], outputs=output)

        with gr.Tab("更新XML Number"):
            gr.Markdown("**根据章节文件夹名更新 ComicInfo.xml 的 Number 字段**")
            manga_dir_tb = gr.Textbox(label="漫画目录路径", placeholder="如 /home/user/dev/mangatag/outputs/漫画名")
            with gr.Row():
                dry_run_cb = gr.Checkbox(label="试运行(dry-run)", value=True)
                verbose_cb = gr.Checkbox(label="详细日志(verbose)")
            run_update_btn = gr.Button("更新 Number")
            update_logs = gr.Textbox(label="处理日志", lines=20)

            def ui_update_numbers(manga_dir: str, dry_run: bool, verbose: bool):
                logs: list[str] = []

                def log(msg: str):
                    logs.append(msg)
                    return "\n".join(logs)

                if not manga_dir or not os.path.isdir(manga_dir):
                    yield log(f"错误：目录不存在 -> {manga_dir}")
                    return

                chapter_dirs = [
                    os.path.join(manga_dir, name)
                    for name in os.listdir(manga_dir)
                    if os.path.isdir(os.path.join(manga_dir, name))
                ]

                def sort_key(path: str):
                    folder = os.path.basename(path)
                    num = parse_prefix_number(folder)
                    return (0, int(num)) if num is not None else (1, folder)

                chapter_dirs.sort(key=sort_key)

                total = 0
                updated = 0

                for chapter_dir in chapter_dirs:
                    folder_name = os.path.basename(chapter_dir)
                    prefix = parse_prefix_number(folder_name)
                    if prefix is None:
                        if verbose:
                            yield log(f"跳过（无数字前缀）：{folder_name}")
                        continue

                    xml_path = find_comicinfo_xml(chapter_dir)
                    if xml_path is None:
                        if verbose:
                            yield log(f"未找到 ComicInfo.xml：{chapter_dir}")
                        continue

                    total += 1
                    if verbose or dry_run:
                        yield log(f"将更新 Number -> {prefix}: {xml_path}")
                    if update_number_in_xml(xml_path, prefix, dry_run=dry_run):
                        updated += 1

                yield log(f"处理完成：目标章节 {total}，成功更新 {updated}，dry-run={dry_run}")

            run_update_btn.click(
                fn=ui_update_numbers,
                inputs=[manga_dir_tb, dry_run_cb, verbose_cb],
                outputs=update_logs,
            )

        with gr.Tab("更新压缩包内XML"):
            gr.Markdown("**将 XML 写入章节压缩包(.cbz/.zip) 根目录为 ComicInfo.xml**")
            comic_dir_tb = gr.Textbox(label="章节压缩包目录(comic_dir)", placeholder="如 /path/to/comic/dir")
            xml_root_tb = gr.Textbox(label="XML 输出根目录(xml_root)", placeholder="如 outputs/天漫浮世錄")
            with gr.Row():
                threshold_num = gr.Slider(label="匹配阈值 threshold", minimum=0.0, maximum=1.0, step=0.01, value=0.60)
                strategy_dd = gr.Dropdown(label="匹配策略 strategy", choices=["both", "title", "folder"], value="both")
            with gr.Row():
                dry_run2 = gr.Checkbox(label="试运行(dry-run)", value=True)
                force_cb = gr.Checkbox(label="存在则覆盖(force)")
                verbose2 = gr.Checkbox(label="详细日志(verbose)")
            run_archives_btn = gr.Button("写入/更新 ComicInfo.xml")
            archives_logs = gr.Textbox(label="处理日志", lines=20)

            def ui_update_archives(comic_dir: str, xml_root: str, threshold: float, dry_run: bool, force: bool, verbose: bool, strategy: str):
                logs: list[str] = []

                def log(msg: str):
                    logs.append(msg)
                    return "\n".join(logs)

                if not comic_dir or not os.path.isdir(comic_dir):
                    yield log(f"错误：章节目录不存在 -> {comic_dir}")
                    return
                if not xml_root or not os.path.isdir(xml_root):
                    yield log(f"错误：XML 目录不存在 -> {xml_root}")
                    return

                xml_items = discover_xmls(xml_root)
                if not xml_items:
                    yield log("未发现任何 XML（ComicInfo.xml）。")
                    return

                archives = list_archives(comic_dir)
                if not archives:
                    yield log("未发现任何章节压缩包（.cbz/.zip）。")
                    return

                if verbose:
                    yield log(f"发现 XML 数量：{len(xml_items)}；压缩包数量：{len(archives)}")

                success = 0
                total = 0
                used_archives: set[str] = set()

                for title, xml_path, chapter_folder in xml_items:
                    chosen_path = None
                    chosen_score = 0.0
                    chosen_basis = ""

                    if strategy in ("title", "both"):
                        p, s = best_match(title, archives)
                        if s > chosen_score:
                            chosen_path, chosen_score, chosen_basis = p, s, "title"

                    if strategy in ("folder", "both"):
                        p2, s2 = best_match(chapter_folder, archives)
                        if s2 > chosen_score:
                            chosen_path, chosen_score, chosen_basis = p2, s2, "folder"

                    if chosen_path is None or chosen_score < float(threshold):
                        if verbose:
                            yield log(f"跳过：无匹配或分数过低（{chosen_score:.2f}） -> Title='{title}', Folder='{chapter_folder}'")
                        continue

                    total += 1
                    if chosen_path in used_archives:
                        if verbose:
                            yield log(f"跳过：目标压缩包已被占用 -> {os.path.basename(chosen_path)} | Title='{title}', Folder='{chapter_folder}'")
                        continue

                    if verbose or dry_run:
                        basis_desc = "标题" if chosen_basis == "title" else "章节文件夹名"
                        yield log(f"匹配成功（{chosen_score:.2f}, 基于{basis_desc}）：'{title}' | '{chapter_folder}' -> {os.path.basename(chosen_path)}")

                    if update_archive_with_xml(chosen_path, xml_path, dry_run=dry_run, force=force):
                        success += 1
                        used_archives.add(chosen_path)

                yield log(f"处理完成：发现{len(xml_items)}个XML，匹配目标 {total}，成功更新 {success}，dry-run={dry_run}, 阈值={float(threshold):.2f}")

            run_archives_btn.click(
                fn=ui_update_archives,
                inputs=[comic_dir_tb, xml_root_tb, threshold_num, dry_run2, force_cb, verbose2, strategy_dd],
                outputs=archives_logs,
            )

        with gr.Tab("编辑压缩包内XML"):
            gr.Markdown("**扫描目录中的 .cbz/.zip，读取 ComicInfo.xml 后在下方 CSV 文本中编辑并保存回压缩包**\n\n- 每行对应一个压缩包；若无 ComicInfo.xml 则输出空行。\n- 字段以逗号分隔，符合CSV标准（引号转义）。\n- 保存时将按扫描顺序逐行写入，并校验行数是否一致。")
            edit_dir_tb = gr.Textbox(label="章节压缩包目录", placeholder="如 /path/to/comic/dir")
            scan_btn = gr.Button("扫描目录并读取 ComicInfo.xml")
            include_header_cb = gr.Checkbox(label="包含表头", value=True)
            sort_dd = gr.Dropdown(label="排序方式", choices=["按字母顺序", "按数字大小顺序"], value="按字母顺序")
            strict_order_cb = gr.Checkbox(label="严格顺序校验（按扫描顺序保存）", value=True)
            csv_tb = gr.Textbox(label="CSV 编辑区", lines=18)
            with gr.Row():
                export_btn = gr.DownloadButton("导出CSV")
                import_file = gr.File(label="导入CSV", file_types=[".csv"]) 
            save_btn = gr.Button("保存修改到压缩包")
            scan_logs = gr.Textbox(label="扫描日志", lines=16)
            save_logs = gr.Textbox(label="保存日志", lines=16)

            def _read_xml_from_archive(archive_path: str):
                try:
                    with zipfile.ZipFile(archive_path, "r") as zf:
                        # 允许大小写差异，但优先严格匹配
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
                        data = zf.read(target_name)
                        return data
                except Exception:
                    return None

            def _parse_xml_fields(xml_bytes: bytes):
                try:
                    root = etree.fromstring(xml_bytes)
                    def get(tag):
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
                    }

            def _build_xml_from_fields(fields: dict) -> bytes:
                root = etree.Element("ComicInfo")
                for tag in [
                    "Title",
                    "Series",
                    "Number",
                    "Summary",
                    "Writer",
                    "Genre",
                    "Web",
                    "PublishingStatusTachiyomi",
                    "SourceMihon",
                ]:
                    val = (fields.get(tag) or "").strip()
                    etree.SubElement(root, tag).text = val
                tree = etree.ElementTree(root)
                return etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding="UTF-8")

            def _write_xml_to_archive(archive_path: str, xml_bytes: bytes) -> bool:
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

            import tempfile

            # 用闭包中的可变字典保存最近一次扫描顺序
            _edit_state = {"archives": []}
            _csv_headers = [
                "Title",
                "Series",
                "Number",
                "Summary",
                "Writer",
                "Genre",
                "Web",
                "PublishingStatusTachiyomi",
                "SourceMihon",
            ]

            def _sort_archives(archives: list[str], sort_mode: str) -> list[str]:
                # sort_mode: "按字母顺序" | "按数字大小顺序"
                if sort_mode == "按数字大小顺序":
                    def key_func(path: str):
                        name = os.path.basename(path)
                        num = parse_prefix_number(os.path.splitext(name)[0])
                        return (0, int(num)) if num is not None else (1, name.lower())
                    return sorted(archives, key=key_func)
                else:
                    return sorted(archives, key=lambda p: os.path.basename(p).lower())

            def scan_archives(comic_dir: str, include_header: bool, sort_mode: str):
                # 流式输出日志：在过程中持续产生日志，最终一次性输出CSV文本
                import io, csv
                logs: list[str] = []

                def log(msg: str):
                    logs.append(msg)
                    # 中间过程：仅更新日志，不更新CSV
                    return (None, "\n".join(logs))

                if not comic_dir or not os.path.isdir(comic_dir):
                    yield ("", "错误：目录不存在或为空")
                    return

                archives = list_archives(comic_dir)
                archives = _sort_archives(archives, sort_mode)
                _edit_state["archives"] = archives
                yield log(f"发现压缩包：{len(archives)} 个，排序：{sort_mode}")

                output = io.StringIO()
                writer = csv.writer(output)
                if include_header:
                    writer.writerow(_csv_headers)

                for i, ap in enumerate(archives, start=1):
                    base_name = os.path.basename(ap)
                    try:
                        xml_bytes = _read_xml_from_archive(ap)
                        if xml_bytes is None:
                            base = os.path.splitext(base_name)[0]
                            series = os.path.basename(os.path.dirname(ap)) if os.path.dirname(ap) else ""
                            writer.writerow([base, series, "", "", "", "", "", "", ""]) 
                            yield log(f"[{i}/{len(archives)}] 无 ComicInfo.xml -> 预填 Title='{base}', Series='{series}'")
                        else:
                            fields = _parse_xml_fields(xml_bytes)
                            writer.writerow([
                                fields.get("Title", ""),
                                fields.get("Series", ""),
                                fields.get("Number", ""),
                                fields.get("Summary", ""),
                                fields.get("Writer", ""),
                                fields.get("Genre", ""),
                                fields.get("Web", ""),
                                fields.get("PublishingStatusTachiyomi", ""),
                                fields.get("SourceMihon", ""),
                            ])
                            yield log(f"[{i}/{len(archives)}] 读取 ComicInfo.xml 成功 -> {base_name}")
                    except Exception as e:
                        # 出错也写入空行保持行数一致
                        writer.writerow(["" for _ in range(9)])
                        yield log(f"[{i}/{len(archives)}] 读取失败 -> {base_name}: {e}")

                # 最终产出CSV文本与最终日志
                yield (output.getvalue(), "\n".join(logs))

            def _strip_optional_header(rows: list[list[str]], include_header: bool):
                if not rows:
                    return rows
                if include_header and rows and [c.strip() for c in rows[0]] == _csv_headers:
                    return rows[1:]
                return rows

            def _prune_trailing_empty_rows(rows: list[list[str]]):
                # 去掉尾部全空行，避免因多余换行导致行数偏差
                pruned = list(rows)
                while pruned and all((c is None or str(c).strip() == "") for c in pruned[-1]):
                    pruned.pop()
                return pruned

            def save_archives(csv_text: str, include_header: bool, strict_order: bool):
                # 流式输出日志：逐个压缩包写入并产生日志
                import io, csv
                logs: list[str] = []

                def log(msg: str):
                    logs.append(msg)
                    return "\n".join(logs)

                if not csv_text:
                    yield log("无可保存的内容")
                    return
                if not _edit_state["archives"]:
                    yield log("请先扫描目录以建立压缩包顺序")
                    return

                reader = csv.reader(io.StringIO(csv_text))
                rows = list(reader)
                rows = _strip_optional_header(rows, include_header)
                rows = _prune_trailing_empty_rows(rows)
                if len(rows) != len(_edit_state["archives"]):
                    yield log(f"行数不匹配：CSV {len(rows)} 行，压缩包 {len(_edit_state['archives'])} 个。已取消保存。")
                    return

                total = len(_edit_state["archives"])
                if strict_order:
                    # 用 Title/Series 做顺序一致性快速校验（仅在CSV提供任一字段时校验）
                    for idx, ap in enumerate(_edit_state["archives"]):
                        row = rows[idx] if idx < len(rows) else []
                        title_csv = (row[0] if len(row) > 0 else "").strip()
                        series_csv = (row[1] if len(row) > 1 else "").strip()
                        if not title_csv and not series_csv:
                            continue
                        xml_bytes = _read_xml_from_archive(ap)
                        if xml_bytes is None:
                            base = os.path.splitext(os.path.basename(ap))[0]
                            series = os.path.basename(os.path.dirname(ap)) if os.path.dirname(ap) else ""
                            title_ref, series_ref = base.strip(), series.strip()
                        else:
                            fields_ref = _parse_xml_fields(xml_bytes)
                            title_ref = (fields_ref.get("Title", "") or "").strip()
                            series_ref = (fields_ref.get("Series", "") or "").strip()
                            if not title_ref and not series_ref:
                                base = os.path.splitext(os.path.basename(ap))[0]
                                series = os.path.basename(os.path.dirname(ap)) if os.path.dirname(ap) else ""
                                title_ref, series_ref = base.strip(), series.strip()
                        if (title_ref and title_csv and title_ref != title_csv) or (series_ref and series_csv and series_ref != series_csv):
                            yield log(f"顺序校验失败于第 {idx+1} 行：文件={os.path.basename(ap)}, 参考(Title='{title_ref}', Series='{series_ref}'), CSV(Title='{title_csv}', Series='{series_csv}')。已取消保存。")
                            return
                for idx, ap in enumerate(_edit_state["archives"]):
                    name = os.path.basename(ap)
                    try:
                        row = rows[idx]
                        if len(row) < 9:
                            row = row + [""] * (9 - len(row))
                        fields = {
                            "Title": row[0],
                            "Series": row[1],
                            "Number": row[2],
                            "Summary": row[3],
                            "Writer": row[4],
                            "Genre": row[5],
                            "Web": row[6],
                            "PublishingStatusTachiyomi": row[7],
                            "SourceMihon": row[8],
                        }
                        xml_bytes = _build_xml_from_fields(fields)
                        ok = _write_xml_to_archive(ap, xml_bytes)
                        if ok:
                            yield log(f"[{idx+1}/{total}] 已保存: {name}")
                        else:
                            yield log(f"[{idx+1}/{total}] 失败: {name}")
                    except Exception as e:
                        yield log(f"[{idx+1}/{total}] 异常: {name} -> {e}")
                # 结束
                yield log("保存完成")

            def export_csv(csv_text: str, include_header: bool):
                # 若需要表头且未包含，则自动添加表头；导出返回临时文件路径
                import io, csv, tempfile
                rows = list(csv.reader(io.StringIO(csv_text or "")))
                if include_header:
                    if not rows or [c.strip() for c in rows[0]] != _csv_headers:
                        output = io.StringIO()
                        writer = csv.writer(output)
                        writer.writerow(_csv_headers)
                        for r in rows:
                            writer.writerow(r)
                        data = output.getvalue().encode("utf-8")
                    else:
                        data = (csv_text or "").encode("utf-8")
                else:
                    data = (csv_text or "").encode("utf-8")
                fd, tmp_path = tempfile.mkstemp(suffix=".csv", prefix="export_comicinfo_")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                return tmp_path

            def import_csv(file_obj, include_header: bool):
                try:
                    if file_obj is None:
                        return ""
                    # gr.File 传入的是一个类似 {name, size, data, ...} 的对象，需读取其临时路径或数据
                    # gradio 通常提供 .name 或 .orig_name；这里使用 .name 作为路径
                    path = getattr(file_obj, "name", None) or getattr(file_obj, "orig_name", None)
                    if path and os.path.exists(path):
                        with open(path, "r", encoding="utf-8") as f:
                            content = f.read()
                    else:
                        # 回退：某些版本以字节形式提供 data
                        data = getattr(file_obj, "data", None)
                        if data is None:
                            return ""
                        try:
                            content = data.decode("utf-8")
                        except Exception:
                            content = data.decode("utf-8", errors="ignore")
                    # 如果不包含表头且文件带表头，则去掉首行
                    import io, csv
                    rows = list(csv.reader(io.StringIO(content)))
                    if not include_header and rows and [c.strip() for c in rows[0]] == _csv_headers:
                        output = io.StringIO()
                        writer = csv.writer(output)
                        for r in rows[1:]:
                            writer.writerow(r)
                        return output.getvalue()
                    return content
                except Exception:
                    return ""

            scan_btn.click(fn=scan_archives, inputs=[edit_dir_tb, include_header_cb, sort_dd], outputs=[csv_tb, scan_logs])
            save_btn.click(fn=save_archives, inputs=[csv_tb, include_header_cb, strict_order_cb], outputs=save_logs)
            export_btn.click(fn=export_csv, inputs=[csv_tb, include_header_cb], outputs=export_btn)
            import_file.upload(fn=import_csv, inputs=[import_file, include_header_cb], outputs=csv_tb)


if __name__ == "__main__":
    import os
    port = int(os.environ.get("GRADIO_SERVER_PORT", "7861") or 7861)
    demo.launch(server_name="0.0.0.0", server_port=port)


