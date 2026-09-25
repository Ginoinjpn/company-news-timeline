import os
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

import feedparser
import requests

from collector.text import article_id, iso_now, normalize_title, parse_iso, strip_html, struct_to_iso

BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
RECENT_DAYS = 3
SEC_FORMS = {"8-K", "10-Q", "10-K", "S-1", "DEF 14A", "6-K", "20-F", "F-1"}
GOOGLE_LIMIT = 100


def google_news_url(query, lang, when=None, after=None, before=None) -> str:
    q = query
    if when:
        q += f" when:{when}"
    if after:
        q += f" after:{after.isoformat()}"
    if before:
        q += f" before:{before.isoformat()}"
    tail = "hl=ja&gl=JP&ceid=JP:ja" if lang == "ja" else "hl=en-US&gl=US&ceid=US:en"
    # ":" を %3A にすると when:/after:/before: が演算子として解釈されず、英語版は0件になる
    return f"https://news.google.com/rss/search?q={quote_plus(q, safe=':')}&{tail}"


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
    if company.get("us_listed", True):
        specs.append((f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US", {"source_name": "Yahoo Finance", **other}))
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
    if company.get("sec_cik"):
        since = (now - timedelta(days=RECENT_DAYS)).date()
        try:
            blocks = fetch_sec_blocks(company["sec_cik"], since, fetch_json or fetch_sec_json)
            out.extend(sec_candidates(blocks, company, since, fetched))
        except Exception as e:
            log(f"SEC 取得失敗 {company['ticker']}: {e}")
    return out


class MissingSecUserAgent(RuntimeError):
    pass


def fetch_sec_json(url: str) -> dict:
    # SEC は連絡先メールを含む User-Agent を必須とし、noreply アドレスは拒否する。
    # 公開リポジトリに連絡先を書かないよう、Secret から渡す。
    user_agent = (os.environ.get("SEC_USER_AGENT") or "").strip()
    if not user_agent:
        raise MissingSecUserAgent("SEC_USER_AGENT が未設定のため SEC の取得を飛ばします")
    res = requests.get(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    res.raise_for_status()
    return res.json()


def fetch_sec_blocks(cik: str, since: date, fetch_json=fetch_sec_json) -> list[dict]:
    data = fetch_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    blocks = [data["filings"]["recent"]]
    for older in data["filings"].get("files", []):
        if older["filingTo"] >= since.isoformat():
            blocks.append(fetch_json(f"https://data.sec.gov/submissions/{older['name']}"))
    return blocks


def sec_candidates(blocks: list[dict], company: dict, since: date, fetched: str) -> list[dict]:
    cik_number = int(company["sec_cik"])
    out = []
    for b in blocks:
        rows = zip(b["form"], b["accessionNumber"], b["primaryDocument"], b["primaryDocDescription"],
                   b["acceptanceDateTime"], b["filingDate"], b.get("items") or [""] * len(b["form"]))
        for form, accession, document, description, accepted, filed, items in rows:
            if form not in SEC_FORMS:
                continue
            published = f"{accepted[:19]}Z" if accepted else f"{filed}T00:00:00Z"
            if published[:10] < since.isoformat():
                continue
            detail = description if description and description.upper() != form else ""
            title = f"{company['name']} が {form} を提出" + (f"（{detail}）" if detail else "")
            out.append({
                "id": article_id(f"sec {accession}"),
                "companies": [company["ticker"]],
                "title": title,
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik_number}/{accession.replace('-', '')}/{document}",
                "source": "SEC",
                "origin": "sec",
                "lang": "ja",
                "published": published,
                "snippet": f"Form {form}. Items: {items or 'なし'}. {description or ''}".strip(),
                "fetched": fetched,
                "form": form,
            })
    return out


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows = []
    current = date(start.year, start.month, 1)
    stop = end + timedelta(days=1)
    while current < stop:
        following = date(current.year + current.month // 12, current.month % 12 + 1, 1)
        windows.append((max(current, start), min(following, stop)))
        current = following
    return windows[::-1]


def collect_window(fetch_window, after: date, before: date, limit: int = GOOGLE_LIMIT) -> list[dict]:
    items = fetch_window(after, before)
    if len(items) < limit or (before - after).days <= 1:
        return items
    middle = after + (before - after) // 2
    return collect_window(fetch_window, after, middle, limit) + collect_window(fetch_window, middle, before, limit)


def in_window(candidate: dict, after: date, before: date, now: datetime) -> bool:
    day = (candidate["published"] or iso_now(now))[:10]
    return after.isoformat() <= day < before.isoformat()


def fetch_backfill_google(company, after, before, now, fetch=fetch_bytes, sleep=time.sleep, log=print) -> list[dict]:
    fetched = iso_now(now)
    out = []
    for lang, queries in (("en", company["queries_en"]), ("ja", company["queries_ja"])):
        for query in queries:
            def window(a, b, query=query, lang=lang):
                sleep(1.5)
                # 境界の日の記事を落とさないよう1日重ねて検索する（重複は後で除く）
                url = google_news_url(query, lang, after=a - timedelta(days=1), before=b)
                try:
                    content = fetch(url)
                except Exception as e:
                    log(f"取得失敗 {url}: {e}")
                    return []
                return entries_to_candidates(content, ticker=company["ticker"], source_name="Google News",
                                             lang=lang, origin="other", fetched=fetched)
            out.extend(collect_window(window, after, before))
    return [c for c in out if in_window(c, after, before, now)]


def fetch_backfill_extras(company, since, now, fetch=fetch_bytes, fetch_json=fetch_sec_json, log=print) -> list[dict]:
    fetched = iso_now(now)
    out = []
    if company.get("official_rss"):
        try:
            out.extend(entries_to_candidates(
                fetch(company["official_rss"]), ticker=company["ticker"], source_name=f"{company['name']} 公式",
                lang="en", origin="official", fetched=fetched, title_prefix=company.get("official_title_prefix", "")))
        except Exception as e:
            log(f"取得失敗 {company['official_rss']}: {e}")
    if company.get("sec_cik"):
        try:
            out.extend(sec_candidates(fetch_sec_blocks(company["sec_cik"], since, fetch_json), company, since, fetched))
        except Exception as e:
            log(f"SEC 取得失敗 {company['ticker']}: {e}")
    return out
