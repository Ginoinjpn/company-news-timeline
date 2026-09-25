from datetime import datetime, timezone

from collector import sources

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
FETCHED = "2026-09-25T12:00:00Z"


def rss(*items: str) -> bytes:
    return ("<rss><channel><title>Feed Title</title>" + "".join(items) + "</channel></rss>").encode()


def item(title, link, date="Thu, 24 Sep 2026 07:00:00 GMT", source=None, desc=""):
    src = f'<source url="https://r">{source}</source>' if source else ""
    pub = f"<pubDate>{date}</pubDate>" if date else ""
    return f"<item><title>{title}</title><link>{link}</link>{pub}{src}<description>{desc}</description></item>"


def test_google_news_url_encodes_query_and_lang():
    url = sources.google_news_url("IonQ", "en", when="1d")
    assert url.startswith("https://news.google.com/rss/search?q=IonQ+when%3A1d&")
    assert url.endswith("hl=en-US&gl=US&ceid=US:en")
    assert "hl=ja&gl=JP&ceid=JP:ja" in sources.google_news_url("イオンキュー", "ja")


def test_google_suffix_is_removed_and_source_taken_from_entry():
    content = rss(item("IonQ Wins Deal - Reuters", "https://g/1", source="Reuters"))
    [c] = sources.entries_to_candidates(content, ticker="IONQ", source_name="Google News", lang="en", origin="other", fetched=FETCHED)
    assert c["title"] == "IonQ Wins Deal"
    assert c["source"] == "Reuters"
    assert c["published"] == "2026-09-24T07:00:00Z"
    assert c["companies"] == ["IONQ"]


def test_official_prefix_removed_so_ids_match_other_sources():
    official = rss(item("IonQ | IonQ Wins Deal", "https://ionq.com/news/1"))
    google = rss(item("IonQ Wins Deal - Reuters", "https://g/1", source="Reuters"))
    [a] = sources.entries_to_candidates(official, ticker="IONQ", source_name="IonQ 公式", lang="en", origin="official", fetched=FETCHED, title_prefix="IonQ | ")
    [b] = sources.entries_to_candidates(google, ticker="IONQ", source_name="Google News", lang="en", origin="other", fetched=FETCHED)
    assert a["title"] == "IonQ Wins Deal"
    assert a["id"] == b["id"]


def test_keywords_filter_and_feed_title_as_default_source():
    content = rss(item("IonQ expands", "https://q/1"), item("Rigetti news", "https://q/2"))
    out = sources.entries_to_candidates(content, ticker="IONQ", source_name=None, lang="en", origin="other", fetched=FETCHED, keywords=["IonQ"])
    assert [c["url"] for c in out] == ["https://q/1"]
    assert out[0]["source"] == "Feed Title"


def test_entries_without_title_link_or_words_are_skipped():
    content = rss(item("", "https://x/1"), item("!!!", "https://x/2"), item("Ok", ""))
    assert sources.entries_to_candidates(content, ticker="IONQ", source_name="S", lang="en", origin="other", fetched=FETCHED) == []


def test_is_recent_keeps_undated_and_drops_old():
    assert sources.is_recent({"published": None}, NOW)
    assert sources.is_recent({"published": "2026-09-23T00:00:00Z"}, NOW)
    assert not sources.is_recent({"published": "2026-09-01T00:00:00Z"}, NOW)


def test_recent_feed_specs_cover_all_sources():
    company = {
        "ticker": "IONQ", "name": "IonQ", "queries_en": ["IonQ"], "queries_ja": ["IonQ", "イオンキュー"],
        "official_rss": "https://ionq.com/news/rss.xml", "official_title_prefix": "IonQ | ",
        "industry_feeds": ["https://thequantuminsider.com/feed/"], "industry_keywords": ["IonQ"],
    }
    urls = [u for u, _ in sources.recent_feed_specs(company)]
    assert len(urls) == 8
    assert any("seekingalpha.com/api/sa/combined/IONQ.xml" in u for u in urls)
    assert any("nasdaq.com/feed/rssoutbound?symbol=IONQ" in u for u in urls)
    assert any("feeds.finance.yahoo.com" in u for u in urls)
    official = dict(sources.recent_feed_specs(company))["https://ionq.com/news/rss.xml"]
    assert official["origin"] == "official" and official["title_prefix"] == "IonQ | "


def test_fetch_recent_survives_a_failing_source_and_filters_old():
    company = {"ticker": "IONQ", "name": "IonQ", "queries_en": ["IonQ"], "queries_ja": []}
    good = rss(item("IonQ new", "https://a/1"), item("IonQ old", "https://a/2", date="Mon, 01 Jan 2024 00:00:00 GMT"), item("IonQ undated", "https://a/3", date=None))

    def fake_fetch(url):
        if "news.google.com" in url:
            raise RuntimeError("boom")
        return good

    logs = []
    out = sources.fetch_recent(company, NOW, fetch=fake_fetch, log=logs.append)
    assert sorted(c["url"] for c in out if c["source"] == "Yahoo Finance") == ["https://a/1", "https://a/3"]
    assert any("boom" in line for line in logs)
