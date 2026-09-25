import json
import os
import re
from datetime import timedelta
from pathlib import Path

from collector.text import article_id, iso_now, parse_iso

ORIGIN_PRIORITY = {"official": 0, "sec": 1, "other": 2}
PUBLIC_FIELDS = (
    "id", "companies", "title", "title_ja", "summary", "category",
    "url", "source", "origin", "lang", "published", "fetched",
)
_MONTH_FILE_RE = re.compile(r"^\d{4}-\d{2}\.json$")
SAME_STORY_DAYS = 3


def _timestamp(article: dict) -> str:
    return article.get("published") or article["fetched"]


def same_story(a: dict, b: dict) -> bool:
    # 同じ見出しでも公開日が大きく離れていれば別の記事（「Why IonQ Stock Is Soaring Today」など）
    pa, pb = a.get("published"), b.get("published")
    if not pa or not pb:
        return True
    return abs(parse_iso(pa) - parse_iso(pb)) <= timedelta(days=SAME_STORY_DAYS)


def dated_id(article: dict) -> str:
    return article_id(f"{article['title']} {(article.get('published') or article.get('fetched') or '')[:10]}")


def _rejection_key(ticker: str, url: str) -> str:
    return f"{ticker} {url}"


def _write_atomic(path: Path, text: str) -> None:
    # 途中で強制終了されても、書きかけのファイルが残らないようにする
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def build_manifest(by_month: dict, companies: list, now) -> dict:
    months = []
    for month in sorted(by_month, reverse=True):
        counts = {}
        for article in by_month[month]:
            for ticker in article["companies"]:
                counts[ticker] = counts.get(ticker, 0) + 1
        months.append({"month": month, "total": len(by_month[month]), "counts": counts})
    return {
        "updated": iso_now(now),
        "companies": [{"ticker": c["ticker"], "name": c["name"], "color": c["color"]} for c in companies],
        "months": months,
    }


class Store:
    def __init__(self, articles=None, rejected=None):
        self.articles: dict[str, dict] = {}
        self._url_index: dict[str, str] = {}
        self._rejected: set[str] = set(rejected or [])
        for article in articles or []:
            self.add(article)

    @classmethod
    def load(cls, data_dir: Path) -> "Store":
        articles = []
        rejected = []
        if data_dir.exists():
            for path in sorted(data_dir.iterdir()):
                if _MONTH_FILE_RE.match(path.name):
                    articles.extend(json.loads(path.read_text(encoding="utf-8")))
            rejected_path = data_dir / "rejected.json"
            if rejected_path.exists():
                rejected = json.loads(rejected_path.read_text(encoding="utf-8"))
        return cls(articles, rejected)

    def find(self, candidate: dict) -> dict | None:
        by_url = self._url_index.get(candidate["url"])
        if by_url:
            return self.articles[by_url]
        existing = self.articles.get(candidate["id"])
        if existing and not same_story(existing, candidate):
            return None
        return existing

    def allowed_companies(self, candidate: dict) -> list[str]:
        return [t for t in candidate["companies"] if _rejection_key(t, candidate["url"]) not in self._rejected]

    def is_rejected(self, candidate: dict) -> bool:
        return not self.allowed_companies(candidate)

    def merge(self, existing: dict, candidate: dict) -> bool:
        changed = False
        for ticker in candidate["companies"]:
            if _rejection_key(ticker, existing["url"]) in self._rejected:
                continue
            if ticker not in existing["companies"]:
                existing["companies"].append(ticker)
                changed = True
        if ORIGIN_PRIORITY[candidate["origin"]] < ORIGIN_PRIORITY[existing["origin"]]:
            existing.update(url=candidate["url"], source=candidate["source"], origin=candidate["origin"])
            self._url_index[candidate["url"]] = existing["id"]
            changed = True
        return changed

    def add(self, article: dict) -> None:
        clash = self.articles.get(article["id"])
        if clash and clash["url"] != article["url"] and not same_story(clash, article):
            article = {**article, "id": dated_id(article)}
        self.articles[article["id"]] = article
        self._url_index[article["url"]] = article["id"]

    def reject(self, url: str, ticker: str) -> None:
        self._rejected.add(_rejection_key(ticker, url))

    def save(self, data_dir: Path, companies: list, now) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        by_month: dict[str, list] = {}
        for article in self.articles.values():
            by_month.setdefault(_timestamp(article)[:7], []).append(article)
        for month, items in by_month.items():
            items.sort(key=lambda a: (_timestamp(a), a["id"]), reverse=True)
            # 1行1記事にして、git の差分を読みやすくする
            lines = ",\n".join(
                json.dumps({k: a[k] for k in PUBLIC_FIELDS if k in a}, ensure_ascii=False) for a in items
            )
            _write_atomic(data_dir / f"{month}.json", f"[\n{lines}\n]\n")
        _write_atomic(data_dir / "rejected.json", json.dumps(sorted(self._rejected), ensure_ascii=False, indent=0) + "\n")
        _write_atomic(data_dir / "manifest.json",
                      json.dumps(build_manifest(by_month, companies, now), ensure_ascii=False, indent=1) + "\n")
