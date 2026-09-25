from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

import feedparser
import requests

from collector.text import article_id, iso_now, normalize_title, parse_iso, strip_html, struct_to_iso

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
RECENT_DAYS = 3


def google_news_url(query, lang, when=None, after=None, before=None) -> str:
    q = query
    if when:
        q += f" when:{when}"
    if after:
        q += f" after:{after.isoformat()}"
    if before:
        q += f" before:{before.isoformat()}"
    tail = "hl=ja&gl=JP&ceid=JP:ja" if lang == "ja" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&{tail}"


def fetch_bytes(url: str, ua: str = BROWSER_UA) -> bytes:
    res = requests.get(url, headers={"User-Agent": ua}, timeout=30)
    res.raise_for_status()
    return res.content


def entries_to_candidates(content, *, ticker, source_name, lang, origin, fetched, title_prefix="", keywords=None) -> list[dict]:
    feed = feedparser.parse(content)
    default_source = source_name or feed.feed.get("title") or "Unknown"
    out = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        source = (entry.get("source") or {}).get("title") or default_source
        suffix = f" - {source}"
        if title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
        if title_prefix and title.startswith(title_prefix):
            title = title[len(title_prefix):].strip()
        if not normalize_title(title):
            continue
        snippet = strip_html(entry.get("summary"))
        if keywords and not any(k.lower() in f"{title} {snippet}".lower() for k in keywords):
            continue
        out.append({
            "id": article_id(title),
            "companies": [ticker],
            "title": title,
            "url": link,
            "source": source,
            "origin": origin,
            "lang": lang,
            "published": struct_to_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
            "snippet": snippet,
            "fetched": fetched,
        })
    return out


def is_recent(candidate: dict, now: datetime, days: int = RECENT_DAYS) -> bool:
    if not candidate["published"]:
        return True
    return parse_iso(candidate["published"]) >= now - timedelta(days=days)


def recent_feed_specs(company: dict) -> list[tuple[str, dict]]:
    ticker = company["ticker"]
    other = {"lang": "en", "origin": "other"}
    specs = []
    for query in company["queries_en"]:
        specs.append((google_news_url(query, "en", when="1d"), {"source_name": "Google News", **other}))
    for query in company["queries_ja"]:
        specs.append((google_news_url(query, "ja", when="1d"), {"source_name": "Google News", "lang": "ja", "origin": "other"}))
    specs.append((f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US", {"source_name": "Yahoo Finance", **other}))
    specs.append((f"https://seekingalpha.com/api/sa/combined/{ticker}.xml", {"source_name": "Seeking Alpha", **other}))
    specs.append((f"https://www.nasdaq.com/feed/rssoutbound?symbol={ticker}", {"source_name": "Nasdaq", **other}))
    if company.get("official_rss"):
        specs.append((company["official_rss"], {
            "source_name": f"{company['name']} 公式", "lang": "en", "origin": "official",
            "title_prefix": company.get("official_title_prefix", ""),
        }))
    for url in company.get("industry_feeds", []):
        specs.append((url, {"source_name": None, **other, "keywords": company.get("industry_keywords") or [company["name"]]}))
    return specs


def fetch_recent(company: dict, now: datetime, fetch=fetch_bytes, fetch_json=None, log=print) -> list[dict]:
    fetched = iso_now(now)
    out = []
    for url, kwargs in recent_feed_specs(company):
        try:
            candidates = entries_to_candidates(fetch(url), ticker=company["ticker"], fetched=fetched, **kwargs)
        except Exception as e:
            log(f"取得失敗 {url}: {e}")
            continue
        out.extend(c for c in candidates if is_recent(c, now))
    return out
