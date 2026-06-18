"""
Tests for update_archives_with_xml.py
"""
import os
import sys
import zipfile

import pytest
from lxml import etree

from update_archives_with_xml import (
    best_match,
    classify_unit,
    discover_xmls,
    extract_chapter_index,
    fuzzy_ratio,
    list_archives,
    main,
    normalize_text,
    read_xml_title,
    update_archive_with_xml,
)

# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

class TestNormalizeText:
    def test_lowercase_and_strip_symbols(self):
        result = normalize_text("Hello-World_Chapter 01!")
        assert result == "helloworldchapter01"
        assert " " not in result
        assert "-" not in result

    def test_handles_whitespace(self):
        result = normalize_text("  Hello   World  ")
        assert result == "helloworld"

    def test_handles_chinese(self):
        result = normalize_text("第01話 Hello")
        assert "第" in result
        assert "01" in result
        assert "hello" in result


# ---------------------------------------------------------------------------
# fuzzy_ratio
# ---------------------------------------------------------------------------

class TestFuzzyRatio:
    def test_identical_strings(self):
        assert fuzzy_ratio("Hello World", "Hello World") == 1.0

    def test_normalized_identical(self):
        assert fuzzy_ratio("Hello-World!", "Hello World") == 1.0

    def test_completely_different(self):
        assert fuzzy_ratio("ABC", "XYZ") < 0.5


# ---------------------------------------------------------------------------
# classify_unit
# ---------------------------------------------------------------------------

class TestClassifyUnit:
    def test_volume(self):
        assert classify_unit("第1卷") == "volume"

    def test_chapter(self):
        assert classify_unit("第01話") == "chapter"
        assert classify_unit("第01话") == "chapter"
        assert classify_unit("第01回") == "chapter"

    def test_unknown(self):
        assert classify_unit("Some Title") is None


# ---------------------------------------------------------------------------
# extract_chapter_index
# ---------------------------------------------------------------------------

class TestExtractChapterIndex:
    def test_simple_number(self):
        result = extract_chapter_index("第093話")
        assert result == (93, None)

    def test_with_subchapter(self):
        result = extract_chapter_index("第093.2話")
        assert result == (93, 2)

    def test_prefixed_number(self):
        result = extract_chapter_index("连载第093話")
        assert result == (93, None)

    def test_underscore_subchapter(self):
        result = extract_chapter_index("连载第093_2話_24p")
        assert result == (93, 2)

    def test_numeric_only(self):
        result = extract_chapter_index("093")
        assert result == (93, None)

    def test_dash_subchapter(self):
        result = extract_chapter_index("093-2")
        assert result == (93, 2)

    def test_no_match(self):
        assert extract_chapter_index("Some Text") is None

    def test_pages_stripped(self):
        result = extract_chapter_index("第093話_24p")
        assert result == (93, None)

    def test_long_number_no_match(self):
        result = extract_chapter_index("12345")
        assert result is None


# ---------------------------------------------------------------------------
# read_xml_title
# ---------------------------------------------------------------------------

class TestReadXmlTitle:
    def test_reads_title(self, tmp_path):
        xml_path = str(tmp_path / "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "MyTitle"
        tree = etree.ElementTree(root)
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))
        assert read_xml_title(xml_path) == "MyTitle"

    def test_returns_none_when_no_title(self, tmp_path):
        xml_path = str(tmp_path / "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        tree = etree.ElementTree(root)
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(tree, xml_declaration=True, encoding="UTF-8"))
        assert read_xml_title(xml_path) is None

    def test_returns_none_on_bad_xml(self, tmp_path):
        xml_path = str(tmp_path / "bad.xml")
        with open(xml_path, "w") as f:
            f.write("not xml")
        assert read_xml_title(xml_path) is None


# ---------------------------------------------------------------------------
# discover_xmls
# ---------------------------------------------------------------------------

class TestDiscoverXmls:
    def test_finds_xml_in_chapter_dir(self, tmp_path):
        chapter = tmp_path / "ch01"
        chapter.mkdir()
        xml_path = chapter / "ComicInfo.xml"
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "ChapterTitle"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))
        items = discover_xmls(str(tmp_path))
        assert len(items) == 1
        assert items[0][0] == "ChapterTitle"
        assert items[0][2] == "ch01"

    def test_finds_xml_in_xml_subdir(self, tmp_path):
        chapter = tmp_path / "ch01"
        xml_sub = chapter / "xml"
        xml_sub.mkdir(parents=True)
        xml_path = xml_sub / "ComicInfo.xml"
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "SubTitle"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))
        items = discover_xmls(str(tmp_path))
        assert len(items) == 1
        assert items[0][0] == "SubTitle"

    def test_empty_dir(self, tmp_path):
        assert discover_xmls(str(tmp_path)) == []

    def test_nonexistent_root(self):
        assert discover_xmls("/nonexistent") == []


# ---------------------------------------------------------------------------
# list_archives
# ---------------------------------------------------------------------------

class TestListArchives:
    def test_finds_cbz_and_zip(self, tmp_path):
        for name in ["a.cbz", "b.zip", "c.txt"]:
            (tmp_path / name).write_text("data")
        result = list_archives(str(tmp_path))
        assert len(result) == 2
        names = [os.path.basename(p) for p in result]
        assert "a.cbz" in names
        assert "b.zip" in names
        assert "c.txt" not in names

    def test_empty_dir(self, tmp_path):
        assert list_archives(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# best_match
# ---------------------------------------------------------------------------

class TestBestMatch:
    def test_exact_normalized_match(self, tmp_path):
        for name in ["ch01.cbz", "ch02.cbz"]:
            (tmp_path / name).write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("ch01", archives)
        assert score >= 0.99
        assert "ch01" in os.path.basename(path)

    def test_chapter_index_match(self, tmp_path):
        for name in ["第093話.cbz", "第094話.cbz"]:
            (tmp_path / name).write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("第093話", archives)
        assert score >= 0.9
        assert "093" in os.path.basename(path)

    def test_fuzzy_match(self, tmp_path):
        for name in ["chapter_one.cbz", "chapter_two.cbz"]:
            (tmp_path / name).write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("chapter 1", archives)
        assert score > 0
        assert path is not None

    def test_no_match(self, tmp_path):
        for name in ["aaa.cbz", "bbb.cbz"]:
            (tmp_path / name).write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("zzz", archives)
        # will pick best of poor fuzzy matches, but score should be low
        assert score < 0.5

    def test_volume_chapter_mismatch(self, tmp_path):
        for name in ["第1卷.cbz", "第2卷.cbz"]:
            (tmp_path / name).write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("第01話", archives)
        assert score == 0.0

    def test_japanese_variant_volume(self, tmp_path):
        for name in ["第1巻.cbz", "第2巻.cbz"]:
            (tmp_path / name).write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("第01話", archives)
        assert score == 0.0


# ---------------------------------------------------------------------------
# update_archive_with_xml
# ---------------------------------------------------------------------------

def _make_comic_xml(title: str) -> bytes:
    root = etree.Element("ComicInfo")
    etree.SubElement(root, "Title").text = title
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


class TestUpdateArchiveWithXml:
    def test_writes_xml(self, tmp_path):
        archive = str(tmp_path / "test.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")
        xml_path = str(tmp_path / "ComicInfo.xml")
        with open(xml_path, "wb") as f:
            f.write(_make_comic_xml("NewTitle"))
        assert update_archive_with_xml(archive, xml_path, dry_run=False)
        with zipfile.ZipFile(archive, "r") as zf:
            content = zf.read("ComicInfo.xml")
            assert b"NewTitle" in content

    def test_dry_run_does_not_write(self, tmp_path):
        archive = str(tmp_path / "test.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")
        xml_path = str(tmp_path / "ComicInfo.xml")
        with open(xml_path, "wb") as f:
            f.write(_make_comic_xml("DryRun"))
        assert update_archive_with_xml(archive, xml_path, dry_run=True)
        with zipfile.ZipFile(archive, "r") as zf:
            assert "ComicInfo.xml" not in zf.namelist()

    def test_force_overwrites_existing(self, tmp_path):
        archive = str(tmp_path / "test.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ComicInfo.xml", _make_comic_xml("OldTitle"))
            zf.writestr("page.jpg", b"data")
        xml_path = str(tmp_path / "ComicInfo.xml")
        with open(xml_path, "wb") as f:
            f.write(_make_comic_xml("NewTitle"))
        assert update_archive_with_xml(archive, xml_path, dry_run=False, force=True)
        with zipfile.ZipFile(archive, "r") as zf:
            content = zf.read("ComicInfo.xml")
            assert b"NewTitle" in content

    def test_skips_when_exists_and_not_force(self, tmp_path):
        archive = str(tmp_path / "test.cbz")
        existing = _make_comic_xml("Existing")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ComicInfo.xml", existing)
            zf.writestr("page.jpg", b"data")
        xml_path = str(tmp_path / "ComicInfo.xml")
        with open(xml_path, "wb") as f:
            f.write(_make_comic_xml("NewTitle"))
        assert update_archive_with_xml(archive, xml_path, dry_run=False, force=False)
        with zipfile.ZipFile(archive, "r") as zf:
            content = zf.read("ComicInfo.xml")
            assert b"Existing" in content
            assert b"NewTitle" not in content

    def test_returns_false_on_bad_archive(self, tmp_path):
        archive = str(tmp_path / "bad.cbz")
        with open(archive, "w") as f:
            f.write("not a zip")
        xml_path = str(tmp_path / "ComicInfo.xml")
        with open(xml_path, "wb") as f:
            f.write(_make_comic_xml("Title"))
        assert not update_archive_with_xml(archive, xml_path)


# ---------------------------------------------------------------------------
# read_xml_title additional coverage
# ---------------------------------------------------------------------------

class TestReadXmlTitleExtra:
    def test_empty_title_returns_none(self, tmp_path):
        xml_path = str(tmp_path / "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "   "
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))
        assert read_xml_title(xml_path) is None


# ---------------------------------------------------------------------------
# discover_xmls additional coverage
# ---------------------------------------------------------------------------

class TestDiscoverXmlsExtra:
    def test_skips_non_directories(self, tmp_path):
        (tmp_path / "afile.txt").write_text("data")
        chapter = tmp_path / "ch01"
        chapter.mkdir()
        xml_path = chapter / "ComicInfo.xml"
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "T"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))
        items = discover_xmls(str(tmp_path))
        assert len(items) == 1


# ---------------------------------------------------------------------------
# best_match additional coverage
# ---------------------------------------------------------------------------

class TestBestMatchExtra:
    def test_exact_name_match_gets_full_score(self, tmp_path):
        (tmp_path / "Chapter 01.cbz").write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("Chapter 01", archives)
        assert score == 1.0

    def test_exact_name_match_no_query_index(self, tmp_path):
        (tmp_path / "Chapter 01.cbz").write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("Chapter 01", archives)
        assert score >= 0.99

    def test_different_main_chapter_mismatch(self, tmp_path):
        (tmp_path / "ch02.cbz").write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("ch01", archives)
        assert "ch01" not in os.path.basename(path or "")

    def test_different_sub_chapter_zero_score(self, tmp_path):
        (tmp_path / "ch01.2.cbz").write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("ch01.1", archives)
        assert score == 0.0

    def test_no_query_index_falls_to_fuzzy(self, tmp_path):
        (tmp_path / "hello_world.cbz").write_text("data")
        archives = list_archives(str(tmp_path))
        path, score = best_match("hello world", archives)
        assert score > 0


# ---------------------------------------------------------------------------
# update_archive_with_xml edge cases
# ---------------------------------------------------------------------------

class TestUpdateArchiveWithXmlExtra:
    def test_temp_cleanup_error(self, tmp_path, monkeypatch):
        archive = str(tmp_path / "test.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")
        xml_path = str(tmp_path / "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "T"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))

        original_exists = os.path.exists

        def always_exists(path):
            if ".zip" in path:
                return True
            return original_exists(path)

        monkeypatch.setattr(os.path, "exists", always_exists)
        result = update_archive_with_xml(archive, xml_path, dry_run=False, force=False)
        assert result is True


# ---------------------------------------------------------------------------
# main() function
# ---------------------------------------------------------------------------

class TestMain:
    def test_missing_comic_dir(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["prog", "/nonexistent_comic", "/tmp"])
        with pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "错误" in captured.out

    def test_missing_xml_root(self, capsys, monkeypatch, tmp_path):
        comic_dir = str(tmp_path)
        monkeypatch.setattr(sys, "argv", ["prog", comic_dir, "/nonexistent_xml"])
        with pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "错误" in captured.out

    def test_no_xml_files(self, capsys, monkeypatch, tmp_path):
        xml_root = str(tmp_path / "xml")
        os.makedirs(xml_root, exist_ok=True)
        monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path), xml_root])
        with pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "未发现任何 XML" in captured.out

    def test_no_archives(self, capsys, monkeypatch, tmp_path):
        xml_root = str(tmp_path / "xml")
        os.makedirs(xml_root)
        ch = os.path.join(xml_root, "ch01")
        os.makedirs(ch)
        xml_path = os.path.join(ch, "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "ChapterTitle"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))

        comic_dir = tmp_path
        monkeypatch.setattr(sys, "argv", ["prog", str(comic_dir), xml_root])
        with pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "未发现任何章节压缩包" in captured.out

    def test_dry_run_success(self, capsys, monkeypatch, tmp_path):
        xml_root = str(tmp_path / "xml")
        os.makedirs(xml_root)
        ch = os.path.join(xml_root, "ch01")
        os.makedirs(ch)
        xml_path = os.path.join(ch, "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "ChapterTitle"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))

        comic_dir = str(tmp_path / "comic")
        os.makedirs(comic_dir)
        archive = os.path.join(comic_dir, "ch01.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")

        monkeypatch.setattr(sys, "argv", ["prog", comic_dir, xml_root, "--dry-run", "--verbose"])
        main()
        captured = capsys.readouterr()
        assert "匹配成功" in captured.out
        assert "处理完成" in captured.out

    def test_strategy_title_only(self, capsys, monkeypatch, tmp_path):
        xml_root = str(tmp_path / "xml")
        os.makedirs(xml_root)
        ch = os.path.join(xml_root, "ch01")
        os.makedirs(ch)
        xml_path = os.path.join(ch, "ComicInfo.xml")
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "ch01"
        with open(xml_path, "wb") as f:
            f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))

        comic_dir = str(tmp_path / "comic")
        os.makedirs(comic_dir)
        archive = os.path.join(comic_dir, "ch01.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")

        monkeypatch.setattr(sys, "argv", ["prog", comic_dir, xml_root, "--strategy", "title", "--dry-run"])
        main()
        captured = capsys.readouterr()
        assert "处理完成" in captured.out

    def test_archive_already_used(self, capsys, monkeypatch, tmp_path):
        xml_root = str(tmp_path / "xml")
        os.makedirs(xml_root)
        for folder in ["ch01", "ch02"]:
            ch = os.path.join(xml_root, folder)
            os.makedirs(ch)
            xml_path = os.path.join(ch, "ComicInfo.xml")
            root = etree.Element("ComicInfo")
            etree.SubElement(root, "Title").text = f"Title-{folder}"
            with open(xml_path, "wb") as f:
                f.write(etree.tostring(etree.ElementTree(root), xml_declaration=True, encoding="UTF-8"))

        comic_dir = str(tmp_path / "comic")
        os.makedirs(comic_dir)
        archive = os.path.join(comic_dir, "ch01.cbz")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")

        args = ["prog", comic_dir, xml_root, "--strategy", "folder", "--dry-run", "--verbose"]
        monkeypatch.setattr(sys, "argv", args)
        main()
        captured = capsys.readouterr()
        assert "处理完成" in captured.out
