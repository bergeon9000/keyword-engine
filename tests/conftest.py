import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHttp:
    """Stands in for HttpClient: answers every request from saved real responses."""

    def __init__(self, fail_hosts=()):
        self.fail_hosts = set(fail_hosts)
        self.calls = []

    def get_json(self, url, params=None, headers=None):
        from keyword_engine.sources import FetchError
        self.calls.append((url, params))
        for host in self.fail_hosts:
            if host in url:
                raise FetchError("simulated outage")
        if "suggestqueries" in url:
            return fixture("youtube_reef_tank.json" if params.get("ds") == "yt" else "google_reef_tank.json")
        if "bing.com" in url:
            return fixture("bing_reef_tank.json")
        if "duckduckgo" in url:
            return fixture("ddg_reef_tank.json")
        if "amazon" in url:
            return fixture("amazon_reef_tank.json")
        if "stackexchange" in url:
            return fixture("stackexchange_reef_tank.json")
        if "w/api.php" in url and params.get("list") == "search":
            return {"query": {"search": [{"title": "Reef aquarium"}]}}
        if "w/api.php" in url:
            return {"query": {"pages": {"1": {"title": "Reef aquarium"}}}}
        if "pageviews" in url:
            return {"items": [{"views": 500}, {"views": 700}]}
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def fake_http():
    return FakeHttp()
