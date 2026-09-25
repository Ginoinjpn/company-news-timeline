from concurrent.futures import ThreadPoolExecutor

import requests
import trafilatura
from googlenewsdecoder import gnewsdecoder

from collector.sources import BROWSER_UA

BODY_LIMIT = 4000
MIN_BODY = 400


def resolve_url(url: str, decode=gnewsdecoder) -> str | None:
    # Google News の RSS のリンクは転送用なので、元の記事の URL に戻してから読む
    if "news.google.com/" not in url:
        return url
    try:
        result = decode(url, interval=1)
    except Exception:
        return None
    return result.get("decoded_url") if result.get("success") else None


def extract_body(html: str) -> str | None:
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text or len(text) < MIN_BODY:
        return None
    return text[:BODY_LIMIT]


def fetch_body(url: str, get=requests.get, resolve=resolve_url) -> tuple[str | None, str | None]:
    real = resolve(url)
    if not real:
        return None, None
    try:
        res = get(real, headers={"User-Agent": BROWSER_UA}, timeout=20)
    except Exception:
        return real, None
    if not res.ok:
        return real, None
    return real, extract_body(res.text)


def fetch_bodies(urls: list[str], fetch=fetch_body, workers: int = 4) -> dict[str, tuple]:
    unique = list(dict.fromkeys(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(unique, pool.map(fetch, unique)))
