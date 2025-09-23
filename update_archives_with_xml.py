import argparse
import os
import re
import sys
import tempfile
import zipfile
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

from lxml import etree


def normalize_text(text: str) -> str:
    """
    规范化用于匹配的字符串：小写、去空白、去常见符号。
    """
    lowered = text.lower()
    # 去除常见分隔符与标点（保留数字和字母及汉字）
    cleaned = re.sub(r"[\s\-_\[\]（）()【】{}:：~·•.,，。!！?？'" "`·]+", "", lowered)
    return cleaned


def fuzzy_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def classify_unit(text: str) -> Optional[str]:
    """
    粗分类单位：卷 或 回/话。
    - 返回 'volume' 表示卷（包含“卷”）
    - 返回 'chapter' 表示章节（包含“回”、“話”、“话”）
    - 无法判断返回 None
    """
    if re.search(r"卷", text):
        return "volume"
    if re.search(r"[回話话]", text):
        return "chapter"
    return None


def extract_chapter_index(text: str) -> Optional[Tuple[int, Optional[int]]]:
    """
    从文本中提取章节编号 (main, sub)。
    兼容示例：
    - "第093.2話" -> (93, 2)
    - "连载第093_2話_24p" -> (93, 2)
    - "第093話" / "连载第093話" -> (93, None)
    - "093-2" / "093.2" / "093_2" -> (93, 2)

    返回 None 表示无法可靠解析。
    """
    # 去除容易干扰的页数字样式，例如 "_24p"、" 24P"
    cleaned = re.sub(r"[\-_\s]\d{1,4}p\b", " ", text, flags=re.IGNORECASE)

    # 常见形式：带話/话尾缀，且存在子章节
    patterns = [
        # 连载第093.2話 / 第093_2话 / 093-2話
        r"[第连載连载]?\s*(\d{1,4})[\._\-＿\s]+(\d{1,2})\s*[話话]",
        # 纯数字子章节：093.2 / 093_2 / 093-2（避免后续紧跟数字）
        r"[第连載连载]?\s*(\d{1,4})[\._\-＿\s]+(\d{1,2})(?!\d)",
        # 仅主章节：第093話 / 连载第093话 / 093話
        r"[第连載连载]?\s*(\d{1,4})\s*[話话]",
        # 仅主章节：开头即数字（避免把年份等长串误判，这里限制到 4 位）
        r"^\D*?(\d{1,4})(?!\d)",
    ]

    for idx, pat in enumerate(patterns):
        m = re.search(pat, cleaned)
        if m:
            main = int(m.group(1))
            sub: Optional[int] = None
            if len(m.groups()) >= 2 and m.group(2):
                try:
                    sub = int(m.group(2))
                except ValueError:
                    sub = None
            return (main, sub)
    return None


def read_xml_title(xml_path: str) -> Optional[str]:
    try:
        tree = etree.parse(xml_path)
        root = tree.getroot()
        title_elem = root.find("Title")
        if title_elem is not None and (title := (title_elem.text or "").strip()):
            return title
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"读取 XML 失败: {xml_path}: {exc}")
        return None


def discover_xmls(xml_root: str) -> List[Tuple[str, str, str]]:
    """
    返回 (title, xml_path, chapter_folder_name) 列表。
    兼容目录结构：
    - xml_root/章节目录/ComicInfo.xml
    - xml_root/章节目录/xml/ComicInfo.xml
    """
    items: List[Tuple[str, str, str]] = []
    if not os.path.isdir(xml_root):
        print(f"错误：XML 目录不存在 -> {xml_root}")
        return items

    for chapter_name in os.listdir(xml_root):
        chapter_dir = os.path.join(xml_root, chapter_name)
        if not os.path.isdir(chapter_dir):
            continue
        # 两种可能路径
        candidates = [
            os.path.join(chapter_dir, "ComicInfo.xml"),
            os.path.join(chapter_dir, "xml", "ComicInfo.xml"),
        ]
        for xml_path in candidates:
            if os.path.isfile(xml_path):
                title = read_xml_title(xml_path)
                if title:
                    items.append((title, xml_path, chapter_name))
                break
    return items


def list_archives(comic_dir: str) -> List[str]:
    exts = {".cbz", ".zip"}
    return [
        os.path.join(comic_dir, f)
        for f in os.listdir(comic_dir)
        if os.path.isfile(os.path.join(comic_dir, f))
        and os.path.splitext(f)[1].lower() in exts
    ]


def best_match(query: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    """
    先尝试基于章节索引匹配（精确优先），否则回退到模糊匹配。
    分数范围 0-1：
      - 索引完全相同：0.99
      - 主章节相同但子章节不同或缺失：0.75
      - 其余：使用模糊相似度
    """
    query_idx = extract_chapter_index(query)
    query_unit = classify_unit(query)

    best_path: Optional[str] = None
    best_score: float = 0.0

    # 按文件名排序遍历候选，确保在分数相同的情况下优先选择文件名靠前的压缩包（确定性行为）
    for path in sorted(candidates, key=lambda p: os.path.basename(p)):
        fname = os.path.basename(path)
        name_wo_ext, _ = os.path.splitext(fname)

        score = 0.0
        # 优先：规范化后完全相等的名字最优先
        try:
            if normalize_text(query) == normalize_text(name_wo_ext):
                score = 1.0
            else:
                score = 0.0
        except Exception:
            score = 0.0

        if query_idx is not None:
            cand_idx = extract_chapter_index(name_wo_ext)
            if cand_idx is not None:
                # 完全一致（主+子章节）
                if cand_idx == query_idx:
                    # 章节索引完全匹配，但若已被完全名称匹配(score 1.0)，则保留更高分
                    if score < 0.99:
                        score = 0.99
                else:
                    if cand_idx[0] == query_idx[0]:
                        # 主章节相同，但子章节不同或一方缺失：直接不匹配
                        # 例如：104 vs 104.2 或 104.1 vs 104.2
                        score = 0.0
                    else:
                        # 主章节号不同：禁止匹配
                        score = 0.0
            else:
                # 仅在非完全名称匹配时使用模糊匹配
                if score < 1.0:
                    score = fuzzy_ratio(query, name_wo_ext)
        else:
            # 仅在非完全名称匹配时使用模糊匹配
            if score < 1.0:
                score = fuzzy_ratio(query, name_wo_ext)

        # 单位强约束：卷/回(話)必须一致
        cand_unit = classify_unit(name_wo_ext)
        if query_unit and cand_unit and query_unit != cand_unit:
            score = 0.0

        if score > best_score:
            best_score = score
            best_path = path

    return best_path, best_score


def update_archive_with_xml(
    archive_path: str, xml_path: str, dry_run: bool = False, force: bool = False
) -> bool:
    """
    将 xml_path 写入 zip/cbz 的根目录为 ComicInfo.xml。
    若已有 ComicInfo.xml：
      - force=True 覆盖
      - force=False 跳过
    通过创建临时 zip 再替换的方式实现安全更新。
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            has_existing = any(
                info.filename.lower() == "comicinfo.xml" for info in zf.infolist()
            )
            if has_existing and not force and not dry_run:
                # 不覆盖则直接视为成功
                return True

            if dry_run:
                return True

            # 写临时 zip
            dir_name = os.path.dirname(archive_path)
            fd, tmp_path = tempfile.mkstemp(
                suffix=".zip", prefix="tmp_update_", dir=dir_name
            )
            os.close(fd)

            try:
                with zipfile.ZipFile(
                    tmp_path, "w", compression=zipfile.ZIP_DEFLATED
                ) as zfw:
                    # 复制原文件（排除 ComicInfo.xml 若要覆盖）
                    for info in zf.infolist():
                        if info.filename.lower() == "comicinfo.xml":
                            if force:
                                continue
                        data = zf.read(info.filename)
                        zfw.writestr(info, data)

                    # 写入/覆盖 ComicInfo.xml
                    with open(xml_path, "rb") as xf:
                        xml_bytes = xf.read()
                    zfw.writestr("ComicInfo.xml", xml_bytes)

                # 替换原文件
                os.replace(tmp_path, archive_path)
                return True
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
    except Exception as exc:  # noqa: BLE001
        print(f"更新压缩包失败: {archive_path}: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="使用 XML 的 Title/章节文件夹名 模糊匹配章节压缩包（.cbz/.zip），写入(或覆盖) ComicInfo.xml"
    )
    parser.add_argument(
        "comic_dir",
        help="章节压缩包所在目录，例如：/home/user/comic/连载中/[作者]书名[manhuagui]",
    )
    parser.add_argument("xml_root", help="XML 输出根目录，例如：outputs/天漫浮世錄")
    parser.add_argument(
        "--threshold", type=float, default=0.60, help="匹配阈值，范围 0-1，默认 0.60"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="试运行，仅显示计划，无写入"
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")
    parser.add_argument(
        "--force",
        action="store_true",
        help="若压缩包内已存在 ComicInfo.xml，是否强制覆盖",
    )
    parser.add_argument(
        "--strategy",
        choices=["title", "folder", "both"],
        default="both",
        help="匹配策略：使用标题(title)、章节文件夹名(folder)或两者择优(both)。默认 both",
    )

    args = parser.parse_args()

    comic_dir = args.comic_dir
    xml_root = args.xml_root

    if not os.path.isdir(comic_dir):
        print(f"错误：章节目录不存在 -> {comic_dir}")
        sys.exit(1)
    if not os.path.isdir(xml_root):
        print(f"错误：XML 目录不存在 -> {xml_root}")
        sys.exit(1)

    xml_items = discover_xmls(xml_root)
    if not xml_items:
        print("未发现任何 XML（ComicInfo.xml）。")
        sys.exit(1)

    archives = list_archives(comic_dir)
    if not archives:
        print("未发现任何章节压缩包（.cbz/.zip）。")
        sys.exit(1)

    if args.verbose:
        print(f"发现 XML 数量：{len(xml_items)}；压缩包数量：{len(archives)}")

    success = 0
    total = 0
    used_archives: set[str] = set()

    for title, xml_path, chapter_folder in xml_items:
        # 计算不同策略下的最佳匹配
        chosen_path: Optional[str] = None
        chosen_score: float = 0.0
        chosen_basis: str = ""

        if args.strategy in ("title", "both"):
            p, s = best_match(title, archives)
            if s > chosen_score:
                chosen_path, chosen_score, chosen_basis = p, s, "title"

        if args.strategy in ("folder", "both"):
            p2, s2 = best_match(chapter_folder, archives)
            if s2 > chosen_score:
                chosen_path, chosen_score, chosen_basis = p2, s2, "folder"

        if chosen_path is None or chosen_score < args.threshold:
            if args.verbose:
                print(
                    f"跳过：无匹配或分数过低（{chosen_score:.2f}） -> Title='{title}', Folder='{chapter_folder}'"
                )
            continue

        total += 1
        # 保证一一对应：同一压缩包只允许被一个 XML 使用
        if chosen_path in used_archives:
            if args.verbose:
                print(
                    f"跳过：目标压缩包已被占用 -> {os.path.basename(chosen_path)} | Title='{title}', Folder='{chapter_folder}'"
                )
            continue
        if args.verbose or args.dry_run:
            basis_desc = "标题" if chosen_basis == "title" else "章节文件夹名"
            print(
                f"匹配成功（{chosen_score:.2f}, 基于{basis_desc}）：'{title}' | '{chapter_folder}' -> {os.path.basename(chosen_path)}"
            )

        if update_archive_with_xml(
            chosen_path, xml_path, dry_run=args.dry_run, force=args.force
        ):
            success += 1
            used_archives.add(chosen_path)

    print(
        f"处理完成：发现{len(xml_items)}个XML，匹配目标 {total}，成功更新 {success}，dry-run={args.dry_run}, 阈值={args.threshold:.2f}"
    )


if __name__ == "__main__":
    main()
