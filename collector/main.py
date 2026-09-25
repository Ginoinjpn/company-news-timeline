import argparse
import collections
import os
import subprocess
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from companies import COMPANIES
from collector import sources, summarize
from collector.store import ORIGIN_PRIORITY, Store, dated_id, same_story

DATA_DIR = Path(__file__).resolve().parent.parent / "docs" / "data"
BACKFILL_DAYS = 730
PAUSE_BETWEEN_BATCHES = 4


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


def process(store, candidates, judge_fn, save, company_by_ticker, log=print, pause=time.sleep) -> None:
    new, changed = split_new(store, candidates)
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
            for article in accepted:
                store.add(article)
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


def run_recent(companies, judge_fn, now, data_dir=DATA_DIR, fetch_recent=sources.fetch_recent, log=print) -> None:
    store = Store.load(data_dir)
    save = lambda: store.save(data_dir, manifest_companies(companies), now)
    by_ticker = {c["ticker"]: c for c in companies}
    candidates = []
    for company in companies:
        found = fetch_recent(company, now)
        log(f"{company['ticker']}: 候補 {len(found)} 件")
        candidates.extend(found)
    process(store, candidates, judge_fn, save, by_ticker, log=log)
    save()


def run_backfill(companies, judge_fn, now, since, data_dir=DATA_DIR, checkpoint=None, log=print) -> None:
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
            process(store, month, judge_fn, save, by_ticker, log=log)
            save()
            if checkpoint:
                checkpoint(f"Backfill {company['ticker']} {after:%Y-%m}")


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
    parser.add_argument("--ticker", help="対象のティッカー（既定は全社）")
    parser.add_argument("--dry-run", action="store_true", help="取得だけ行い、件数を表示する（Gemini と保存なし）")
    parser.add_argument("--checkpoint", action="store_true", help="過去分の1か月ごとに commit-data.sh でコミットする")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    companies = [c for c in COMPANIES if not args.ticker or c["ticker"] == args.ticker.upper()]
    if not companies:
        raise SystemExit(f"companies.py に登録されていないティッカーです: {args.ticker}")
    if args.dry_run:
        dry_run(companies, now)
        return

    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY が設定されていません")
    from google import genai
    client = genai.Client(api_key=api_key)
    judge_fn = lambda company, batch: summarize.judge(client, company, batch)

    if args.backfill:
        since = args.since or (now - timedelta(days=BACKFILL_DAYS)).date()
        run_backfill(companies, judge_fn, now, since, checkpoint=git_checkpoint if args.checkpoint else None)
    else:
        run_recent(companies, judge_fn, now)


if __name__ == "__main__":
    main()
