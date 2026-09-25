import argparse
import collections
import os
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from companies import COMPANIES
from collector import article_text, sources, summarize
from collector.store import ORIGIN_PRIORITY, Store, dated_id, same_story
from collector.text import iso_now, parse_iso

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
BACKFILL_DAYS = 730
PAUSE_BETWEEN_BATCHES = 4
SAME_EVENT_DAYS = 3
REGROUP_CHUNK = 40
RESUMMARIZE_CHUNK = 20
# 本文が1件も取れないときは、サイト側ではなく実行環境の問題（Google にブロックされたなど）とみなす
MIN_BODY_ATTEMPTS = 5
MIN_RESUMMARIZE_ATTEMPTS = 10


def attach_bodies(store: Store, candidates: list[dict], body_fn, log=print) -> list[dict]:
    """記事本文を取り出して候補に付ける。本文が取れない報道記事（見出しのみ）は除外する。"""
    fetchable = [c for c in candidates if c["origin"] != "sec"]
    results = body_fn([c["url"] for c in fetchable]) if fetchable else {}
    others = [c for c in candidates if c["origin"] == "other"]
    readable = sum(1 for c in others if results.get(c["url"], (None, None))[1])
    postpone = len(others) >= MIN_BODY_ATTEMPTS and readable == 0
    if postpone:
        log(f"本文を1件も取得できなかったため、報道記事 {len(others)} 件の判定を次回に回します")
    kept, dropped = [], 0
    for c in candidates:
        if c["origin"] == "sec":
            kept.append(c)
            continue
        real, body = results.get(c["url"], (None, None))
        if body:
            resolved = {**c, "url": real or c["url"], "body": body}
            existing = store.find({**resolved, "id": ""})
            if existing:
                store.merge(existing, resolved)
            else:
                kept.append(resolved)
            continue
        if c["origin"] == "official":
            kept.append(c)
            continue
        if postpone:
            continue
        for ticker in c["companies"]:
            store.reject(c["url"], ticker)
        dropped += 1
    if others and not postpone:
        log(f"本文あり {readable} 件 / 見出ししか取れず除外 {dropped} 件")
    return kept


def _stamp(article: dict) -> str:
    return article.get("published") or article["fetched"]


def resolve_groups(n_labels, links: dict, e_labels) -> dict:
    """Gemini が返した same_as をたどり、各 N 記事がまとまる先（E 記事か、グループ代表の N 記事）を決める。"""
    n_set = set(n_labels)

    def root(label):
        seen = [label]
        current = label
        while True:
            target = (links.get(current) or "").strip()
            if target in e_labels:
                return target
            if target not in n_set:
                return current
            if target in seen:
                return min(seen)
            seen.append(target)
            current = target

    return {label: root(label) for label in n_labels}


def _primary_rank(article: dict):
    # 公式・SEC を優先し、株価だけの記事より中身のある記事を、同じなら早く出た記事を代表にする
    return (ORIGIN_PRIORITY[article["origin"]], article["category"] == "株価・市場", _stamp(article), article["id"])


def merge_same_events(store: Store, company: dict, items: list[dict], cluster_fn, log=print) -> None:
    items = [store.articles[a["id"]] for a in items if a["id"] in store.articles]
    if not items:
        return
    stamps = [_stamp(a) for a in items]
    window = timedelta(days=SAME_EVENT_DAYS)
    start = iso_now(parse_iso(min(stamps)) - window)
    end = iso_now(parse_iso(max(stamps)) + window)
    context = store.event_context(company["ticker"], start, end, exclude={a["id"] for a in items})
    try:
        links = cluster_fn(company, context, items)
    except summarize.BadResponse as e:
        log(f"{company['ticker']}: 同じ出来事の判定に失敗したため、まとめずに残します ({e})")
        return
    e_labels = {f"E{i}": a for i, a in enumerate(context)}
    n_labels = {f"N{i}": a for i, a in enumerate(items)}
    groups: dict[str, list] = {}
    for label, root in resolve_groups(list(n_labels), links, e_labels).items():
        if label != root:
            groups.setdefault(root, []).append(label)
    merged = 0
    for root, members in groups.items():
        head = e_labels.get(root) or n_labels[root]
        group = [a for a in [head] + [n_labels[m] for m in members] if a["id"] in store.articles]
        if len(group) < 2:
            continue
        primary = min(group, key=_primary_rank)
        for article in group:
            if article is not primary:
                store.absorb(primary, article)
                merged += 1
    if merged:
        log(f"{company['ticker']}: 同じ出来事の記事を {merged} 件まとめました")


def dedupe(candidates: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    url_to_id: dict[str, str] = {}
    out = []
    for c in candidates:
        key = url_to_id.get(c["url"]) or c["id"]
        existing = by_id.get(key)
        if existing is not None and not same_story(existing, c):
            c = {**c, "id": dated_id(c)}
            key = c["id"]
            existing = by_id.get(key)
        if existing is None:
            copy = {**c, "companies": list(c["companies"])}
            by_id[key] = copy
            url_to_id[c["url"]] = key
            out.append(copy)
            continue
        for ticker in c["companies"]:
            if ticker not in existing["companies"]:
                existing["companies"].append(ticker)
        if ORIGIN_PRIORITY[c["origin"]] < ORIGIN_PRIORITY[existing["origin"]]:
            existing.update(url=c["url"], source=c["source"], origin=c["origin"], snippet=c["snippet"] or existing["snippet"])
            url_to_id[c["url"]] = key
        if not existing["published"] and c["published"]:
            existing["published"] = c["published"]
    return out


def split_new(store: Store, candidates: list[dict]) -> tuple[list[dict], bool]:
    new, changed = [], False
    for c in dedupe(candidates):
        allowed = store.allowed_companies(c)
        if not allowed:
            continue
        c["companies"] = allowed
        existing = store.find(c)
        if existing:
            changed = store.merge(existing, c) or changed
        else:
            new.append(c)
    return new, changed


def judge_batch(judge_fn, company, batch, log=print):
    """解釈できない応答が返ったら半分ずつに分けて判定し直し、原因の1件だけを除外する。"""
    try:
        return summarize.apply_judgements(batch, judge_fn(company, batch))
    except summarize.BadResponse as e:
        if len(batch) == 1:
            log(f"{company['ticker']}: 判定できないため除外 {batch[0]['url']} ({e})")
            return [], batch, []
        middle = len(batch) // 2
        a1, r1, l1 = judge_batch(judge_fn, company, batch[:middle], log)
        a2, r2, l2 = judge_batch(judge_fn, company, batch[middle:], log)
        return a1 + a2, r1 + r2, l1 + l2


def process(store, candidates, judge_fn, save, company_by_ticker, log=print, pause=time.sleep, cluster_fn=None,
            body_fn=None) -> None:
    new, changed = split_new(store, candidates)
    if body_fn and new:
        new = attach_bodies(store, new, body_fn, log)
        changed = True
    if changed:
        save()
    pending: dict[str, list] = {}
    for c in new:
        pending.setdefault(c["companies"][0], []).append(c)
    first = True
    while pending:
        ticker = next(iter(pending))
        items = pending.pop(ticker)
        company = company_by_ticker[ticker]
        for start in range(0, len(items), summarize.BATCH_SIZE):
            if not first:
                pause(PAUSE_BETWEEN_BATCHES)
            first = False
            batch = items[start:start + summarize.BATCH_SIZE]
            accepted, rejected, leftover = judge_batch(judge_fn, company, batch, log)
            added = [store.add(article) for article in accepted]
            if cluster_fn and added:
                pause(PAUSE_BETWEEN_BATCHES)
                merge_same_events(store, company, added, cluster_fn, log)
            for c in rejected:
                store.reject(c["url"], ticker)
                # 複数社に関係しうる記事は、残りの会社の観点でもう一度判定する
                rest = [t for t in c["companies"] if t != ticker]
                if rest:
                    pending.setdefault(rest[0], []).append({**c, "companies": rest})
            save()
            log(f"{ticker}: 追加 {len(accepted)} / 除外 {len(rejected)} / 判定なし {len(leftover)}")


def manifest_companies(selected):
    # --ticker で1社だけ処理しても、manifest には登録済みの全社を載せる
    return COMPANIES if all(c in COMPANIES for c in selected) else selected


def run_recent(companies, judge_fn, now, data_dir=DATA_DIR, fetch_recent=sources.fetch_recent, log=print, cluster_fn=None,
               body_fn=None) -> None:
    store = Store.load(data_dir)
    save = lambda: store.save(data_dir, manifest_companies(companies), now)
    by_ticker = {c["ticker"]: c for c in companies}
    candidates = []
    for company in companies:
        found = fetch_recent(company, now)
        log(f"{company['ticker']}: 候補 {len(found)} 件")
        candidates.extend(found)
    process(store, candidates, judge_fn, save, by_ticker, log=log, cluster_fn=cluster_fn, body_fn=body_fn)
    save()


def run_regroup(companies, cluster_fn, now, data_dir=DATA_DIR, log=print, pause=time.sleep) -> None:
    """保存済みの記事を古い順に見直し、別サイトが報じた同じ出来事を1件にまとめる。"""
    store = Store.load(data_dir)
    for company in companies:
        articles = sorted((a for a in store.articles.values() if company["ticker"] in a["companies"]), key=_stamp)
        before = len(articles)
        for start in range(0, len(articles), REGROUP_CHUNK):
            chunk = [a for a in articles[start:start + REGROUP_CHUNK] if a["id"] in store.articles]
            if start:
                pause(PAUSE_BETWEEN_BATCHES)
            merge_same_events(store, company, chunk, cluster_fn, log)
            store.save(data_dir, manifest_companies(companies), now)
        after = sum(company["ticker"] in a["companies"] for a in store.articles.values())
        log(f"{company['ticker']}: {before} 件 → {after} 件")


def _first_readable(article: dict, results: dict):
    for url in [article["url"]] + [r["url"] for r in article.get("related", [])]:
        real, body = results.get(url, (None, None))
        if body:
            return url, real or url, body
    return None


def _promote(store: Store, article: dict, url: str, real: str) -> None:
    """読めるのが「ほかの報道」の方だけなら、そちらを代表のリンクにする。"""
    if url != article["url"]:
        chosen = next(r for r in article["related"] if r["url"] == url)
        old = {"source": article["source"], "url": article["url"], "title": article.get("title_ja") or article["title"]}
        article["related"] = [old] + [r for r in article["related"] if r["url"] != url]
        article["source"] = chosen["source"]
    article["url"] = real
    store.reindex(article)


def run_resummarize(companies, judge_fn, now, data_dir=DATA_DIR, body_fn=None, log=print, pause=time.sleep) -> None:
    """保存済みの記事の本文を読み直して長い要約に書き換え、本文が取れない報道記事は削除する。"""
    body_fn = body_fn or article_text.fetch_bodies
    store = Store.load(data_dir)
    save = lambda: store.save(data_dir, manifest_companies(companies), now)
    for company in companies:
        todo = sorted((a for a in store.articles.values()
                       if a["companies"][0] == company["ticker"] and a.get("basis") != "body" and a["origin"] != "sec"),
                      key=_stamp, reverse=True)
        rewritten = removed = 0
        for start in range(0, len(todo), RESUMMARIZE_CHUNK):
            chunk = [a for a in todo[start:start + RESUMMARIZE_CHUNK] if a["id"] in store.articles]
            if start:
                pause(PAUSE_BETWEEN_BATCHES)
            results = body_fn([u for a in chunk for u in [a["url"]] + [r["url"] for r in a.get("related", [])]])
            hits = {a["id"]: _first_readable(a, results) for a in chunk}
            others = [a for a in chunk if a["origin"] == "other"]
            if len(others) >= MIN_RESUMMARIZE_ATTEMPTS and not any(hits[a["id"]] for a in others):
                raise RuntimeError("本文を1件も取得できません。実行環境の問題の可能性があるため、削除せずに中断します")
            ready = []
            for a in chunk:
                hit = hits[a["id"]]
                if not hit:
                    if a["origin"] == "other":
                        store.remove(a)
                        removed += 1
                    continue
                url, real, body = hit
                _promote(store, a, url, real)
                ready.append({"id": a["id"], "companies": a["companies"], "title": a["title"], "url": a["url"],
                              "source": a["source"], "origin": a["origin"], "snippet": "", "body": body})
            if ready:
                try:
                    judgements = {j.index: j for j in judge_fn(company, ready)}
                except summarize.BadResponse as e:
                    log(f"{company['ticker']}: 要約の作り直しに失敗したため、この {len(ready)} 件は元のままにします ({e})")
                    judgements = {}
                for i, c in enumerate(ready):
                    j = judgements.get(i)
                    if j and j.summary.strip():
                        article = store.articles[c["id"]]
                        article["summary"] = j.summary.strip()
                        article["basis"] = "body"
                        rewritten += 1
            save()
            log(f"{company['ticker']}: {start + len(chunk)}/{len(todo)} 件を処理（書き換え {rewritten} / 削除 {removed}）")


def run_backfill(companies, judge_fn, now, since, data_dir=DATA_DIR, checkpoint=None, log=print, cluster_fn=None,
                 body_fn=None) -> None:
    store = Store.load(data_dir)
    save = lambda: store.save(data_dir, manifest_companies(companies), now)
    by_ticker = {c["ticker"]: c for c in companies}
    for company in companies:
        extras = sources.fetch_backfill_extras(company, since, now, log=log)
        log(f"{company['ticker']}: 公式・SEC {len(extras)} 件")
        for after, before in sources.month_windows(since, now.date()):
            month = sources.fetch_backfill_google(company, after, before, now, log=log)
            month += [c for c in extras if sources.in_window(c, after, before, now)]
            log(f"{company['ticker']} {after:%Y-%m}: 候補 {len(month)} 件")
            process(store, month, judge_fn, save, by_ticker, log=log, cluster_fn=cluster_fn, body_fn=body_fn)
            save()
            if checkpoint:
                checkpoint(f"Backfill {company['ticker']} {after:%Y-%m}")


def select_companies(ticker_arg):
    if not ticker_arg:
        return list(COMPANIES)
    wanted = [t.strip().upper() for t in ticker_arg.split(",") if t.strip()]
    by_ticker = {c["ticker"]: c for c in COMPANIES}
    missing = [t for t in wanted if t not in by_ticker]
    if missing:
        raise SystemExit(f"companies.py に登録されていないティッカーです: {', '.join(missing)}")
    return [by_ticker[t] for t in wanted]


def git_checkpoint(message: str) -> None:
    subprocess.run(["bash", "scripts/commit-data.sh", message], check=False)


def dry_run(companies, now) -> None:
    for company in companies:
        found = sources.fetch_recent(company, now)
        print(f"{company['ticker']}: {len(found)} 件")
        for source, n in collections.Counter(c["source"] for c in found).most_common():
            print(f"  {source}: {n}")
        for c in found[:5]:
            print(f"  - {c['published']} [{c['source']}] {c['title']}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="企業ニュースの収集")
    parser.add_argument("--backfill", action="store_true", help="過去分を取り込む")
    parser.add_argument("--since", type=date.fromisoformat, help="過去分の開始日（既定は730日前）")
    parser.add_argument("--ticker", help="対象のティッカー。カンマ区切りで複数指定できる（既定は全社）")
    parser.add_argument("--dry-run", action="store_true", help="取得だけ行い、件数を表示する（Gemini と保存なし）")
    parser.add_argument("--checkpoint", action="store_true", help="過去分の1か月ごとに commit-data.sh でコミットする")
    parser.add_argument("--resummarize", action="store_true", help="保存済みの記事の本文を読み直して要約を長くし、本文が取れない報道記事を削除する")
    parser.add_argument("--regroup", action="store_true", help="保存済みの記事のうち、同じ出来事を報じたものを1件にまとめ直す")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    companies = select_companies(args.ticker)
    if args.dry_run:
        dry_run(companies, now)
        return

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY が設定されていません")
    from google import genai
    client = genai.Client(api_key=api_key)
    judge_fn = lambda company, batch: summarize.judge(client, company, batch)
    cluster_fn = lambda company, context, items: summarize.find_same_events(client, company, context, items)

    body_fn = article_text.fetch_bodies

    if args.resummarize:
        run_resummarize(companies, judge_fn, now, body_fn=body_fn)
    elif args.regroup:
        run_regroup(companies, cluster_fn, now)
    elif args.backfill:
        since = args.since or (now - timedelta(days=BACKFILL_DAYS)).date()
        run_backfill(companies, judge_fn, now, since, checkpoint=git_checkpoint if args.checkpoint else None,
                     cluster_fn=cluster_fn, body_fn=body_fn)
    else:
        run_recent(companies, judge_fn, now, cluster_fn=cluster_fn, body_fn=body_fn)


if __name__ == "__main__":
    main()
