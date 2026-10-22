import json

import pytest

from conftest import FakeHttp
from keyword_engine.pipeline import load_niche, run_expand
from keyword_engine.volume import (DataForSEO, Ledger, VolumeError, best_entry, evaluate,
                                   google_ads_ok, run_volume, spearman)


class FakeClient:
    """Stands in for DataForSEO: volume = 1000 // (position + 1), ready after `delay` polls."""

    def __init__(self, delay=0):
        self.posted, self.delay, self.polls = [], delay, 0

    def balance(self):
        return 1.0 - 0.06 * len(self.posted)

    def post(self, keywords, location_code=2840, language_code="en", tag=None):
        self.posted.append(list(keywords))
        return f"task-{len(self.posted)}", 0.06

    def get(self, task_id):
        self.polls += 1
        if self.polls <= self.delay:
            return None
        kws = self.posted[int(task_id.split("-")[1]) - 1]
        return [{"keyword": k, "search_volume": 1000 // (i + 1), "cpc": 1.5} for i, k in enumerate(kws[:-1])]


@pytest.fixture
def run_dir(tmp_path):
    out, _ = run_expand("reef tank", load_niche("reef"), cache_dir=tmp_path / "cache",
                        out_root=tmp_path / "runs", http=FakeHttp(), prefix_top=0)
    return out


def test_google_ads_limits():
    assert google_ads_ok("reef tank lights")
    assert not google_ads_ok("reef tank & lights")
    assert not google_ads_ok(" ".join(["word"] * 11))
    assert not google_ads_ok("x" * 81)


def test_dry_run_never_touches_the_client(run_dir, tmp_path):
    class Boom:
        def __getattr__(self, name):
            raise AssertionError("client used during a dry run")
    assert run_volume(run_dir, tmp_path / "ledger.json", spend=False, client=Boom(), log=lambda m: None) is None
    assert not (tmp_path / "ledger.json").exists()


def test_cost_cap_blocks_before_sending(run_dir, tmp_path):
    client = FakeClient()
    with pytest.raises(VolumeError, match="max-cost"):
        run_volume(run_dir, tmp_path / "ledger.json", spend=True, max_cost=0.01, client=client, log=lambda m: None)
    assert client.posted == []


def test_spend_writes_report_and_never_resends(run_dir, tmp_path):
    ledger = tmp_path / "ledger.json"
    client = FakeClient()
    summary = run_volume(run_dir, ledger, spend=True, client=client, poll_s=0, log=lambda m: None)
    assert len(client.posted) == 1 and summary["cost_usd"] == 0.06
    assert summary["evaluation"]["spearman_engine_rank_vs_volume"] > 0.9  # fake volumes follow engine order
    for name in ["volume.jsonl", "volume.json", "volume_report.md"]:
        assert (run_dir / name).exists()
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["pending"] == []
    assert any(v.get("no_data") for v in data["keywords"].values())  # the keyword Google returned nothing for

    again = FakeClient()
    run_volume(run_dir, ledger, spend=True, client=again, poll_s=0, log=lambda m: None)
    assert again.posted == []  # everything already in the ledger


def test_timeout_keeps_task_pending_and_next_run_collects_it(run_dir, tmp_path):
    ledger = tmp_path / "ledger.json"
    slow = FakeClient(delay=10**6)
    assert run_volume(run_dir, ledger, spend=True, client=slow, wait_minutes=0, poll_s=0, log=lambda m: None) is None
    assert len(json.loads(ledger.read_text(encoding="utf-8"))["pending"]) == 1

    slow.delay = 0
    slow.posted_before = len(slow.posted)
    summary = run_volume(run_dir, ledger, spend=False, client=slow, poll_s=0, log=lambda m: None)
    assert summary is not None and len(slow.posted) == slow.posted_before  # collected, not re-posted


def test_best_entry_takes_highest_variant(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.add_results([{"keyword": "reef tank nano", "search_volume": 2400},
                     {"keyword": "nano reef tank", "search_volume": 2400},
                     {"keyword": "nano reef tanks", "search_volume": 90}], "t", 2840, "en", [])
    assert best_entry(led, ["nano reef tanks", "reef tank nano"], 2840, "en")["search_volume"] == 2400
    assert best_entry(led, ["never checked"], 2840, "en") == {}


def test_spearman():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert spearman([1, 2], [1, 2]) is None


def test_evaluate_groups_by_engine_count():
    rows = [{"phrase": f"p{i}", "engine_count": 5 - i % 5, "search_volume": v}
            for i, v in enumerate([900, 500, 300, None, 0, 800])]
    ev = evaluate(rows)
    assert ev["with_volume"] == 5 and ev["nonzero_volume"] == 4
    assert ev["median_volume_by_engine_count"][5] == 850


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class _Session:
    def __init__(self, *bodies):
        self.bodies, self.calls = list(bodies), []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        return _Resp(self.bodies.pop(0))


def test_client_post_and_get_shapes():
    s = _Session({"status_code": 20000, "cost": 0.06, "tasks": [{"id": "abc", "status_code": 20100}]},
                 {"status_code": 20000, "tasks": [{"id": "abc", "status_code": 40602}]},
                 {"status_code": 20000, "tasks": [{"id": "abc", "status_code": 20000,
                                                   "result": [{"keyword": "reef tank", "search_volume": 9900}]}]})
    c = DataForSEO("login", "pw", session=s)
    assert c.post(["reef tank"]) == ("abc", 0.06)
    assert s.calls[0][2][0]["location_code"] == 2840
    assert c.get("abc") is None
    assert c.get("abc")[0]["search_volume"] == 9900


def test_client_bad_login_message():
    class S:
        def request(self, *a, **kw):
            return _Resp({}, status=401)
    with pytest.raises(VolumeError, match="API login"):
        DataForSEO("x", "y", session=S()).balance()
