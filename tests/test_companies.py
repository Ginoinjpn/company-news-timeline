import re

from companies import COMPANIES

REQUIRED = {"ticker", "name", "queries_en", "queries_ja", "color", "description"}


def test_every_company_has_required_keys():
    for c in COMPANIES:
        assert REQUIRED <= c.keys(), c.get("ticker")


def test_tickers_are_unique_and_uppercase():
    tickers = [c["ticker"] for c in COMPANIES]
    assert len(tickers) == len(set(tickers))
    assert all(t == t.upper() for t in tickers)


def test_colors_are_hex():
    for c in COMPANIES:
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", c["color"])


def test_ionq_is_configured():
    ionq = next(c for c in COMPANIES if c["ticker"] == "IONQ")
    assert ionq["sec_cik"] == "0001824920"
    assert ionq["official_rss"] == "https://ionq.com/news/rss.xml"


def test_new_companies_are_configured():
    by_ticker = {c["ticker"]: c for c in COMPANIES}
    assert {"IONQ", "JMIA", "NBIS", "6965"} <= by_ticker.keys()
    assert by_ticker["JMIA"]["sec_cik"] == "0001756708"
    assert by_ticker["NBIS"]["sec_cik"] == "0001513845"
    assert by_ticker["6965"]["us_listed"] is False
    assert "6965" in by_ticker["6965"]["official_rss"]
