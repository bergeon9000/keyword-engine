from datetime import date

import pytest
import requests

from conftest import FakeHttp, fixture
from keyword_engine.cache import Cache
from keyword_engine.sources import (FetchError, HttpClient, SourceLog, Sources, last_full_months,
                                    parse_amazon, parse_ddg, parse_google, parse_osjson,
                                    parse_stackexchange, wiki_title_matches)

REGION = {"hl": "en", "gl": "us", "market": "en-US", "ddg_kl": "us-en"}


def test_parse_google_keeps_relevance():
    hits = parse_google(fixture("google_reef_tank.json"))
    assert hits[0]["rank"] == 1
    assert all(isinstance(h["relevance"], int) for h in hits)
    assert hits[0]["relevance"] >= hits[-1]["relevance"]


def test_parse_other_engines():
    for parse, name in [(parse_osjson, "bing"), (parse_osjson, "youtube"),
                        (parse_ddg, "ddg"), (parse_amazon, "amazon")]:
        hits = parse(fixture(f"{name}_reef_tank.json"))
        assert hits, name
        assert all("reef" in h["phrase"].lower() for h in hits), name


def test_parse_stackexchange_has_views():
    qs = parse_stackexchange(fixture("stackexchange_reef_tank.json"), "pets")
    assert qs and all(q["views"] > 0 and q["site"] == "pets" for q in qs)


def test_wiki_title_guard():
    assert wiki_title_matches("Freshwater aquarium", "freshwater aquarium")
    assert wiki_title_matches("Blennies", "blenny")
    assert not wiki_title_matches("Hank Plante", "planted tank")
    assert not wiki_title_matches("Lima Army Tank Plant", "planted tank")


class _WikiNoExactTitle:
    """Exact title missing, so the search fallback runs; only the 2nd hit is on topic."""

    def get_json(self, url, params=None, headers=None):
        if "pageviews" in url:
            return {"items": [{"views": 100}]}
        if params.get("list") == "search":
            return {"query": {"search": [{"title": "Hank Plante"}, {"title": "Planted tank"}]}}
        return {"query": {"pages": {"-1": {"title": params["titles"], "missing": ""}}}}


def test_wiki_search_fallback_skips_off_topic_hits(tmp_path):
    s = Sources(_WikiNoExactTitle(), Cache(tmp_path), REGION, SourceLog())
    assert s.wiki_monthly_views("planted tank")["title"] == "Planted tank"


def test_last_full_months():
    assert last_full_months(12, date(2026, 10, 21)) == ("2025100100", "2026090100")
    assert last_full_months(3, date(2026, 1, 5)) == ("2025100100", "2025120100")


def test_failed_engine_is_logged_not_raised(tmp_path):
    log = SourceLog()
    s = Sources(FakeHttp(fail_hosts=["bing.com"]), Cache(tmp_path), REGION, log)
    assert s.suggest("bing", "reef tank") == []
    assert s.suggest("ddg", "reef tank")
    assert log.stats["bing"]["failed"] == 1 and log.stats["bing"]["errors"]
    assert log.ok_sources() == ["ddg"]


def test_second_call_comes_from_cache(tmp_path):
    http = FakeHttp()
    s = Sources(http, Cache(tmp_path), REGION, SourceLog())
    s.suggest("google", "reef tank")
    s.suggest("google", "reef tank")
    assert len(http.calls) == 1
    assert s.log.stats["google"]["cache"] == 1


def test_wikipedia_average(tmp_path):
    s = Sources(FakeHttp(), Cache(tmp_path), REGION, SourceLog())
    assert s.wiki_monthly_views("reef tank") == {"title": "Reef aquarium", "avg_monthly_views": 600,
                                                  "months": 2}


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *a, **kw):
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_http_retries_once_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    h = HttpClient(delay=0, session=_Session([requests.Timeout("slow"), _Resp(200, ["ok"])]))
    assert h.get_json("https://example.com/x") == ["ok"]


def test_http_gives_up_with_fetch_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    h = HttpClient(delay=0, session=_Session([_Resp(429), _Resp(503)]))
    with pytest.raises(FetchError):
        h.get_json("https://example.com/x")


def test_http_404_is_none():
    h = HttpClient(delay=0, session=_Session([_Resp(404)]))
    assert h.get_json("https://example.com/x") is None
