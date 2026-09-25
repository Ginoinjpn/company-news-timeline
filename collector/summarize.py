import json
import time

from google.genai import errors, types
from pydantic import BaseModel, ValidationError

MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 40
CATEGORIES = ("決算・業績", "提携・契約", "製品・技術", "買収・資金調達", "経営・人事", "株価・市場", "その他")
RETRY_CODES = (429, 500, 503)
MAX_RETRIES = 3


class BadResponse(Exception):
    """Gemini の応答を解釈できない（安全フィルタで空、JSON 不正、形式違い）。"""


class Judgement(BaseModel):
    index: int
    relevant: bool
    title_ja: str
    summary: str
    category: str


def build_prompt(company: dict, items: list[dict]) -> str:
    lines = "\n".join(
        f"{i}. [{c['source']}] {c['title']}\n   概要: {c['snippet'] or 'なし'}" for i, c in enumerate(items)
    )
    return f"""あなたは米国株ニュースの編集者です。以下は「{company['name']}」（ティッカー: {company['ticker']}、{company['description']}）に関係する可能性があるニュースの見出しと概要です。

各記事について次の項目を作ってください。
- relevant: この会社が記事の主題か、内容のある形で取り上げられていれば true。同名の別物、銘柄の一覧に名前が並ぶだけの記事、この会社に触れない市場全体のまとめは false。
- title_ja: 見出しの自然な日本語訳。日本語の見出しはそのまま。
- summary: 見出しと概要から分かる事実だけで書いた日本語の要約（1〜2文）。本文に書かれていそうな内容を推測で足さないこと。SEC の提出書類は、書類の種類と Items 番号から分かる内容（例: 2.02 は決算発表、5.02 は役員の異動）を説明すること。
- category: 次のうち1つ: {' / '.join(CATEGORIES)}

{lines}

すべての記事について、index（上の番号）・relevant・title_ja・summary・category を持つ JSON 配列で出力してください。"""


def _generate(client, prompt: str, schema, sleep=time.sleep) -> list:
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=list[schema],
                ),
            )
        except errors.APIError as e:
            if e.code not in RETRY_CODES or attempt == MAX_RETRIES:
                raise
            sleep(30 * (attempt + 1))
            continue
        try:
            return [schema.model_validate(x) for x in json.loads(response.text)]
        except (json.JSONDecodeError, TypeError, ValidationError) as e:
            raise BadResponse(str(e)) from e
    raise AssertionError("unreachable")


def judge(client, company: dict, items: list[dict], sleep=time.sleep) -> list[Judgement]:
    return _generate(client, build_prompt(company, items), Judgement, sleep)


class SameEvent(BaseModel):
    label: str
    same_as: str


def build_event_prompt(company: dict, context: list[dict], items: list[dict]) -> str:
    def line(label, a):
        return f"{label}. {(a.get('published') or a['fetched'])[:10]} [{a['source']}] {a.get('title_ja') or a['title']}"

    existing = "\n".join(line(f"E{i}", a) for i, a in enumerate(context)) or "（なし）"
    new = "\n".join(line(f"N{i}", a) for i, a in enumerate(items))
    return f"""以下は「{company['name']}」（{company['description']}）に関するニュースの一覧です。E で始まる記事はすでに掲載済み、N で始まる記事は新しく見つかった記事です。

各 N の記事について、同じ出来事を報じている記事がほかにあれば、その記号（E0 や N3 など）を same_as に入れてください。
- 同じ出来事とは、同じ発表・同じ契約・同じ決算・同じ人事・同じ日の同じ株価の動きのことです。媒体・言語・見出しの言い回しが違っても、内容が同じなら同じ出来事です。
- 同じ会社の別の出来事、同じテーマを扱った別の分析記事、日付が大きく違う似た話題は、同じ出来事ではありません。
- 迷う場合は同じ出来事としないでください。
- 該当がなければ same_as は空文字にしてください。

掲載済み:
{existing}

新しい記事:
{new}

すべての N の記事について、label（N の記号）と same_as を持つ JSON 配列で出力してください。"""


def find_same_events(client, company: dict, context: list[dict], items: list[dict], sleep=time.sleep) -> dict[str, str]:
    rows = _generate(client, build_event_prompt(company, context, items), SameEvent, sleep)
    return {r.label.strip(): r.same_as.strip() for r in rows}


def apply_judgements(items: list[dict], judgements: list[Judgement]):
    by_index = {j.index: j for j in judgements}
    accepted, rejected, leftover = [], [], []
    for i, c in enumerate(items):
        j = by_index.get(i)
        if j is None:
            leftover.append(c)
            continue
        # 公式発表と SEC の書類はその会社自身のものなので、関連性の判定では除外しない
        if not j.relevant and c["origin"] not in ("official", "sec"):
            rejected.append(c)
            continue
        category = j.category if j.category in CATEGORIES else "その他"
        if c.get("form") in ("10-Q", "10-K", "20-F"):
            category = "決算・業績"
        accepted.append({
            "id": c["id"], "companies": c["companies"], "title": c["title"],
            "title_ja": j.title_ja.strip() or c["title"], "summary": j.summary.strip(), "category": category,
            "url": c["url"], "source": c["source"], "origin": c["origin"], "lang": c["lang"],
            "published": c["published"], "fetched": c["fetched"],
        })
    return accepted, rejected, leftover
