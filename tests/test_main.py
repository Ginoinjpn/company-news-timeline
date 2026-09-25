import json
from datetime import datetime, timezone

import pytest

from collector import main
from collector.store import Store
from collector.summarize import Judgement

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
IONQ = {"ticker": "IONQ", "name": "IonQ", "color": "#0077B6", "description": "d"}
NVDA = {"ticker": "NVDA", "name": "NVIDIA", "color": "#76B900", "description": "d"}
BY_TICKER = {"IONQ": IONQ, "NVDA": NVDA}


def cand(id_, url, ticker="IONQ", origin="other", published="2026-09-24T00:00:00Z", snippet=""):
    return {"id": id_, "companies": [ticker], "title": f"T {id_}", "url": url, "source": "S", "origin": origin,
            "lang": "en", "published": published, "snippet": snippet, "fetched": "2026-09-25T12:00:00Z"}


def all_relevant(company, batch):
    return [Judgement(index=i, relevant=True, title_ja=f"見出し {c['id']}", summary="s", category="その他") for i, c in enumerate(batch)]


def test_dedupe_merges_same_story_and_prefers_official():
    out = main.dedupe([
        cand("a", "https://g/1"),
        cand("a", "https://ionq.com/1", origin="official", snippet="official text"),
        cand("a", "https://g/2", ticker="NVDA"),
        cand("b", "https://g/1"),
    ])
    assert len(out) == 1
    assert out[0]["url"] == "https://ionq.com/1"
    assert out[0]["companies"] == ["IONQ", "NVDA"]
    assert out[0]["snippet"] == "official text"


def test_dedupe_fills_missing_published():
    out = main.dedupe([cand("a", "https://g/1", published=None), cand("a", "https://g/2")])
    assert out[0]["published"] == "2026-09-24T00:00:00Z"


def test_split_new_merges_known_and_skips_rejected():
    store = Store()
    store.add({**cand("a", "https://g/1"), "title_ja": "x", "summary": "", "category": "その他"})
    store.reject("https://spam/1")
    new, changed = main.split_new(store, [cand("a", "https://g/9", ticker="NVDA"), cand("s", "https://spam/1"), cand("n", "https://g/3")])
    assert [c["id"] for c in new] == ["n"]
    assert changed is True
    assert store.articles["a"]["companies"] == ["IONQ", "NVDA"]


def test_process_saves_accepted_rejects_irrelevant_and_retries_missing_next_time(tmp_path):
    store = Store()
    saves = []

    def partial(company, batch):
        return [Judgement(index=0, relevant=True, title_ja="ok", summary="s", category="その他"),
                Judgement(index=1, relevant=False, title_ja="no", summary="s", category="その他")]

    main.process(store, [cand("a", "https://g/1"), cand("b", "https://g/2"), cand("c", "https://g/3")],
                 partial, lambda: saves.append(1), BY_TICKER, log=lambda m: None, pause=lambda s: None)
    assert set(store.articles) == {"a"}
    assert store.is_rejected({"url": "https://g/2"})
    assert store.find(cand("c", "https://g/3")) is None
    assert saves


def test_process_groups_by_company_and_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(main.summarize, "BATCH_SIZE", 2)
    calls = []

    def recording(company, batch):
        calls.append((company["ticker"], len(batch)))
        return all_relevant(company, batch)

    store = Store()
    items = [cand(f"i{n}", f"https://i/{n}") for n in range(3)] + [cand("v", "https://v/1", ticker="NVDA")]
    main.process(store, items, recording, lambda: None, BY_TICKER, log=lambda m: None, pause=lambda s: None)
    assert calls == [("IONQ", 2), ("IONQ", 1), ("NVDA", 1)]
    assert len(store.articles) == 4


def test_process_keeps_earlier_batches_when_gemini_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(main.summarize, "BATCH_SIZE", 1)
    store = Store()
    data_dir = tmp_path / "data"
    responses = iter([all_relevant, None])

    def flaky(company, batch):
        fn = next(responses)
        if fn is None:
            raise json.JSONDecodeError("bad", "", 0)
        return fn(company, batch)

    with pytest.raises(json.JSONDecodeError):
        main.process(store, [cand("a", "https://g/1"), cand("b", "https://g/2")], flaky,
                     lambda: store.save(data_dir, [IONQ], NOW), BY_TICKER, log=lambda m: None, pause=lambda s: None)
    assert set(Store.load(data_dir).articles) == {"a"}


def test_run_recent_writes_manifest_even_without_news(tmp_path):
    main.run_recent([IONQ], all_relevant, NOW, tmp_path, fetch_recent=lambda company, now: [], log=lambda m: None)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["months"] == [] and manifest["updated"] == "2026-09-25T12:00:00Z"
