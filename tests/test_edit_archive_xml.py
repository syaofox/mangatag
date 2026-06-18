import os
import zipfile

from lxml import etree

from edit_archive_xml import (
    ALL_MARK,
    CSV_HEADERS,
    XML_FIELD_TAGS,
    _fields_equal,
    _replace_placeholders,
    _sanitize_filename,
    _sort_by_number_field,
    batch_find_replace,
    batch_prefix,
    batch_set,
    batch_suffix,
    build_xml_from_fields,
    export_csv,
    extract_headers,
    import_csv_content,
    list_dirs_with_archives,
    parse_xml_fields,
    preview_rename_by_rule,
    prune_trailing_empty_rows,
    read_xml_from_archive,
    rename_archives_by_rule,
    resolve_selected_columns,
    save_archives,
    save_archives_streaming,
    scan_archives,
    sort_archives,
    strip_optional_header,
    write_xml_to_archive,
)


def _make_archive(path: str, xml_bytes: bytes | None = None) -> str:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if xml_bytes is not None:
            zf.writestr("ComicInfo.xml", xml_bytes)
        zf.writestr("page001.jpg", b"fake")
    return path


# ---------------------------------------------------------------------------
# read_xml_from_archive
# ---------------------------------------------------------------------------

class TestReadXmlFromArchive:
    def test_returns_none_when_no_xml(self, tmp_path):
        ap = _make_archive(str(tmp_path / "test.cbz"), None)
        assert read_xml_from_archive(ap) is None

    def test_reads_xml_case_sensitive(self, tmp_path):
        xml = build_xml_from_fields({"Title": "Test"})
        ap = _make_archive(str(tmp_path / "test.cbz"), xml)
        result = read_xml_from_archive(ap)
        assert result is not None
        assert b"Test" in result

    def test_reads_xml_case_insensitive(self, tmp_path):
        path = str(tmp_path / "test.cbz")
        xml = build_xml_from_fields({"Title": "Test"})
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("comicinfo.xml", xml)
            zf.writestr("page001.jpg", b"fake")
        result = read_xml_from_archive(path)
        assert result is not None
        assert b"Test" in result

    def test_returns_none_on_corrupt_archive(self, tmp_path):
        path = str(tmp_path / "bad.cbz")
        with open(path, "w") as f:
            f.write("not a zip")
        assert read_xml_from_archive(path) is None


# ---------------------------------------------------------------------------
# parse_xml_fields
# ---------------------------------------------------------------------------

class TestParseXmlFields:
    def test_parses_all_fields(self):
        fields = {
            "Title": "T", "Series": "S", "Number": "1", "Summary": "Sum",
            "Writer": "W", "Genre": "G", "Web": "https://x.com",
            "PublishingStatusTachiyomi": "C", "SourceMihon": "M",
            "PublicationYear": "2024", "PublicationMonth": "3",
        }
        xml = build_xml_from_fields(fields)
        result = parse_xml_fields(xml)
        for k, v in fields.items():
            assert result.get(k) == v, f"Mismatch for {k}"

    def test_returns_empty_on_invalid_xml(self):
        result = parse_xml_fields(b"not xml")
        assert all(v == "" for v in result.values())

    def test_missing_tags_return_empty(self):
        root = etree.Element("ComicInfo")
        etree.SubElement(root, "Title").text = "OnlyTitle"
        xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
        result = parse_xml_fields(xml)
        assert result["Title"] == "OnlyTitle"
        assert result["Series"] == ""


# ---------------------------------------------------------------------------
# build_xml_from_fields
# ---------------------------------------------------------------------------

class TestBuildXmlFromFields:
    def test_builds_valid_xml(self):
        xml = build_xml_from_fields({"Title": "Test", "Number": "5"})
        root = etree.fromstring(xml)
        assert root.find("Title").text == "Test"
        assert root.find("Number").text == "5"

    def test_all_fields_present(self):
        vals = [str(i) for i in range(len(XML_FIELD_TAGS))]
        fields = dict(zip(XML_FIELD_TAGS, vals))
        xml = build_xml_from_fields(fields)
        root = etree.fromstring(xml)
        for tag in XML_FIELD_TAGS:
            elem = root.find(tag)
            assert elem is not None and (elem.text or "") == fields[tag]

    def test_strips_whitespace(self):
        xml = build_xml_from_fields({"Title": "  Hello  "})
        root = etree.fromstring(xml)
        assert root.find("Title").text == "Hello"


# ---------------------------------------------------------------------------
# write_xml_to_archive
# ---------------------------------------------------------------------------

class TestWriteXmlToArchive:
    def test_writes_xml(self, tmp_path):
        path = str(tmp_path / "test.cbz")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("page.jpg", b"data")
        xml = build_xml_from_fields({"Title": "New"})
        assert write_xml_to_archive(path, xml)
        with zipfile.ZipFile(path, "r") as zf:
            assert "ComicInfo.xml" in zf.namelist()

    def test_replaces_existing_xml(self, tmp_path):
        orig = build_xml_from_fields({"Title": "Old"})
        path = _make_archive(str(tmp_path / "test.cbz"), orig)
        new = build_xml_from_fields({"Title": "Replaced"})
        assert write_xml_to_archive(path, new)
        content = read_xml_from_archive(path)
        assert b"Replaced" in content

    def test_preserves_other_files(self, tmp_path):
        path = str(tmp_path / "test.cbz")
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("img1.jpg", b"jpg")
            zf.writestr("img2.png", b"png")
        xml = build_xml_from_fields({"Title": "New"})
        assert write_xml_to_archive(path, xml)
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            assert "ComicInfo.xml" in names
            assert "img1.jpg" in names
            assert "img2.png" in names

    def test_returns_false_on_bad_archive(self, tmp_path):
        path = str(tmp_path / "bad.cbz")
        with open(path, "w") as f:
            f.write("not a zip")
        assert not write_xml_to_archive(path, b"<xml/>")


# ---------------------------------------------------------------------------
# list_dirs_with_archives
# ---------------------------------------------------------------------------

class TestListDirsWithArchives:
    def test_returns_empty_for_nonexistent(self):
        assert list_dirs_with_archives("/nonexistent") == []

    def test_finds_subdirs_with_archives(self, tmp_path):
        (tmp_path / "vol1").mkdir()
        (tmp_path / "vol2").mkdir()
        _make_archive(str(tmp_path / "vol1" / "ch01.cbz"))
        _make_archive(str(tmp_path / "vol2" / "ch02.zip"))
        result = list_dirs_with_archives(str(tmp_path))
        assert sorted(result) == ["vol1", "vol2"]

    def test_recursive(self, tmp_path):
        child = tmp_path / "p" / "c"
        child.mkdir(parents=True)
        _make_archive(str(child / "ch.cbz"))
        result = list_dirs_with_archives(str(tmp_path))
        assert "p/c" in result

    def test_skips_empty_dirs(self, tmp_path):
        (tmp_path / "empty").mkdir()
        assert list_dirs_with_archives(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# sort_archives
# ---------------------------------------------------------------------------

class TestSortArchives:
    def test_numeric_order(self):
        a = ["ch10.cbz", "ch2.cbz", "ch1.cbz"]
        assert sort_archives(a, "按数字大小顺序") == ["ch1.cbz", "ch2.cbz", "ch10.cbz"]

    def test_alpha_order(self):
        a = ["ch10.cbz", "ch2.cbz", "ch1.cbz"]
        assert sort_archives(a, "按字母顺序") == ["ch1.cbz", "ch10.cbz", "ch2.cbz"]

    def test_number_field_passthrough(self):
        a = ["ch3.cbz", "ch1.cbz"]
        assert sort_archives(a, "按Number列数字大小排序") == ["ch3.cbz", "ch1.cbz"]

    def test_empty(self):
        assert sort_archives([], "按数字大小顺序") == []


# ---------------------------------------------------------------------------
# _sort_by_number_field
# ---------------------------------------------------------------------------

class TestSortByNumberField:
    def test_sorts_by_number(self):
        a = ["a.cbz", "b.cbz", "c.cbz"]
        cached = {"a.cbz": {"Number": "3"}, "b.cbz": {"Number": "1"}, "c.cbz": {"Number": "2"}}
        result = _sort_by_number_field(a, cached)
        assert [os.path.basename(p) for p in result] == ["b.cbz", "c.cbz", "a.cbz"]

    def test_no_number_goes_last(self):
        a = ["a.cbz", "b.cbz"]
        cached = {"a.cbz": {"Number": "2"}, "b.cbz": {}}
        result = _sort_by_number_field(a, cached)
        assert os.path.basename(result[1]) == "b.cbz"


# ---------------------------------------------------------------------------
# scan_archives
# ---------------------------------------------------------------------------

class TestScanArchives:
    def test_empty_dir(self, tmp_path):
        csv_text, log, archives = scan_archives(str(tmp_path), True, "按数字大小顺序")
        assert csv_text.startswith("FileName,") or csv_text == ""
        assert archives == []

    def test_reads_archives(self, tmp_path):
        xml = build_xml_from_fields({"Title": "MyTitle", "Series": "MySeries"})
        _make_archive(str(tmp_path / "ch01.cbz"), xml)
        csv_text, _, archives = scan_archives(str(tmp_path), True, "按数字大小顺序")
        assert len(archives) == 1
        assert "MyTitle" in csv_text
        assert "MySeries" in csv_text

    def test_includes_header(self, tmp_path):
        _make_archive(str(tmp_path / "ch.cbz"), build_xml_from_fields({"Title": "T"}))
        csv_text, _, _ = scan_archives(str(tmp_path), True, "按数字大小顺序")
        assert csv_text.startswith("FileName,")

    def test_no_header(self, tmp_path):
        _make_archive(str(tmp_path / "ch.cbz"), build_xml_from_fields({"Title": "T"}))
        csv_text, _, _ = scan_archives(str(tmp_path), False, "按数字大小顺序")
        assert not csv_text.startswith("FileName,")

    def test_fills_defaults_when_no_xml(self, tmp_path):
        _make_archive(str(tmp_path / "ch01.cbz"), None)
        csv_text, _, _ = scan_archives(str(tmp_path), True, "按数字大小顺序")
        assert "ch01.cbz" in csv_text
        assert "ch01" in csv_text


# ---------------------------------------------------------------------------
# strip_optional_header
# ---------------------------------------------------------------------------

class TestStripOptionalHeader:
    def test_strips_header(self):
        rows = [["FileName", "Title"], ["ch01.cbz", "Test"]]
        assert strip_optional_header(rows, True) == [["ch01.cbz", "Test"]]

    def test_no_header_present(self):
        rows = [["ch01.cbz", "Test"]]
        assert strip_optional_header(rows, True) == rows

    def test_empty(self):
        assert strip_optional_header([], True) == []

    def test_always_strips(self):
        rows = [["FileName"], ["ch01.cbz"]]
        assert strip_optional_header(rows, False) == [["ch01.cbz"]]


# ---------------------------------------------------------------------------
# prune_trailing_empty_rows
# ---------------------------------------------------------------------------

class TestPruneTrailingEmptyRows:
    def test_removes_trailing(self):
        assert prune_trailing_empty_rows([["a"], [""], [""]]) == [["a"]]

    def test_keeps_internal(self):
        assert prune_trailing_empty_rows([["a"], [""], ["b"]]) == [["a"], [""], ["b"]]

    def test_empty(self):
        assert prune_trailing_empty_rows([]) == []


# ---------------------------------------------------------------------------
# _fields_equal
# ---------------------------------------------------------------------------

class TestFieldsEqual:
    def test_equal(self):
        a = dict(zip(XML_FIELD_TAGS, ["a", "b"]))
        b = dict(zip(XML_FIELD_TAGS, ["a", "b"]))
        assert _fields_equal(a, b)

    def test_different(self):
        a = dict(zip(XML_FIELD_TAGS, ["a", "b"]))
        b = dict(zip(XML_FIELD_TAGS, ["a", "c"]))
        assert not _fields_equal(a, b)


# ---------------------------------------------------------------------------
# save_archives / save_archives_streaming
# ---------------------------------------------------------------------------

class TestSaveArchives:
    def test_no_csv_text(self):
        log, success = save_archives(["a.cbz"], "", True, True)
        assert not success
        assert "无可保存" in log

    def test_no_archives(self):
        log, success = save_archives([], "a,b\n1,2", True, True)
        assert not success
        assert "请先扫描" in log

    def test_duplicate_filenames(self):
        csv_text = "FileName,Title\nch01.cbz,T1\nch01.cbz,T2\n"
        log, success = save_archives(["ch01.cbz"], csv_text, True, True)
        assert not success
        assert "重复" in log

    def test_missing_files_with_check(self, tmp_path):
        _make_archive(str(tmp_path / "ch01.cbz"), build_xml_from_fields({"Title": "T"}))
        csv_text = "FileName,Title\nch01.cbz,T\nch02.cbz,X\n"
        log, success = save_archives([str(tmp_path / "ch01.cbz")], csv_text, True, True)
        assert not success
        assert "未在扫描列表中" in log

    def test_saves_xml(self, tmp_path):
        _make_archive(str(tmp_path / "ch01.cbz"), build_xml_from_fields({"Title": "Old"}))
        arch = [str(tmp_path / "ch01.cbz")]
        csv_text = "FileName,Title,Series,Number,Summary,Writer,Genre,Web,PublishingStatusTachiyomi,SourceMihon,PublicationYear,PublicationMonth\nch01.cbz,NewTitle,NS,1,,A,,https://x.com,,,,,\n"
        log, success = save_archives(arch, csv_text, True, True)
        assert success, log
        assert "已保存" in log
        xml = read_xml_from_archive(arch[0])
        assert b"NewTitle" in xml

    def test_skips_unchanged(self, tmp_path):
        xml = build_xml_from_fields({"Title": "T", "Series": "S", "Number": "1"})
        _make_archive(str(tmp_path / "ch01.cbz"), xml)
        arch = [str(tmp_path / "ch01.cbz")]
        h = "FileName,Title,Series,Number,Summary,Writer,Genre,Web,"
        d = "PublishingStatusTachiyomi,SourceMihon,PublicationYear,PublicationMonth\n"
        csv_text = h + d + "ch01.cbz,T,S,1,,,,,,,,,\n"
        log, success = save_archives(arch, csv_text, True, True)
        assert success
        assert "无改动" in log

    def test_streaming_yields_same(self, tmp_path):
        _make_archive(str(tmp_path / "ch01.cbz"), build_xml_from_fields({"Title": "Old"}))
        arch = [str(tmp_path / "ch01.cbz")]
        csv_text = "FileName,Title,Series,Number,Summary,Writer,Genre,Web,PublishingStatusTachiyomi,SourceMihon,PublicationYear,PublicationMonth\nch01.cbz,NewTitle,NS,1,,A,,https://x.com,,,,,\n"
        msgs = list(save_archives_streaming(arch, csv_text, True, True))
        combined = "\n".join(msgs)
        assert "已保存" in combined


# ---------------------------------------------------------------------------
# export_csv
# ---------------------------------------------------------------------------

class TestExportCsv:
    def test_returns_provided_csv(self):
        data, name = export_csv("a,b\n1,2", True, "mydir", [])
        assert b"a,b" in data
        assert "mydir.csv" == name

    def test_generates_from_archives(self, tmp_path):
        _make_archive(str(tmp_path / "ch01.cbz"), build_xml_from_fields({"Title": "T"}))
        data, name = export_csv("", True, "dir", [str(tmp_path / "ch01.cbz")])
        assert b"T" in data
        assert b"FileName" in data

    def test_adds_header_when_missing(self):
        data, name = export_csv("ch01.cbz,T", True, "dir", [])
        assert data.decode("utf-8").startswith("FileName,")

    def test_no_duplicate_header(self):
        headers = ",".join(CSV_HEADERS)
        csv = headers + "\nch01.cbz,T,,,,,,,,,,\n"
        data, _ = export_csv(csv, True, "dir", [])
        assert data.decode("utf-8").count("FileName") == 1


# ---------------------------------------------------------------------------
# import_csv_content
# ---------------------------------------------------------------------------

class TestImportCsvContent:
    def test_strips_header(self):
        headers = ",".join(CSV_HEADERS)
        content = headers + "\nch01.cbz,T,,,,,,,,,,\n"
        result = import_csv_content(content, False)
        assert result.rstrip("\r\n") == "ch01.cbz,T,,,,,,,,,,"

    def test_keeps_header(self):
        result = import_csv_content("FileName,Title\nch01.cbz,T\n", True)
        assert "FileName" in result

    def test_handles_bytes(self):
        result = import_csv_content(b"FileName,Title\nch01.cbz,T\n", True)
        assert "FileName" in result

    def test_handles_bad_encoding(self):
        result = import_csv_content(b"\xff\xfea,b\n1,2\n", True)
        assert result is not None


# ---------------------------------------------------------------------------
# extract_headers
# ---------------------------------------------------------------------------

class TestExtractHeaders:
    def test_extracts(self):
        assert extract_headers("a,b,c\n1,2,3\n") == ["a", "b", "c"]

    def test_empty(self):
        assert extract_headers("") == []

    def test_strips_whitespace(self):
        assert extract_headers("  a , b \n1,2\n") == ["a", "b"]


# ---------------------------------------------------------------------------
# resolve_selected_columns
# ---------------------------------------------------------------------------

class TestResolveSelectedColumns:
    def test_all_mark(self):
        csv_text = ",".join(CSV_HEADERS) + "\n1,2\n"
        result = resolve_selected_columns(csv_text, True, [ALL_MARK])
        assert "FileName" not in result

    def test_specific_columns(self):
        csv_text = "FileName,Title,Series\n1,2,3\n"
        result = resolve_selected_columns(csv_text, True, ["Title"])
        assert result == ["Title"]

    def test_excludes_filename(self):
        csv_text = "FileName,Title\n1,2\n"
        result = resolve_selected_columns(csv_text, True, ["FileName"])
        assert result == []


# ---------------------------------------------------------------------------
# batch_set
# ---------------------------------------------------------------------------

class TestBatchSet:
    def test_sets_columns(self):
        csv_text = "FileName,Title,Series\nch01.cbz,Old,OldS\n"
        result = batch_set(csv_text, True, ["Title", "Series"], "New")
        assert result.count("New") == 2

    def test_ignores_filename(self):
        csv_text = "FileName,Title\nch01.cbz,Old\n"
        result = batch_set(csv_text, True, ["FileName"], "New")
        assert "ch01.cbz" in result

    def test_preserves_header(self):
        csv_text = "FileName,Title\nch01.cbz,Old\n"
        result = batch_set(csv_text, True, ["Title"], "New")
        assert result.startswith("FileName,")

    def test_no_selection(self):
        csv_text = "FileName,Title\nch01.cbz,Old\n"
        assert batch_set(csv_text, True, [], "New") == csv_text

    def test_no_header_mode(self):
        csv_text = "ch01.cbz,Old\nch02.cbz,Old2\n"
        result = batch_set(csv_text, False, ["Title"], "New")
        assert "New" in result


# ---------------------------------------------------------------------------
# batch_find_replace
# ---------------------------------------------------------------------------

class TestBatchFindReplace:
    def test_basic(self):
        csv_text = "FileName,Title\nch01.cbz,Hello World\n"
        result = batch_find_replace(csv_text, True, ["Title"], "World", "Manga")
        assert "Hello Manga" in result

    def test_regex(self):
        csv_text = "FileName,Title\nch01.cbz,Hell0\n"
        result = batch_find_replace(csv_text, True, ["Title"], r"\d", "X", use_regex=True)
        assert "HellX" in result

    def test_invalid_regex(self):
        csv_text = "FileName,Title\nch01.cbz,Hello\n"
        result = batch_find_replace(csv_text, True, ["Title"], r"[", "X", use_regex=True)
        assert "Hello" in result

    def test_empty_find(self):
        csv_text = "FileName,Title\nch01.cbz,Hello\n"
        assert batch_find_replace(csv_text, True, ["Title"], "", "X") == csv_text


# ---------------------------------------------------------------------------
# batch_prefix / batch_suffix
# ---------------------------------------------------------------------------

class TestBatchPrefix:
    def test_adds_prefix(self):
        csv_text = "FileName,Title\nch01.cbz,World\n"
        result = batch_prefix(csv_text, True, ["Title"], "Hello")
        assert "HelloWorld" in result

class TestBatchSuffix:
    def test_adds_suffix(self):
        csv_text = "FileName,Title\nch01.cbz,Hello\n"
        result = batch_suffix(csv_text, True, ["Title"], "World")
        assert "HelloWorld" in result


# ---------------------------------------------------------------------------
# _replace_placeholders
# ---------------------------------------------------------------------------

class TestReplacePlaceholders:
    def test_basic(self):
        row = ["ch01.cbz", "MyTitle", "MySeries", "1"]
        header = ["FileName", "Title", "Series", "Number"]
        n2i = {n: i for i, n in enumerate(header)}
        result = _replace_placeholders("{Series} - {Title}", row, header, n2i)
        assert result == "MySeries - MyTitle"

    def test_zero_padding(self):
        row = ["ch01.cbz", "T", "S", "5"]
        header = ["FileName", "Title", "Series", "Number"]
        n2i = {n: i for i, n in enumerate(header)}
        result = _replace_placeholders("{Number:3}", row, header, n2i)
        assert result == "005"

    def test_unknown_placeholder(self):
        row = ["ch01.cbz"]
        header = ["FileName"]
        n2i = {"FileName": 0}
        result = _replace_placeholders("{Unknown}", row, header, n2i)
        assert result == ""

    def test_missing_value(self):
        row = ["ch01.cbz"]
        header = ["FileName", "Title"]
        n2i = {n: i for i, n in enumerate(header)}
        result = _replace_placeholders("{Title}", row, header, n2i)
        assert result == ""


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_replaces_whitespace(self):
        result = _sanitize_filename("Hello World Chapter", "_")
        assert result == "Hello_World_Chapter"

    def test_removes_illegal_chars(self):
        result = _sanitize_filename('Hello:World?Test*Name', "_")
        assert "/" not in result
        assert "\\" not in result
        assert ":" not in result

    def test_returns_empty_for_dot(self):
        assert _sanitize_filename(".", "_") == ""

    def test_returns_empty_for_dotdot(self):
        assert _sanitize_filename("..", "_") == ""

    def test_returns_empty_for_blank(self):
        assert _sanitize_filename("   ", "_") == ""
        assert _sanitize_filename("", "_") == ""


# ---------------------------------------------------------------------------
# preview_rename_by_rule
# ---------------------------------------------------------------------------

class TestPreviewRenameByRule:
    def test_empty_rule(self):
        result, err = preview_rename_by_rule(["a.cbz"], "FileName,Title\na.cbz,T", True, "")
        assert err != ""
        assert result == []

    def test_no_archives(self):
        result, err = preview_rename_by_rule([], "FileName,Title\na.cbz,T", True, "{Title}")
        assert err != ""
        assert result == []

    def test_basic_preview(self):
        csv_text = "FileName,Title,Number\na.cbz,Ch1,1\nb.cbz,Ch2,2\n"
        archives = ["a.cbz", "b.cbz"]
        result, err = preview_rename_by_rule(archives, csv_text, True, "{Title} - {Number:3}")
        assert err == ""
        assert len(result) == 2
        old_names = [p[0] for p in result]
        new_names = [p[1] for p in result]
        assert "a.cbz" in old_names
        assert "Ch1_-_001.cbz" in new_names


# ---------------------------------------------------------------------------
# rename_archives_by_rule (integration-level test without actual rename)
# ---------------------------------------------------------------------------

class TestRenameArchivesByRule:
    def test_empty_rule(self):
        csv_text, log, arch = rename_archives_by_rule(
            ["a.cbz"], "/tmp", "FileName,Title\na.cbz,T", True, ""
        )
        assert "规则不能为空" in log

    def test_no_archives(self):
        csv_text, log, arch = rename_archives_by_rule(
            [], "/tmp", "FileName,Title\n", True, "{Title}"
        )
        assert "请先扫描" in log

    def test_missing_comic_dir(self):
        csv_text, log, arch = rename_archives_by_rule(
            ["a.cbz"], "/nonexistent/dir", "FileName,Title\na.cbz,T", True, "{Title}"
        )
        assert "章节目录不存在" in log
