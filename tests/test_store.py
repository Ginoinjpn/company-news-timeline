import json
from datetime import datetime, timezone

from collector.store import Store

NOW = datetime(2026, 9, 25, 13, 0, 0, tzinfo=timezone.utc)
COMPANIES = [
    {"ticker": "IONQ", "name": "IonQ", "color": "#0077B6"},
    {"ticker": "NVDA", "name": "NVIDIA", "color": "#76B900"},
]


def make_article(id_, published, companies=("IONQ",), url=None, origin="other", fetched="2026-09-25T13:00:00Z"):
    return {
        "id": id_, "companies": list(companies), "title": f"Title {id_}", "title_ja": f"見出し {id_}",
        "summary": "要約", "category": "その他", "url": url or f"https://example.com/{id_}",
        "source": "Reuters", "origin": origin, "lang": "en", "published": published, "fetched": fetched,
    }


def test_load_from_missing_directory_is_empty(tmp_path):
    store = Store.load(tmp_path / "nope")
    assert store.articles == {}


def test_save_splits_by_month_sorted_newest_first(tmp_path):
    store = Store()
    store.add(make_article("a", "2026-09-01T00:00:00Z"))
    store.add(make_article("b", "2026-09-20T00:00:00Z"))
    store.add(make_article("c", "2026-08-31T23:59:59Z"))
    store.save(tmp_path, COMPANIES, NOW)
    sept = json.loads((tmp_path / "2026-09.json").read_text(encoding="utf-8"))
    aug = json.loads((tmp_path / "2026-08.json").read_text(encoding="utf-8"))
    assert [a["id"] for a in sept] == ["b", "a"]
    assert [a["id"] for a in aug] == ["c"]


def test_undated_article_goes_to_fetched_month(tmp_path):
    store = Store()
    store.add(make_article("u", None, fetched="2026-07-10T00:00:00Z"))
    store.save(tmp_path, COMPANIES, NOW)
    assert (tmp_path / "2026-07.json").exists()


def test_roundtrip_keeps_articles_and_rejected(tmp_path):
    store = Store()
    store.add(make_article("a", "2026-09-01T00:00:00Z"))
    store.reject("https://spam.example/1")
    store.save(tmp_path, COMPANIES, NOW)
    loaded = Store.load(tmp_path)
    assert set(loaded.articles) == {"a"}
    assert loaded.is_rejected({"url": "https://spam.example/1"})


def test_find_by_url_or_id():
    store = Store()
    store.add(make_article("a", "2026-09-01T00:00:00Z", url="https://x/1"))
    assert store.find({"id": "zzz", "url": "https://x/1"})["id"] == "a"
    assert store.find({"id": "a", "url": "https://other"})["id"] == "a"
    assert store.find({"id": "zzz", "url": "https://other"}) is None


def test_merge_adds_company_and_prefers_official_url():
    store = Store()
    existing = make_article("a", "2026-09-01T00:00:00Z", url="https://news.google.com/x")
    store.add(existing)
    candidate = {"id": "a", "companies": ["NVDA"], "url": "https://ionq.com/news/x", "source": "IonQ 公式", "origin": "official"}
    assert store.merge(existing, candidate) is True
    assert existing["companies"] == ["IONQ", "NVDA"]
    assert existing["url"] == "https://ionq.com/news/x"
    assert store.find({"id": "nope", "url": "https://ionq.com/news/x"}) is existing
    assert store.merge(existing, candidate) is False


def test_manifest_counts_each_company(tmp_path):
    store = Store()
    store.add(make_article("a", "2026-09-01T00:00:00Z", companies=("IONQ", "NVDA")))
    store.add(make_article("b", "2026-09-02T00:00:00Z"))
    store.save(tmp_path, COMPANIES, NOW)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["updated"] == "2026-09-25T13:00:00Z"
    assert manifest["companies"][0] == {"ticker": "IONQ", "name": "IonQ", "color": "#0077B6"}
    assert manifest["months"] == [{"month": "2026-09", "total": 2, "counts": {"IONQ": 2, "NVDA": 1}}]
