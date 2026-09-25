from collector import article_text

LONG = "IonQ announced a new contract. " * 40


class Res:
    def __init__(self, status, text=""):
        self.status_code = status
        self.ok = status < 400
        self.text = text


def page(body):
    return f"<html><body><article><h1>Title</h1><p>{body}</p></article></body></html>"


def test_resolve_url_passes_direct_links_through():
    assert article_text.resolve_url("https://www.fool.com/x", decode=lambda u, **k: 1 / 0) == "https://www.fool.com/x"


def test_resolve_url_decodes_google_news_links():
    ok = lambda u, **k: {"success": True, "decoded_url": "https://real/1"}
    bad = lambda u, **k: {"success": False, "message": "x"}
    assert article_text.resolve_url("https://news.google.com/rss/articles/abc", decode=ok) == "https://real/1"
    assert article_text.resolve_url("https://news.google.com/rss/articles/abc", decode=bad) is None


def test_extract_body_requires_enough_text_and_is_capped():
    assert article_text.extract_body(page("too short")) is None
    body = article_text.extract_body(page(LONG * 5))
    assert body and len(body) <= article_text.BODY_LIMIT


def test_fetch_body_returns_resolved_url_and_body_or_none():
    get_ok = lambda url, **k: Res(200, page(LONG))
    get_403 = lambda url, **k: Res(403)
    real, body = article_text.fetch_body("https://direct/1", get=get_ok)
    assert real == "https://direct/1" and "IonQ announced" in body
    assert article_text.fetch_body("https://direct/1", get=get_403) == ("https://direct/1", None)
    boom = lambda url, **k: (_ for _ in ()).throw(TimeoutError())
    assert article_text.fetch_body("https://direct/1", get=boom) == ("https://direct/1", None)


def test_fetch_bodies_maps_each_url():
    out = article_text.fetch_bodies(["https://a", "https://b"], fetch=lambda u: (u + "/real", "body " + u), workers=2)
    assert out == {"https://a": ("https://a/real", "body https://a"), "https://b": ("https://b/real", "body https://b")}
