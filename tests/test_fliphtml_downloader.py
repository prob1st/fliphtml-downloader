"""Unit tests for the pure (network-free) parts of fliphtml_downloader."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fliphtml_downloader as fd


def test_parse_book_id():
    ref = fd.parse_book_input("ceeok/pkdn")
    assert ref.book_id == "ceeok/pkdn"
    assert ref.base_url == "https://online.fliphtml5.com/ceeok/pkdn/"


def test_parse_full_urls():
    for url in (
        "https://fliphtml5.com/hgzok/boba",
        "https://online.fliphtml5.com/hgzok/boba/",
        "https://online.fliphtml5.com/hgzok/boba/index.html",
        "https://online.fliphtml5.com/hgzok/boba/#p=3",
    ):
        ref = fd.parse_book_input(url)
        assert ref.book_id == "hgzok/boba", url
        assert ref.base_url == "https://online.fliphtml5.com/hgzok/boba/"


def test_parse_custom_domain():
    ref = fd.parse_book_input("https://books.example.com/my-catalog/")
    assert ref.book_id is None
    assert ref.base_url == "https://books.example.com/my-catalog/"


def test_sanitize_title():
    cleaned = fd.sanitize_title('A<B>:"/\\|?*  Title', "x")
    assert not any(c in cleaned for c in '<>:"/\\|?*')
    assert cleaned.startswith("A_B") and cleaned.endswith("Title")
    assert fd.sanitize_title("", "fallback") == "fallback"


def test_parse_config_text():
    cfg = fd.parse_config_text('var htmlConfig = {"meta": {"title": "T"}, "fliphtml5_pages": []};')
    assert cfg["meta"]["title"] == "T"


def test_decode_plain_pages():
    pages = [{"n": ["a.webp"]}, {"n": ["b.webp"]}]
    assert fd.decode_pages_value(session=None, value=pages) == pages
    assert fd.decode_pages_value(session=None, value='[{"n": ["a.webp"]}]') == [{"n": ["a.webp"]}]


def test_page_filename():
    assert fd.page_filename({"n": ["abc.webp"]}) == "abc.webp"
    assert fd.page_filename({"n": "abc.jpg"}) == "abc.jpg"
    assert fd.page_filename("direct.jpg") == "direct.jpg"
    assert fd.page_filename({"x": 1}) is None


def test_candidate_urls_fallbacks():
    urls = fd.candidate_urls("https://online.fliphtml5.com/a/b/", "abc.webp", "large")
    assert urls[0] == "https://online.fliphtml5.com/a/b/files/large/abc.webp"
    assert "https://online.fliphtml5.com/a/b/files/large/abc.jpg" in urls
    assert "https://online.fliphtml5.com/a/b/files/medium/abc.webp" in urls
    assert "https://online.fliphtml5.com/a/b/files/thumb/abc.webp" in urls
    assert len(urls) == len(set(urls))  # no duplicates


def test_get_title():
    assert fd.get_title({"meta": {"title": "My Book"}}, "fb") == "My Book"
    assert fd.get_title({}, "fb") == "fb"


def test_looks_like_image():
    assert fd.looks_like_image(b"\xff\xd8\xff" + b"\x00" * 200)
    assert fd.looks_like_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    assert fd.looks_like_image(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 200)
    assert not fd.looks_like_image(b"<html>not found</html>" + b" " * 200)
    assert not fd.looks_like_image(b"")
