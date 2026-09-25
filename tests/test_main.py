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
    store.reject("https://spam/1", "IONQ")
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
    assert store.is_rejected({"url": "https://g/2", "companies": ["IONQ"]})
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


def test_one_unparseable_item_does_not_block_the_rest(monkeypatch):
    monkeypatch.setattr(main.summarize, "BATCH_SIZE", 4)

    def poisoned(company, batch):
        if any(c["id"] == "p" for c in batch):
            raise main.summarize.BadResponse("blocked")
        return all_relevant(company, batch)

    store = Store()
    items = [cand("a", "https://g/1"), cand("p", "https://g/2"), cand("b", "https://g/3"), cand("c", "https://g/4")]
    main.process(store, items, poisoned, lambda: None, BY_TICKER, log=lambda m: None, pause=lambda s: None)
    assert set(store.articles) == {"a", "b", "c"}
    assert store.is_rejected({"url": "https://g/2", "companies": ["IONQ"]})


def test_irrelevant_for_first_company_is_judged_for_the_next():
    seen = []

    def judge(company, batch):
        seen.append(company["ticker"])
        return [Judgement(index=i, relevant=company["ticker"] == "NVDA", title_ja="t", summary="s", category="その他")
                for i, _ in enumerate(batch)]

    store = Store()
    both = main.dedupe([cand("x", "https://g/1"), cand("x", "https://g/1", ticker="NVDA")])
    main.process(store, both, judge, lambda: None, BY_TICKER, log=lambda m: None, pause=lambda s: None)
    assert seen == ["IONQ", "NVDA"]
    assert store.articles["x"]["companies"] == ["NVDA"]
    new, _ = main.split_new(store, [cand("x", "https://g/1")])
    assert new == [] and store.articles["x"]["companies"] == ["NVDA"]


def test_transient_failure_stops_the_run_but_keeps_saved_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(main.summarize, "BATCH_SIZE", 1)
    store = Store()
    data_dir = tmp_path / "data"
    calls = iter([all_relevant, None])

    def flaky(company, batch):
        fn = next(calls)
        if fn is None:
            raise RuntimeError("503 after retries")
        return fn(company, batch)

    with pytest.raises(RuntimeError):
        main.process(store, [cand("a", "https://g/1"), cand("b", "https://g/2")], flaky,
                     lambda: store.save(data_dir, [IONQ], NOW), BY_TICKER, log=lambda m: None, pause=lambda s: None)
    assert set(Store.load(data_dir).articles) == {"a"}


def test_dedupe_keeps_recurring_headlines_from_different_weeks_apart():
    out = main.dedupe([cand("same", "https://fool/1", published="2026-06-01T00:00:00Z"),
                       cand("same", "https://fool/2", published="2026-09-20T00:00:00Z")])
    assert len(out) == 2 and out[0]["id"] != out[1]["id"]


def test_select_companies_accepts_comma_separated_tickers():
    assert [c["ticker"] for c in main.select_companies("jmia, 6965")] == ["JMIA", "6965"]
    assert len(main.select_companies(None)) == len(main.COMPANIES)
    with pytest.raises(SystemExit):
        main.select_companies("NOPE")


def stored(id_, published, origin="other", category="その他", ticker="IONQ", source="S"):
    return {"id": id_, "companies": [ticker], "title": f"T {id_}", "title_ja": f"見出し {id_}", "summary": "s",
            "category": category, "url": f"https://x/{id_}", "source": source, "origin": origin, "lang": "en",
            "published": published, "fetched": published}


def test_resolve_groups_follows_chains_and_breaks_cycles():
    roots = main.resolve_groups(["N0", "N1", "N2", "N3", "N4"],
                                {"N0": "", "N1": "N0", "N2": "N1", "N3": "N4", "N4": "N3"}, {"E0"})
    assert roots["N1"] == "N0" and roots["N2"] == "N0"
    assert roots["N3"] == roots["N4"]
    assert main.resolve_groups(["N0"], {"N0": "E0"}, {"E0"}) == {"N0": "E0"}
    assert main.resolve_groups(["N0"], {"N0": "E9"}, {"E0"}) == {"N0": "N0"}


def test_merge_same_events_folds_new_article_into_existing_one():
    store = Store()
    old = stored("old", "2026-09-24T00:00:00Z", source="Reuters")
    new = stored("new", "2026-09-24T05:00:00Z", source="Yahoo")
    store.add(old)
    store.add(new)
    main.merge_same_events(store, IONQ, [new], lambda company, context, items: {"N0": "E0"}, log=lambda m: None)
    assert set(store.articles) == {"old"}
    assert store.articles["old"]["related"][0]["source"] == "Yahoo"


def test_merge_same_events_prefers_official_then_non_market_then_earliest():
    store = Store()
    items = [stored("m", "2026-09-24T01:00:00Z", category="株価・市場"),
             stored("o", "2026-09-24T03:00:00Z", origin="official"),
             stored("e", "2026-09-24T00:00:00Z")]
    for a in items:
        store.add(a)
    main.merge_same_events(store, IONQ, items, lambda c, ctx, its: {"N0": "N2", "N1": "N2", "N2": ""}, log=lambda m: None)
    assert set(store.articles) == {"o"}
    items2 = [stored("m2", "2026-09-24T01:00:00Z", category="株価・市場"), stored("e2", "2026-09-24T02:00:00Z")]
    for a in items2:
        store.add(a)
    main.merge_same_events(store, IONQ, items2, lambda c, ctx, its: {"N0": "N1"}, log=lambda m: None)
    assert "e2" in store.articles and "m2" not in store.articles


def test_merge_same_events_keeps_articles_when_gemini_output_is_bad():
    store = Store()
    a, b = stored("a", "2026-09-24T00:00:00Z"), stored("b", "2026-09-24T01:00:00Z")
    store.add(a)
    store.add(b)

    def bad(company, context, items):
        raise main.summarize.BadResponse("x")

    main.merge_same_events(store, IONQ, [a, b], bad, log=lambda m: None)
    assert set(store.articles) == {"a", "b"}


def test_process_merges_cross_site_reports_of_the_same_event():
    store = Store()
    store.add(stored("known", "2026-09-24T00:00:00Z", source="Reuters"))

    def cluster(company, context, items):
        assert [a["id"] for a in context] == ["known"]
        return {"N0": "E0", "N1": "E0"}

    main.process(store, [cand("x", "https://y/1"), cand("y", "https://y/2")], all_relevant, lambda: None, BY_TICKER,
                 log=lambda m: None, pause=lambda s: None, cluster_fn=cluster)
    assert set(store.articles) == {"known"}
    assert len(store.articles["known"]["related"]) == 2
    new, _ = main.split_new(store, [cand("zz", "https://y/2")])
    assert new == []


def test_run_regroup_collapses_existing_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "REGROUP_CHUNK", 2)
    store = Store()
    for a in [stored("a", "2026-09-20T00:00:00Z"), stored("b", "2026-09-20T02:00:00Z"), stored("c", "2026-09-20T04:00:00Z")]:
        store.add(a)
    store.save(tmp_path, [IONQ], NOW)

    def cluster(company, context, items):
        labels = {f"E{i}": a["id"] for i, a in enumerate(context)} | {f"N{i}": a["id"] for i, a in enumerate(items)}
        first = min(labels, key=lambda l: labels[l])
        return {l: (first if l != first else "") for l in labels if l.startswith("N")}

    main.run_regroup([IONQ], cluster, NOW, tmp_path, log=lambda m: None, pause=lambda s: None)
    loaded = Store.load(tmp_path)
    assert set(loaded.articles) == {"a"}
    assert {r["url"] for r in loaded.articles["a"]["related"]} == {"https://x/b", "https://x/c"}


def fake_bodies(mapping):
    return lambda urls: {u: mapping.get(u, (u, None)) for u in urls}


def test_attach_bodies_drops_headline_only_news_and_keeps_primary_sources():
    store = Store()
    items = [cand("a", "https://g/a"), cand("b", "https://g/b"), cand("o", "https://ionq.com/o", origin="official"),
             {**cand("s", "https://sec/s", origin="sec"), "form": "8-K"}] + [cand(f"x{i}", f"https://g/x{i}") for i in range(3)]
    bodies = fake_bodies({"https://g/a": ("https://real/a", "本文A"), "https://ionq.com/o": ("https://ionq.com/o", "公式本文")})
    kept = main.attach_bodies(store, items, bodies, log=lambda m: None)
    by_id = {c["id"]: c for c in kept}
    assert set(by_id) == {"a", "o", "s"}
    assert by_id["a"]["url"] == "https://real/a" and by_id["a"]["body"] == "本文A"
    assert by_id["o"]["body"] == "公式本文" and "body" not in by_id["s"]
    assert store.is_rejected({"url": "https://g/b", "companies": ["IONQ"]})


def test_attach_bodies_postpones_everything_when_no_body_can_be_fetched():
    store = Store()
    items = [cand(f"x{i}", f"https://g/x{i}") for i in range(6)] + [cand("o", "https://ionq.com/o", origin="official")]
    logs = []
    kept = main.attach_bodies(store, items, fake_bodies({}), log=logs.append)
    assert [c["id"] for c in kept] == ["o"]
    assert not store.is_rejected({"url": "https://g/x0", "companies": ["IONQ"]})
    assert logs


def test_process_passes_bodies_to_judge_and_saves_basis():
    store = Store()
    seen = []

    def judge(company, batch):
        seen.extend(c.get("body") for c in batch)
        return all_relevant(company, batch)

    main.process(store, [cand("a", "https://g/a"), cand("b", "https://g/b")], judge, lambda: None, BY_TICKER,
                 log=lambda m: None, pause=lambda s: None, body_fn=fake_bodies({"https://g/a": ("https://real/a", "本文")}))
    assert seen == ["本文"]
    assert store.articles["a"]["basis"] == "body" and store.articles["a"]["url"] == "https://real/a"
    assert "b" not in store.articles


def test_run_resummarize_rewrites_deletes_and_promotes_readable_related(tmp_path):
    store = Store()
    readable = stored("r", "2026-09-20T00:00:00Z")
    promoted = {**stored("p", "2026-09-20T01:00:00Z"), "related": [{"source": "Yahoo", "url": "https://yahoo/p", "title": "y"}]}
    dropped = {**stored("d", "2026-09-20T02:00:00Z"), "related": [{"source": "SA", "url": "https://sa/d", "title": "s"}]}
    filing = {**stored("s", "2026-09-20T03:00:00Z", origin="sec")}
    for a in (readable, promoted, dropped, filing):
        store.add(a)
    store.add({**stored("done", "2026-09-20T04:00:00Z"), "basis": "body"})
    for a in [stored(f"ok{i}", f"2026-09-21T0{i}:00:00Z") for i in range(3)]:
        store.add(a)
    store.save(tmp_path, [IONQ], NOW)
    bodies = fake_bodies({"https://x/r": ("https://x/r", "本文R"), "https://yahoo/p": ("https://yahoo/p", "本文P"),
                          **{f"https://x/ok{i}": (f"https://x/ok{i}", "本文") for i in range(3)}})
    calls = []

    def judge(company, batch):
        calls.append([c["id"] for c in batch])
        return [Judgement(index=i, relevant=True, title_ja="t", summary=f"長い要約 {c['id']}", category="その他") for i, c in enumerate(batch)]

    main.run_resummarize([IONQ], judge, NOW, tmp_path, body_fn=bodies, log=lambda m: None, pause=lambda s: None)
    loaded = Store.load(tmp_path)
    assert "d" not in loaded.articles and "done" in loaded.articles and "s" in loaded.articles
    assert loaded.articles["r"]["summary"] == "長い要約 r" and loaded.articles["r"]["basis"] == "body"
    assert loaded.articles["r"]["title_ja"] == "見出し r"
    p = loaded.articles["p"]
    assert p["url"] == "https://yahoo/p" and p["source"] == "Yahoo" and p["summary"] == "長い要約 p"
    assert {r["url"] for r in p["related"]} == {"https://x/p"}
    assert loaded.articles["s"]["summary"] == "s"
    assert loaded.is_rejected({"url": "https://sa/d", "companies": ["IONQ"]})
    assert all("done" not in batch for batch in calls)


def test_run_resummarize_stops_without_deleting_when_nothing_is_readable(tmp_path):
    store = Store()
    for i in range(12):
        store.add(stored(f"a{i}", f"2026-09-20T{i:02d}:00:00Z"))
    store.save(tmp_path, [IONQ], NOW)
    with pytest.raises(RuntimeError):
        main.run_resummarize([IONQ], all_relevant, NOW, tmp_path, body_fn=fake_bodies({}), log=lambda m: None, pause=lambda s: None)
    assert len(Store.load(tmp_path).articles) == 12
