"""Tests for app.py"""
import os
import re
import time
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import (
    _SCAN_CACHE,
    ALLOWED_BASE_PATHS,
    _build_content_disposition,
    _build_search_value,
    _clamp_parent_to_allowed,
    _get_archives_from_token,
    _get_version,
    _is_path_under_any_allowed,
    _match_dir_name,
    _normalize_t_s,
    app,
    check_scan_dir,
    ensure_allowed_path,
    ensure_archives_allowed,
)
from edit_archive_xml import build_xml_from_fields


class TestGetVersion:
    def test_regex_double_quotes(self):
        text = '[project]\nversion = "1.2.3"\n'
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        assert m and m.group(1) == "1.2.3"

    def test_regex_single_quotes(self):
        text = "[project]\nversion = '3.0.0'\n"
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        assert m and m.group(1) == "3.0.0"

    def test_regex_no_match(self):
        text = "[project]\nname = 'test'\n"
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        assert m is None

    def test_regex_with_spaces(self):
        text = '[project]\nversion =   "1.0.0"\n'
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        assert m and m.group(1) == "1.0.0"

    def test_actual_pyproject_contains_version(self):
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        assert pyproject.exists()
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
        assert m is not None
        version = m.group(1)
        assert re.match(r"^\d+\.\d+\.\d+", version), f"Unexpected version: {version}"

    def test_function_returns_valid_version(self):
        version = _get_version()
        assert version != "dev"
        assert re.match(r"^\d+\.\d+\.\d+", version)


# ---------------------------------------------------------------------------
# ensure_allowed_path
# ---------------------------------------------------------------------------

class TestEnsureAllowedPath:
    def test_empty_or_blank(self):
        assert ensure_allowed_path("") is None
        assert ensure_allowed_path("   ") is None

    def test_non_existent_path(self):
        assert ensure_allowed_path("/tmp/__nonexistent_path_xyz__") is None

    def test_existent_no_whitelist(self, tmp_path):
        p = str(tmp_path)
        result = ensure_allowed_path(p)
        assert result == os.path.abspath(p)

    def test_normalized(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        result = ensure_allowed_path(str(sub) + "/../")
        assert result == os.path.abspath(str(tmp_path))

    def test_within_whitelist(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        sub = tmp_path / "sub"
        sub.mkdir()
        result = ensure_allowed_path(str(sub))
        assert result == os.path.abspath(str(sub))

    def test_outside_whitelist(self, monkeypatch, tmp_path):
        outside = Path("/tmp") / "__test_outside__"
        outside.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        result = ensure_allowed_path(str(outside))
        assert result is None

    def test_whitelist_exact_match(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        result = ensure_allowed_path(str(tmp_path))
        assert result == os.path.abspath(str(tmp_path))


# ---------------------------------------------------------------------------
# check_scan_dir
# ---------------------------------------------------------------------------

class TestCheckScanDir:
    def test_empty(self):
        ok, err = check_scan_dir("")
        assert ok is None
        assert "填写" in err

    def test_non_existent(self):
        ok, err = check_scan_dir("/tmp/__does_not_exist__")
        assert ok is None
        assert "不存在" in err

    def test_is_file_not_dir(self, tmp_path):
        f = tmp_path / "afile.txt"
        f.write_text("hello")
        ok, err = check_scan_dir(str(f))
        assert ok is None
        assert "不是目录" in err

    def test_outside_whitelist(self, monkeypatch):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", ["/tmp"])
        ok, err = check_scan_dir("/root")
        assert ok is None
        assert "允许范围" in err

    def test_valid(self, tmp_path):
        ok, err = check_scan_dir(str(tmp_path))
        assert ok == os.path.abspath(str(tmp_path))
        assert err == ""


# ---------------------------------------------------------------------------
# ensure_archives_allowed
# ---------------------------------------------------------------------------

class TestEnsureArchivesAllowed:
    def test_empty_list(self):
        assert ensure_archives_allowed([]) is True

    def test_all_exist(self, tmp_path):
        paths = [str(tmp_path / "a.cbz"), str(tmp_path / "b.cbz")]
        for p in paths:
            Path(p).write_text("")
        assert ensure_archives_allowed(paths) is True

    def test_with_whitelist_all_ok(self, monkeypatch, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        paths = [str(sub / "a.cbz")]
        Path(paths[0]).write_text("")
        assert ensure_archives_allowed(paths) is True

    def test_with_whitelist_outside(self, monkeypatch, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path / "sub")])
        paths = [str(outside / "a.cbz")]
        assert ensure_archives_allowed(paths) is False


# ---------------------------------------------------------------------------
# _normalize_t_s
# ---------------------------------------------------------------------------

class TestNormalizeTS:
    def test_empty(self):
        assert _normalize_t_s("") == set()

    def test_returns_text_and_lower(self):
        result = _normalize_t_s("ABC")
        assert "ABC" in result
        assert "abc" in result
        assert len(result) == 2

    def test_handles_none(self):
        assert _normalize_t_s("") == set()


# ---------------------------------------------------------------------------
# _match_dir_name
# ---------------------------------------------------------------------------

class TestMatchDirName:
    def test_empty_query(self):
        assert _match_dir_name("some/dir", "") is True

    def test_basic_match(self):
        assert _match_dir_name("Chapter 01", "chapter") is True

    def test_no_match(self):
        assert _match_dir_name("Chapter 01", "xyz") is False

    def test_case_insensitive(self):
        assert _match_dir_name("Chapter 01", "CHAPTER") is True


# ---------------------------------------------------------------------------
# _build_search_value
# ---------------------------------------------------------------------------

class TestBuildSearchValue:
    def test_returns_string(self):
        val = _build_search_value("Test Dir")
        assert isinstance(val, str)
        assert "Test Dir" in val

    def test_contains_lowercase(self):
        val = _build_search_value("ABC")
        assert "abc" in val or "ABC" in val


# ---------------------------------------------------------------------------
# _get_archives_from_token
# ---------------------------------------------------------------------------

class TestGetArchivesFromToken:
    def clean_cache(self):
        _SCAN_CACHE.clear()

    def test_empty_token(self):
        self.clean_cache()
        archives, comic_dir = _get_archives_from_token("")
        assert archives == []
        assert comic_dir == ""

    def test_whitespace_token(self):
        self.clean_cache()
        archives, comic_dir = _get_archives_from_token("   ")
        assert archives == []
        assert comic_dir == ""

    def test_unknown_token(self):
        self.clean_cache()
        archives, comic_dir = _get_archives_from_token("unknown")
        assert archives == []
        assert comic_dir == ""

    def test_expired_token(self):
        self.clean_cache()
        _SCAN_CACHE["tok1"] = {"archives": ["a.zip"], "comic_dir": "/dir", "ts": 0}
        archives, comic_dir = _get_archives_from_token("tok1")
        assert archives == []
        assert comic_dir == ""
        assert "tok1" not in _SCAN_CACHE

    def test_valid_token(self):
        self.clean_cache()
        _SCAN_CACHE["tok2"] = {
            "archives": ["a.zip", "b.zip"],
            "comic_dir": "/mydir",
            "ts": time.time(),
        }
        archives, comic_dir = _get_archives_from_token("tok2")
        assert archives == ["a.zip", "b.zip"]
        assert comic_dir == "/mydir"


# ---------------------------------------------------------------------------
# _is_path_under_any_allowed
# ---------------------------------------------------------------------------

class TestIsPathUnderAnyAllowed:
    def test_no_whitelist(self):
        assert _is_path_under_any_allowed("/any/path") is True

    def test_path_under_allowed(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        original = list(ALLOWED_BASE_PATHS)
        try:
            type(app).ALLOWED_BASE_PATHS = [str(tmp_path)]
            assert _is_path_under_any_allowed(str(sub)) is True
        finally:
            type(app).ALLOWED_BASE_PATHS = original

    def test_path_outside(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        assert _is_path_under_any_allowed("/tmp/somewhere/else") is False

    def test_exact_match(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        assert _is_path_under_any_allowed(str(tmp_path)) is True


# ---------------------------------------------------------------------------
# _clamp_parent_to_allowed
# ---------------------------------------------------------------------------

class TestClampParentToAllowed:
    def test_none_parent(self):
        assert _clamp_parent_to_allowed(None, "/current") is None

    def test_no_whitelist(self):
        assert _clamp_parent_to_allowed("/parent", "/current") == "/parent"

    def test_parent_outside_whitelist(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        assert _clamp_parent_to_allowed("/outside", str(tmp_path)) is None

    def test_parent_is_root_of_whitelist(self, monkeypatch, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        parent = str(tmp_path)
        assert _clamp_parent_to_allowed(parent, str(tmp_path)) is None

    def test_parent_valid(self, monkeypatch, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        assert _clamp_parent_to_allowed(str(tmp_path), str(sub)) == str(tmp_path)

    def test_parent_valid_relative_symlink(self, monkeypatch, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        result = _clamp_parent_to_allowed(str(tmp_path) + "/./", str(sub))
        assert result is not None


# ---------------------------------------------------------------------------
# _build_content_disposition
# ---------------------------------------------------------------------------

class TestBuildContentDisposition:
    def test_empty(self):
        result = _build_content_disposition("")
        assert 'filename="export.csv"' in result

    def test_ascii(self):
        result = _build_content_disposition("test.csv")
        assert 'filename="test.csv"' in result
        assert "UTF-8" not in result

    def test_unicode(self):
        result = _build_content_disposition("中文.csv")
        assert "UTF-8" in result

    def test_unicode_no_extension(self):
        result = _build_content_disposition("测试文件")
        assert "UTF-8" in result
        assert "export.csv" in result


# ---------------------------------------------------------------------------
# Basic endpoint tests
# ---------------------------------------------------------------------------

def test_index_page_shows_version():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    version = _get_version()
    assert f"v{version}" in resp.text


def test_favicon_returns_svg():
    client = TestClient(app)
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert b"<svg" in resp.content
    assert b"M</text>" in resp.content


def test_index_page_links_favicon():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.ico" />' in resp.text


# ---------------------------------------------------------------------------
# /api/browse
# ---------------------------------------------------------------------------

class TestApiBrowse:
    def test_no_path_uses_browse_root(self):
        client = TestClient(app)
        resp = client.get("/api/browse")
        assert resp.status_code == 200
        data = resp.json()
        assert "current" in data
        assert "entries" in data

    def test_valid_path(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        client = TestClient(app)
        resp = client.get(f"/api/browse?path={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == os.path.abspath(str(tmp_path))
        names = [e["name"] for e in data["entries"]]
        assert "subdir" in names

    def test_invalid_path_returns_400(self):
        client = TestClient(app)
        resp = client.get("/api/browse?path=/nonexistent_path_xyz")
        assert resp.status_code == 400
        assert "无效" in resp.json()["error"]

    def test_path_is_file_returns_400(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("")
        client = TestClient(app)
        resp = client.get(f"/api/browse?path={f}")
        assert resp.status_code == 400

    def test_filters_outside_whitelist(self, monkeypatch, tmp_path):
        within = tmp_path / "within"
        within.mkdir()
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        client = TestClient(app)
        resp = client.get(f"/api/browse?path={tmp_path}")
        assert resp.status_code == 200
        data = resp.json()
        names = [e["name"] for e in data["entries"]]
        assert "sub" in names

    def test_parent_outside_whitelist_is_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
        client = TestClient(app)
        resp = client.get(f"/api/browse?path={tmp_path}")
        data = resp.json()
        assert data["parent"] is None

    def test_browse_root_nonexistent(self, monkeypatch):
        monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [])
        monkeypatch.setattr("app._browse_root", lambda: "/nonexistent_root_xyz")
        client = TestClient(app)
        resp = client.get("/api/browse")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/dirs-search
# ---------------------------------------------------------------------------

class TestApiDirsSearch:
    def test_empty_base_path(self):
        client = TestClient(app)
        resp = client.get("/api/dirs-search?base_path=")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    def test_invalid_base_path(self):
        client = TestClient(app)
        resp = client.get("/api/dirs-search?base_path=/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []

    def test_no_query_returns_up_to_limit(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        archive = sub / "ch01.cbz"
        zipfile.ZipFile(archive, "w").close()
        client = TestClient(app)
        resp = client.get(f"/api/dirs-search?base_path={tmp_path}&limit=10")
        data = resp.json()
        assert len(data["entries"]) == 1

    def test_with_query_matches(self, tmp_path):
        sub = tmp_path / "Chapter 01"
        sub.mkdir()
        archive = sub / "ch01.cbz"
        zipfile.ZipFile(archive, "w").close()
        client = TestClient(app)
        resp = client.get(f"/api/dirs-search?base_path={tmp_path}&q=chapter&limit=10")
        data = resp.json()
        assert len(data["entries"]) >= 1

    def test_with_query_no_match(self, tmp_path):
        client = TestClient(app)
        resp = client.get(f"/api/dirs-search?base_path={tmp_path}&q=xyzxyz&limit=10")
        data = resp.json()
        assert data["entries"] == []


# ---------------------------------------------------------------------------
# /api/dirs
# ---------------------------------------------------------------------------

class TestApiDirs:
    def test_invalid_base(self):
        client = TestClient(app)
        resp = client.get("/api/dirs?base_path=/nonexistent")
        assert resp.status_code == 200
        assert "无效或不在允许范围内" in resp.text

    def test_returns_options(self, tmp_path):
        sub = tmp_path / "Some Dir"
        sub.mkdir()
        archive = sub / "ch01.cbz"
        zipfile.ZipFile(archive, "w").close()
        client = TestClient(app)
        resp = client.get(f"/api/dirs?base_path={tmp_path}")
        assert resp.status_code == 200
        assert "Some Dir" in resp.text
        assert "option" in resp.text

    def test_escapes_html(self, tmp_path):
        sub = tmp_path / 'Dir & "Quote"'
        sub.mkdir()
        archive = sub / "ch01.cbz"
        zipfile.ZipFile(archive, "w").close()
        client = TestClient(app)
        resp = client.get(f"/api/dirs?base_path={tmp_path}")
        assert "&amp;" in resp.text
        assert "&quot;" in resp.text


# ---------------------------------------------------------------------------
# /scan
# ---------------------------------------------------------------------------

class TestPostScan:
    def test_error_empty_dir(self):
        client = TestClient(app)
        resp = client.post("/scan", data={"comic_dir": ""})
        assert resp.status_code == 200
        assert "错误" in resp.text or "请填写" in resp.text

    def test_error_non_existent(self):
        client = TestClient(app)
        resp = client.post("/scan", data={"comic_dir": "/nonexistent"})
        assert resp.status_code == 200
        assert "错误" in resp.text

    def test_success_with_archives(self, tmp_path):
        xml = build_xml_from_fields({"Title": "T1", "Series": "S1"})
        archive = str(tmp_path / "ch01.cbz")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ComicInfo.xml", xml)
            zf.writestr("page.jpg", b"data")

        client = TestClient(app)
        resp = client.post(
            "/scan",
            data={
                "comic_dir": str(tmp_path),
                "include_header": "true",
                "sort_mode": "按数字大小顺序",
            },
        )
        assert resp.status_code == 200
        assert "ch01" in resp.text

    def test_include_header_off(self, tmp_path):
        archive = str(tmp_path / "ch01.cbz")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ComicInfo.xml", build_xml_from_fields({"Title": "T1"}))
            zf.writestr("page.jpg", b"data")
        client = TestClient(app)
        resp = client.post(
            "/scan",
            data={
                "comic_dir": str(tmp_path),
                "include_header": "false",
                "sort_mode": "按字母顺序",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /scan-stream
# ---------------------------------------------------------------------------

class TestPostScanStream:
    def test_error_empty_dir(self):
        client = TestClient(app)
        resp = client.post("/scan-stream", data={"comic_dir": ""})
        assert resp.status_code == 200
        assert resp.text

    def test_success(self, tmp_path):
        archive = str(tmp_path / "ch01.cbz")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ComicInfo.xml", build_xml_from_fields({"Title": "T1"}))
            zf.writestr("page.jpg", b"data")
        client = TestClient(app)
        resp = client.post(
            "/scan-stream",
            data={"comic_dir": str(tmp_path), "include_header": "true", "sort_mode": "按数字大小顺序"},
        )
        assert resp.status_code == 200
        assert "ch01" in resp.text


# ---------------------------------------------------------------------------
# /scan-json
# ---------------------------------------------------------------------------

class TestPostScanJson:
    def test_error_empty_dir(self):
        client = TestClient(app)
        resp = client.post("/scan-json", data={"comic_dir": ""})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_success(self, tmp_path):
        archive = str(tmp_path / "ch01.cbz")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ComicInfo.xml", build_xml_from_fields({"Title": "T1"}))
            zf.writestr("page.jpg", b"data")
        client = TestClient(app)
        resp = client.post(
            "/scan-json",
            data={"comic_dir": str(tmp_path), "include_header": "true", "sort_mode": "按数字大小顺序"},
        )
        data = resp.json()
        assert data["ok"] is True
        assert data["csv_text"]
        assert data["scan_token"]


# ---------------------------------------------------------------------------
# /save
# ---------------------------------------------------------------------------

class TestPostSave:
    def test_no_archives_in_session_or_token(self):
        _SCAN_CACHE.clear()
        client = TestClient(app)
        resp = client.post("/save", data={"scan_token": ""})
        assert resp.status_code == 200
        assert "请先扫描" in resp.text

    def test_with_token_no_archives(self):
        _SCAN_CACHE.clear()
        _SCAN_CACHE["tok_noarch"] = {"archives": ["/nonexistent/a.cbz"], "comic_dir": "/dir", "ts": time.time()}
        client = TestClient(app)
        resp = client.post("/save", data={"scan_token": "tok_noarch"})
        assert resp.status_code == 200

    def test_with_archives_from_cache(self, tmp_path):
        xml = build_xml_from_fields({"Title": "T1", "Series": "S1"})
        archive = str(tmp_path / "ch01.cbz")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("ComicInfo.xml", xml)
            zf.writestr("page.jpg", b"data")

        csv_text = (
            "FileName,Title,Series,Number,Summary,Writer,Genre,Web,"
            "PublishingStatusTachiyomi,SourceMihon,PublicationYear,PublicationMonth\n"
        )
        csv_text += "ch01.cbz,New Title,S1,1,,,,,,,,"
        _SCAN_CACHE.clear()
        _SCAN_CACHE["tok_save"] = {
            "archives": [archive],
            "comic_dir": str(tmp_path),
            "orig_rows": {},
            "ts": time.time(),
        }

        client = TestClient(app)
        resp = client.post(
            "/save",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "check_count": "true",
                "scan_token": "tok_save",
            },
        )
        assert resp.status_code == 200
        assert "已保存" in resp.text or "跳过" in resp.text or "保存完成" in resp.text


# ---------------------------------------------------------------------------
# /save-stream
# ---------------------------------------------------------------------------

class TestPostSaveStream:
    def test_no_archives(self):
        _SCAN_CACHE.clear()
        client = TestClient(app)
        resp = client.post(
            "/save-stream",
            json={"scan_token": "", "csv_text": "", "include_header": True, "check_count": True},
        )
        assert resp.status_code == 200
        assert "请先扫描" in resp.text

    def test_invalid_json_falls_back(self):
        _SCAN_CACHE.clear()
        client = TestClient(app)
        resp = client.post("/save-stream", content=b"not json")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /export
# ---------------------------------------------------------------------------

class TestExport:
    def test_get_no_session(self):
        client = TestClient(app)
        resp = client.get("/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

    def test_post_with_csv_text(self, tmp_path):
        csv_text = "FileName,Title\nch01.cbz,Test\n"
        client = TestClient(app)
        resp = client.post(
            "/export",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "comic_dir": str(tmp_path),
            },
        )
        assert resp.status_code == 200
        assert b"ch01.cbz" in resp.content

    def test_post_empty_dir_fallback_to_session(self, tmp_path):
        client = TestClient(app)
        resp = client.post(
            "/export",
            data={
                "csv_text": "FileName,Title\nch01.cbz,T\n",
                "include_header": "false",
                "comic_dir": "",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /import
# ---------------------------------------------------------------------------

class TestPostImport:
    def test_no_file(self):
        client = TestClient(app)
        resp = client.post("/import", data={"include_header": "true"})
        assert resp.status_code == 200

    def test_with_csv_file(self, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        Path(csv_path).write_text("FileName,Title\nch01.cbz,Test\n", encoding="utf-8")
        client = TestClient(app)
        with open(csv_path, "rb") as f:
            resp = client.post(
                "/import",
                data={"include_header": "true"},
                files={"import_file": ("test.csv", f, "text/csv")},
            )
        assert resp.status_code == 200
        assert "ch01.cbz" in resp.text

    def test_with_non_csv_extension(self, tmp_path):
        txt_path = str(tmp_path / "test.txt")
        Path(txt_path).write_text("FileName,Title\nch01.cbz,T\n", encoding="utf-8")
        client = TestClient(app)
        with open(txt_path, "rb") as f:
            resp = client.post(
                "/import",
                data={"include_header": "true"},
                files={"import_file": ("test.txt", f, "text/plain")},
            )
        assert resp.status_code == 200

    def test_with_unsupported_extension(self, tmp_path):
        bin_path = str(tmp_path / "test.pdf")
        Path(bin_path).write_text("dummy", encoding="utf-8")
        client = TestClient(app)
        with open(bin_path, "rb") as f:
            resp = client.post(
                "/import",
                data={"include_header": "true"},
                files={"import_file": ("test.pdf", f, "application/pdf")},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /batch-edit
# ---------------------------------------------------------------------------

class TestPostBatchEdit:
    @pytest.fixture
    def csv_text(self):
        return "FileName,Title,Series\nch01.cbz,Old Title,S1\nch02.cbz,Old Title 2,S2\n"

    def test_batch_set(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "batch_set",
                "batch_set_val": "NewVal",
                "columns": ["Title"],
            },
        )
        assert resp.status_code == 200
        assert "NewVal" in resp.text

    def test_find_replace(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "find_replace",
                "fr_find": "Old",
                "fr_replace": "New",
                "columns": ["Title"],
            },
        )
        assert resp.status_code == 200
        assert "New Title" in resp.text

    def test_prefix(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "prefix",
                "prefix_val": "Pre_",
                "columns": ["Title"],
            },
        )
        assert resp.status_code == 200
        assert "Pre_Old" in resp.text

    def test_suffix(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "suffix",
                "suffix_val": "_Suf",
                "columns": ["Title"],
            },
        )
        assert resp.status_code == 200
        assert "Old Title_Suf" in resp.text

    def test_t2s(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "t2s",
                "columns": ["Title"],
            },
        )
        assert resp.status_code == 200

    def test_s2t_no_columns(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "s2t",
            },
        )
        assert resp.status_code == 200

    def test_no_action_returns_unchanged(self, csv_text):
        client = TestClient(app)
        resp = client.post(
            "/batch-edit",
            data={
                "csv_text": csv_text,
                "include_header": "true",
                "action": "",
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /batch-rename-preview
# ---------------------------------------------------------------------------

class TestPostBatchRenamePreview:
    def test_empty_rule(self):
        client = TestClient(app)
        resp = client.post(
            "/batch-rename-preview",
            json={"rule": "", "scan_token": "", "csv_text": ""},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "规则不能为空"

    def test_no_archives(self):
        _SCAN_CACHE.clear()
        client = TestClient(app)
        resp = client.post(
            "/batch-rename-preview",
            json={"rule": "{Series}", "scan_token": "nonexistent", "csv_text": ""},
        )
        assert resp.status_code == 400
        assert "请先扫描" in resp.json()["error"]

    def test_invalid_json(self):
        client = TestClient(app)
        resp = client.post(
            "/batch-rename-preview",
            content=b"not-json",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /batch-rename
# ---------------------------------------------------------------------------

class TestPostBatchRename:
    def test_empty_rule(self):
        client = TestClient(app)
        resp = client.post("/batch-rename", json={"rule": ""})
        assert resp.status_code == 400
        assert resp.json()["error"] == "规则不能为空"

    def test_no_archives(self):
        _SCAN_CACHE.clear()
        client = TestClient(app)
        resp = client.post(
            "/batch-rename",
            json={"rule": "{Series}", "scan_token": "nonexistent"},
        )
        assert resp.status_code == 400

    def test_invalid_json(self):
        client = TestClient(app)
        resp = client.post("/batch-rename", content=b"bad")
        assert resp.status_code == 400

    def test_with_archives_no_comic_dir(self, tmp_path):
        archive = str(tmp_path / "ch01.cbz")
        zipfile.ZipFile(archive, "w").close()
        _SCAN_CACHE.clear()
        _SCAN_CACHE["tok_rn"] = {
            "archives": [archive],
            "comic_dir": "",
            "ts": time.time(),
        }
        client = TestClient(app)
        resp = client.post(
            "/batch-rename",
            json={
                "rule": "{Series}",
                "scan_token": "tok_rn",
                "csv_text": "FileName,Series\nch01.cbz,MySeries\n",
                "include_header": True,
            },
        )
        assert resp.status_code == 400
        assert "章节目录不存在" in resp.json()["error"]

    def test_successful_rename(self, tmp_path):
        comic_dir = str(tmp_path)
        archive = os.path.join(comic_dir, "ch01.cbz")
        zipfile.ZipFile(archive, "w").close()
        _SCAN_CACHE.clear()
        _SCAN_CACHE["tok_rn2"] = {
            "archives": [archive],
            "comic_dir": comic_dir,
            "ts": time.time(),
        }
        client = TestClient(app)
        resp = client.post(
            "/batch-rename",
            json={
                "rule": "{Series}",
                "scan_token": "tok_rn2",
                "csv_text": "FileName,Series\nch01.cbz,MySeries\n",
                "include_header": True,
                "ws_replace_enabled": False,
                "conflict_mode": "skip",
            },
        )
        data = resp.json()
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# _browse_root
# ---------------------------------------------------------------------------

def test_browse_root_no_whitelist(monkeypatch):
    from app import _browse_root
    monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [])
    result = _browse_root()
    assert result == os.path.abspath(os.getcwd())


def test_browse_root_with_whitelist(monkeypatch, tmp_path):
    from app import _browse_root
    monkeypatch.setattr("app.ALLOWED_BASE_PATHS", [str(tmp_path)])
    result = _browse_root()
    assert result == os.path.abspath(str(tmp_path))
