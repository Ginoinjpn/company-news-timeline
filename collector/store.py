import json
import re
from pathlib import Path

from collector.text import iso_now

ORIGIN_PRIORITY = {"official": 0, "sec": 1, "other": 2}
PUBLIC_FIELDS = (
    "id", "companies", "title", "title_ja", "summary", "category",
    "url", "source", "origin", "lang", "published", "fetched",
)
_MONTH_FILE_RE = re.compile(r"^\d{4}-\d{2}\.json$")


def _timestamp(article: dict) -> str:
    return article.get("published") or article["fetched"]


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
        article_id = self._url_index.get(candidate["url"]) or candidate["id"]
        return self.articles.get(article_id)

    def is_rejected(self, candidate: dict) -> bool:
        return candidate["url"] in self._rejected

    def merge(self, existing: dict, candidate: dict) -> bool:
        changed = False
        for ticker in candidate["companies"]:
            if ticker not in existing["companies"]:
                existing["companies"].append(ticker)
                changed = True
        if ORIGIN_PRIORITY[candidate["origin"]] < ORIGIN_PRIORITY[existing["origin"]]:
            existing.update(url=candidate["url"], source=candidate["source"], origin=candidate["origin"])
            self._url_index[candidate["url"]] = existing["id"]
            changed = True
        return changed

    def add(self, article: dict) -> None:
        self.articles[article["id"]] = article
        self._url_index[article["url"]] = article["id"]

    def reject(self, url: str) -> None:
        self._rejected.add(url)

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
            (data_dir / f"{month}.json").write_text(f"[\n{lines}\n]\n", encoding="utf-8")
        (data_dir / "rejected.json").write_text(
            json.dumps(sorted(self._rejected), ensure_ascii=False, indent=0) + "\n", encoding="utf-8"
        )
        (data_dir / "manifest.json").write_text(
            json.dumps(build_manifest(by_month, companies, now), ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
