import os
import tempfile
import zipfile
from pathlib import Path

import pytest

from edit_archive_xml import (
    XML_FIELD_TAGS,
    build_xml_from_fields,
)


def make_comic_xml(fields: dict[str, str]) -> bytes:
    """Helper: build ComicInfo.xml bytes."""
    return build_xml_from_fields(fields)


def make_archive(tmp_dir: str, name: str, xml_bytes: bytes | None = None) -> str:
    """Create a .cbz archive in tmp_dir with optional ComicInfo.xml."""
    path = os.path.join(tmp_dir, name)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if xml_bytes is not None:
            zf.writestr("ComicInfo.xml", xml_bytes)
        zf.writestr("page001.jpg", b"fake-image-data")
    return path


@pytest.fixture
def tmp_workdir():
    """Provide a temporary directory that is automatically cleaned up."""
    with tempfile.TemporaryDirectory() as d:
        old_cwd = os.getcwd()
        os.chdir(d)
        yield Path(d)
        os.chdir(old_cwd)


@pytest.fixture
def sample_xml_bytes():
    return make_comic_xml({
        "Title": "Test Title",
        "Series": "Test Series",
        "Number": "1",
        "Summary": "A test summary",
        "Writer": "Test Author",
        "Genre": "Action",
        "Web": "https://example.com",
        "PublishingStatusTachiyomi": "Completed",
        "SourceMihon": "mangaplus",
        "PublicationYear": "2024",
        "PublicationMonth": "3",
    })

@pytest.fixture
def empty_xml_bytes():
    return make_comic_xml({tag: "" for tag in XML_FIELD_TAGS})


@pytest.fixture
def sample_csv():
    """Return a sample CSV string with header and data rows."""
    return (
        "FileName,Title,Series,Number,Summary,Writer,Genre,Web,"
        "PublishingStatusTachiyomi,SourceMihon,PublicationYear,PublicationMonth\n"
        "ch01.cbz,Chapter 1,My Series,1,Summary1,Author1,Action,https://a.com,,source1,2024,1\n"
        "ch02.cbz,Chapter 2,My Series,2,Summary2,Author2,Comedy,https://b.com,,source2,2024,2\n"
    )


@pytest.fixture
def sample_csv_no_header():
    """CSV without header row."""
    return (
        "ch01.cbz,Chapter 1,My Series,1,Summary1,Author1,Action,https://a.com,,source1,2024,1\n"
        "ch02.cbz,Chapter 2,My Series,2,Summary2,Author2,Comedy,https://b.com,,source2,2024,2\n"
    )
