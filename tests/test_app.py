"""Tests for app.py"""
import re
from pathlib import Path

from starlette.testclient import TestClient

from app import _get_version, app


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
