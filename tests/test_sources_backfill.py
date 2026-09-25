from datetime import date, datetime, timezone

from collector import sources

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
COMPANY = {"ticker": "IONQ", "name": "IonQ", "queries_en": ["IonQ"], "queries_ja": ["イオンキュー"], "sec_cik": "0001824920"}


def block(rows):
    keys = ["form", "accessionNumber", "primaryDocument", "primaryDocDescription", "acceptanceDateTime", "filingDate", "items"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


RECENT_BLOCK = block([
    ("8-K", "0001824920-26-000050", "ionq-8k.htm", "8-K", "2026-09-08T11:40:15.000Z", "2026-09-08", "2.02,9.01"),
    ("4", "0001824920-26-000049", "form4.xml", "", "2026-09-07T20:00:00.000Z", "2026-09-07", ""),
    ("10-Q", "0001824920-26-000040", "ionq-10q.htm", "10-Q", "2026-08-07T10:01:33.000Z", "2026-08-07", ""),
    ("8-K", "0001824920-24-000001", "old.htm", "8-K", "2024-01-05T10:00:00.000Z", "2024-01-05", "8.01"),
])


def test_sec_candidates_filters_forms_and_dates_and_builds_urls():
    out = sources.sec_candidates([RECENT_BLOCK], COMPANY, since=date(2026, 8, 1), fetched="x")
    assert [c["form"] for c in out] == ["8-K", "10-Q"]
    first = out[0]
    assert first["url"] == "https://www.sec.gov/Archives/edgar/data/1824920/000182492026000050/ionq-8k.htm"
    assert first["published"] == "2026-09-08T11:40:15Z"
    assert first["origin"] == "sec" and first["source"] == "SEC"
    assert "2.02" in first["snippet"]
    assert first["id"] != out[1]["id"]


def test_two_8ks_with_same_description_get_different_ids():
    b = block([
        ("8-K", "0001824920-26-000050", "a.htm", "8-K", "2026-09-08T11:40:15.000Z", "2026-09-08", "8.01"),
        ("8-K", "0001824920-26-000051", "b.htm", "8-K", "2026-09-09T11:40:15.000Z", "2026-09-09", "8.01"),
    ])
    out = sources.sec_candidates([b], COMPANY, since=date(2026, 1, 1), fetched="x")
    assert len({c["id"] for c in out}) == 2


def test_fetch_sec_blocks_reads_older_files_only_when_needed():
    calls = []

    def fake_json(url):
        calls.append(url)
        if url.endswith("CIK0001824920.json"):
            return {"filings": {"recent": RECENT_BLOCK, "files": [
                {"name": "CIK0001824920-submissions-001.json", "filingFrom": "2023-01-01", "filingTo": "2025-01-01"},
                {"name": "CIK0001824920-submissions-002.json", "filingFrom": "2020-01-01", "filingTo": "2022-12-31"},
            ]}}
        return block([])

    blocks = sources.fetch_sec_blocks("0001824920", date(2024, 9, 25), fetch_json=fake_json)
    assert len(blocks) == 2
    assert calls[1] == "https://data.sec.gov/submissions/CIK0001824920-submissions-001.json"


def test_month_windows_newest_first_and_clipped():
    windows = sources.month_windows(date(2024, 9, 25), date(2024, 11, 10))
    assert windows == [
        (date(2024, 11, 1), date(2024, 11, 11)),
        (date(2024, 10, 1), date(2024, 11, 1)),
        (date(2024, 9, 25), date(2024, 10, 1)),
    ]
    assert sources.month_windows(date(2024, 12, 5), date(2025, 1, 2))[0] == (date(2025, 1, 1), date(2025, 1, 3))


def test_collect_window_splits_when_limit_reached():
    seen = []

    def fake_window(after, before):
        seen.append((after, before))
        days = (before - after).days
        return [{"n": i} for i in range(100 if days > 8 else 5)]

    out = sources.collect_window(fake_window, date(2026, 1, 1), date(2026, 2, 1))
    # 31日 → 15日 + 16日 → 7・8日 + 8・8日 の4区間に分かれ、それぞれ5件
    assert len(out) == 20
    assert seen[0] == (date(2026, 1, 1), date(2026, 2, 1))


def test_collect_window_stops_splitting_at_one_day():
    out = sources.collect_window(lambda a, b: [{"n": 1}] * 100, date(2026, 1, 1), date(2026, 1, 2))
    assert len(out) == 100


def test_in_window_uses_now_for_undated():
    assert sources.in_window({"published": "2026-09-10T00:00:00Z"}, date(2026, 9, 1), date(2026, 10, 1), NOW)
    assert not sources.in_window({"published": "2026-08-31T23:00:00Z"}, date(2026, 9, 1), date(2026, 10, 1), NOW)
    assert sources.in_window({"published": None}, date(2026, 9, 1), date(2026, 10, 1), NOW)


def test_sec_is_skipped_with_log_when_user_agent_missing(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    logs = []
    out = sources.fetch_backfill_extras(COMPANY, date(2026, 1, 1), NOW, log=logs.append)
    assert out == []
    assert any("SEC_USER_AGENT" in line for line in logs)


def test_sec_user_agent_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", " name contact@example.org \n")
    sent = {}

    class Res:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    def fake_get(url, headers, timeout):
        sent.update(headers)
        return Res()

    monkeypatch.setattr(sources.requests, "get", fake_get)
    assert sources.fetch_sec_json("https://data.sec.gov/x") == {"ok": True}
    assert sent["User-Agent"] == "name contact@example.org"


def test_fetch_backfill_google_queries_each_language_with_overlap():
    urls = []

    def fake_fetch(url):
        urls.append(url)
        return b"<rss><channel></channel></rss>"

    sources.fetch_backfill_google(COMPANY, date(2026, 8, 1), date(2026, 9, 1), NOW, fetch=fake_fetch, sleep=lambda s: None)
    assert len(urls) == 2
    assert "after:2026-07-31" in urls[0] and "before:2026-09-01" in urls[0]
    assert "hl=ja" in urls[1]
