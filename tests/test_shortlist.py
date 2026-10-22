import json

import pytest

from keyword_engine.shortlist import Store, estimate, pick, run_shortlist, summarize_serp
from keyword_engine.volume import VolumeError

ROWS = [{"phrase": f"gravel calc {i}", "tool_type": "quantity", "search_volume": v, "cpc": 1.0, "seeds": ["gravel"]}
        for i, v in enumerate([5000, 0, None, 900, 12000, 40])]


@pytest.fixture
def run_dir(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    (d / "volume.jsonl").write_text("\n".join(json.dumps(r) for r in ROWS) + "\n", encoding="utf-8")
    return d


class FakeClient:
    def __init__(self, ready_after=0):
        self.calls, self.ready_after, self.polls = [], ready_after, 0

    def _call(self, method, path, body=None):
        self.calls.append((method, path))
        if "keyword_difficulty" in path:
            items = [{"keyword": t, "keyword_difficulty": 10 + i} for i, t in enumerate(body[0]["keywords"])]
            return {"cost": 0.0124, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}
        if path.endswith("task_post"):
            return {"cost": 0.0006 * len(body),
                    "tasks": [{"status_code": 20100, "id": f"t-{b['keyword']}"} for b in body]}
        self.polls += 1
        if self.polls <= self.ready_after:
            return {"tasks": [{"status_code": 40602}]}
        return {"tasks": [{"status_code": 20000, "result": [{"item_types": ["organic", "people_also_ask"], "items": [
            {"type": "organic", "rank_group": 1, "domain": "www.omnicalculator.com", "title": "x", "url": "u"},
            {"type": "organic", "rank_group": 2, "domain": "www.reddit.com", "title": "y", "url": "v"},
            {"type": "paid", "domain": "ads.example.com"}]}]}]}


def test_pick_orders_by_volume_and_drops_zero_and_unknown():
    assert [r["search_volume"] for r in pick(ROWS, 3)] == [12000, 5000, 900]


def test_base_domain():
    from keyword_engine.shortlist import _base_domain
    assert _base_domain("www.homedepot.com") == "homedepot.com"
    assert _base_domain("shop.example.co.uk") == "example.co.uk"


def test_estimate():
    assert estimate(40, 40) == round(0.012 + 0.00012 * 40 + 0.0006 * 40, 4)
    assert estimate(0, 0) == 0


def test_summarize_serp_counts_big_and_weak():
    s = summarize_serp(FakeClient()._call("GET", "/serp/google/organic/task_get/regular/x")["tasks"][0]["result"][0])
    assert s["big_sites"] == 1 and s["weak_results"] == 1
    assert [t["domain"] for t in s["top10"]] == ["omnicalculator.com", "reddit.com"]
    assert s["features"] == ["people_also_ask"]


def test_dry_run_and_cap_send_nothing(run_dir, tmp_path):
    client = FakeClient()
    assert run_shortlist(run_dir, tmp_path / "kd.json", tmp_path / "serp.json", top=3, client=client,
                         log=lambda m: None) is None
    with pytest.raises(VolumeError, match="max-cost"):
        run_shortlist(run_dir, tmp_path / "kd.json", tmp_path / "serp.json", top=3, spend=True, max_cost=0.001,
                      client=client, log=lambda m: None)
    assert client.calls == []


def test_spend_then_rerun_is_free(run_dir, tmp_path):
    client = FakeClient(ready_after=2)
    out = run_shortlist(run_dir, tmp_path / "kd.json", tmp_path / "serp.json", top=3, spend=True, poll_s=0,
                        client=client, log=lambda m: None)
    assert out["rows"] == 3
    report = (run_dir / "shortlist_report.md").read_text(encoding="utf-8")
    assert "omnicalculator.com" in report and "gravel calc 4" in report
    rows = [json.loads(l) for l in (run_dir / "shortlist.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(r["keyword_difficulty"] is not None for r in rows)

    again = FakeClient()
    run_shortlist(run_dir, tmp_path / "kd.json", tmp_path / "serp.json", top=3, spend=True, poll_s=0,
                  client=again, log=lambda m: None)
    assert again.calls == []  # everything came from the ledgers


def test_queued_serps_survive_a_timeout(run_dir, tmp_path):
    slow = FakeClient(ready_after=10**6)
    assert run_shortlist(run_dir, tmp_path / "kd.json", tmp_path / "serp.json", top=2, spend=True,
                         wait_minutes=0, poll_s=0, client=slow, log=lambda m: None) is None
    assert len(Store(tmp_path / "serp.json").pending) == 2
    posts_before = sum(1 for _, p in slow.calls if p.endswith("task_post"))
    slow.ready_after = 0
    run_shortlist(run_dir, tmp_path / "kd.json", tmp_path / "serp.json", top=2, spend=True, poll_s=0,
                  client=slow, log=lambda m: None)
    assert sum(1 for _, p in slow.calls if p.endswith("task_post")) == posts_before  # collected, not re-bought
