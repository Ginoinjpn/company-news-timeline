import calendar
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TAG_RE = re.compile(r"<[^>]+>")
_NON_WORD_RE = re.compile(r"[^\w]+")


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).lower()
    return " ".join(_NON_WORD_RE.sub(" ", text).split())


def article_id(key: str) -> str:
    return hashlib.sha1(normalize_title(key).encode("utf-8")).hexdigest()[:12]


def strip_html(text: str | None, limit: int = 500) -> str:
    plain = html.unescape(_TAG_RE.sub(" ", text or ""))
    return " ".join(plain.split())[:limit]


def struct_to_iso(parsed) -> str | None:
    # feedparser の *_parsed は UTC なので mktime（ローカル時刻扱い）ではなく timegm を使う
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc).strftime(ISO_FORMAT)


def iso_now(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime(ISO_FORMAT)


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, ISO_FORMAT).replace(tzinfo=timezone.utc)
