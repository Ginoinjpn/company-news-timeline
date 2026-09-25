import json

import pytest
from google.genai import errors

from collector import summarize
from collector.summarize import Judgement

COMPANY = {"ticker": "IONQ", "name": "IonQ", "description": "量子コンピュータ企業"}


def cand(i, origin="other", form=None):
    c = {"id": f"id{i}", "companies": ["IONQ"], "title": f"IonQ news {i}", "url": f"https://x/{i}", "source": "Reuters",
         "origin": origin, "lang": "en", "published": "2026-09-24T00:00:00Z", "snippet": "", "fetched": "2026-09-25T00:00:00Z"}
    if form:
        c["form"] = form
    return c


def j(i, relevant=True, category="提携・契約"):
    return Judgement(index=i, relevant=relevant, title_ja=f"見出し{i}", summary=f"要約{i}", category=category)


def test_prompt_lists_items_and_categories():
    prompt = summarize.build_prompt(COMPANY, [cand(0), cand(1)])
    assert "0. [Reuters] IonQ news 0" in prompt
    assert "1. [Reuters] IonQ news 1" in prompt
    assert "株価・市場" in prompt and "量子コンピュータ企業" in prompt


def test_apply_judgements_accepts_rejects_and_leaves_missing():
    items = [cand(0), cand(1), cand(2)]
    accepted, rejected, leftover = summarize.apply_judgements(items, [j(0), j(1, relevant=False)])
    assert [a["id"] for a in accepted] == ["id0"]
    assert accepted[0]["title_ja"] == "見出し0" and accepted[0]["category"] == "提携・契約"
    assert "snippet" not in accepted[0]
    assert [c["url"] for c in rejected] == ["https://x/1"]
    assert [c["id"] for c in leftover] == ["id2"]


def test_official_and_sec_are_never_rejected_and_filings_get_earnings_category():
    items = [cand(0, origin="official"), cand(1, origin="sec", form="10-Q")]
    accepted, rejected, _ = summarize.apply_judgements(items, [j(0, relevant=False), j(1, relevant=False, category="その他")])
    assert len(accepted) == 2 and rejected == []
    assert accepted[1]["category"] == "決算・業績"


def test_unknown_category_becomes_other_and_blank_title_falls_back():
    bad = Judgement(index=0, relevant=True, title_ja=" ", summary="s", category="謎")
    [a], _, _ = summarize.apply_judgements([cand(0)], [bad])
    assert a["category"] == "その他" and a["title_ja"] == "IonQ news 0"


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"text": r})()


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def test_judge_parses_json():
    payload = json.dumps([{"index": 0, "relevant": True, "title_ja": "t", "summary": "s", "category": "その他"}])
    out = summarize.judge(FakeClient([payload]), COMPANY, [cand(0)], sleep=lambda s: None)
    assert out[0].title_ja == "t"


def test_judge_retries_rate_limit_then_succeeds():
    rate = errors.APIError(429, {"error": {"message": "quota"}})
    client = FakeClient([rate, "[]"])
    assert summarize.judge(client, COMPANY, [cand(0)], sleep=lambda s: None) == []
    assert client.models.calls == 2


def test_judge_raises_bad_response_on_broken_json():
    with pytest.raises(summarize.BadResponse):
        summarize.judge(FakeClient(["not json"]), COMPANY, [cand(0)], sleep=lambda s: None)


def test_judge_raises_bad_response_on_blocked_or_invalid_output():
    with pytest.raises(summarize.BadResponse):
        summarize.judge(FakeClient([None]), COMPANY, [cand(0)], sleep=lambda s: None)
    with pytest.raises(summarize.BadResponse):
        summarize.judge(FakeClient(['[{"index": "x"}]']), COMPANY, [cand(0)], sleep=lambda s: None)


def test_judge_does_not_hide_non_retryable_api_errors():
    bad_key = errors.APIError(400, {"error": {"message": "API key not valid"}})
    with pytest.raises(errors.APIError):
        summarize.judge(FakeClient([bad_key]), COMPANY, [cand(0)], sleep=lambda s: None)
