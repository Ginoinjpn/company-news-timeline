import time
from datetime import datetime, timezone

from collector.text import article_id, iso_now, normalize_title, parse_iso, strip_html, struct_to_iso


def test_normalize_title_ignores_case_symbols_and_width():
    assert normalize_title("IonQ Announces: NEW  System!") == "ionq announces new system"
    assert normalize_title("ＩｏｎＱが新システム") == normalize_title("IonQが新システム")


def test_article_id_matches_for_equivalent_titles():
    assert article_id("IonQ Announces New System") == article_id("ionq announces new system!!")
    assert len(article_id("x")) == 12


def test_strip_html_removes_tags_and_entities_and_limits_length():
    assert strip_html('<a href="x">IonQ</a>&nbsp;<font>Reuters</font>') == "IonQ Reuters"
    assert len(strip_html("a" * 900)) == 500
    assert strip_html(None) == ""


def test_struct_to_iso_treats_struct_as_utc():
    parsed = time.strptime("2024-10-03 07:00:00", "%Y-%m-%d %H:%M:%S")
    assert struct_to_iso(parsed) == "2024-10-03T07:00:00Z"
    assert struct_to_iso(None) is None


def test_iso_roundtrip():
    now = datetime(2026, 9, 25, 13, 5, 0, tzinfo=timezone.utc)
    assert iso_now(now) == "2026-09-25T13:05:00Z"
    assert parse_iso("2026-09-25T13:05:00Z") == now
