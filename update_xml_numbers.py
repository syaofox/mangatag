import argparse
import os
import re
import sys
from typing import Optional

from lxml import etree


def parse_prefix_number(folder_name: str) -> Optional[str]:
    """
    从章节文件夹名中解析序号前缀。
    兼容形如: "001-第01卷", "12_特典", "3 第3话" 等格式。
    返回零填充后的字符串（保持原有前导零）。
    """
    # 优先匹配以数字开头，后面跟分隔符或空白
    match = re.match(r"^(\d+)(?:[\-_\s].*)?$", folder_name)
    if match:
        return match.group(1)

    # 退化匹配：抓取开头连续数字
    match = re.match(r"^(\d+)", folder_name)
    if match:
        return match.group(1)

    return None


def find_comicinfo_xml(chapter_dir: str) -> Optional[str]:
    """
    在章节目录下查找 ComicInfo.xml。
    兼容两种结构：
    - outputs/漫画名/章节目录/ComicInfo.xml
    - outputs/漫画名/章节目录/xml/ComicInfo.xml
    """
    direct_path = os.path.join(chapter_dir, "ComicInfo.xml")
    if os.path.isfile(direct_path):
        return direct_path

    xml_sub_path = os.path.join(chapter_dir, "xml", "ComicInfo.xml")
    if os.path.isfile(xml_sub_path):
        return xml_sub_path

    return None


def update_number_in_xml(xml_path: str, new_number: str, dry_run: bool = False) -> bool:
    """
    将 XML 文件中的 <Number> 值更新为 new_number。
    返回是否更新成功（或在 dry-run 下返回 True 表示将会更新）。
    """
    try:
        parser = etree.XMLParser(remove_blank_text=True)
        tree = etree.parse(xml_path, parser)
        root = tree.getroot()

        number_elem = root.find("Number")
        if number_elem is None:
            # 若不存在则创建
            number_elem = etree.SubElement(root, "Number")

        old = number_elem.text or ""
        if old == new_number:
            return True

        number_elem.text = new_number
        if not dry_run:
            tree.write(xml_path, pretty_print=True, xml_declaration=True, encoding="UTF-8")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"更新失败: {xml_path}: {exc}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="根据章节文件夹名的序号前缀，更新 ComicInfo.xml 的 Number 字段")
    parser.add_argument(
        "manga_dir",
        help="漫画目录路径，例如: outputs/終末的後宮 玄幻版學園",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="试运行：只显示将要更新的内容，不写回文件",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="显示详细处理日志",
    )

    args = parser.parse_args()

    manga_dir = args.manga_dir
    if not os.path.isdir(manga_dir):
        print(f"错误：目录不存在 -> {manga_dir}")
        sys.exit(1)

    # 仅遍历直接子目录（每个子目录代表一个章节）
    chapter_dirs = [
        os.path.join(manga_dir, name)
        for name in os.listdir(manga_dir)
        if os.path.isdir(os.path.join(manga_dir, name))
    ]

    # 按文件夹名的数字前缀排序（若无前缀则排在后面）
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
            if args.verbose:
                print(f"跳过（无数字前缀）：{folder_name}")
            continue

        xml_path = find_comicinfo_xml(chapter_dir)
        if xml_path is None:
            if args.verbose:
                print(f"未找到 ComicInfo.xml：{chapter_dir}")
            continue

        total += 1

        if args.verbose or args.dry_run:
            print(f"将更新 Number -> {prefix}: {xml_path}")

        if update_number_in_xml(xml_path, prefix, dry_run=args.dry_run):
            updated += 1

    print(f"处理完成：目标章节 {total}，成功更新 {updated}，dry-run={args.dry_run}")


if __name__ == "__main__":
    main()


