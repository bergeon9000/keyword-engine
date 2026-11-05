import json
from datetime import datetime, timezone

import pytest

from keyword_engine.questions import check, estimate, page_of, recover, render_report, report
from keyword_engine.shortlist import Store, paa_questions, summarize_serp
from keyword_engine.volume import VolumeError

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def serp_result(keyword):
    return {"item_types": ["organic", "people_also_ask", "related_searches"], "items": [
        {"type": "organic", "rank_group": 1, "domain": "www.reddit.com", "title": "x", "url": "u"},
        {"type": "people_also_ask", "items": [
            {"type": "people_also_ask_element", "title": f"How heavy is a {keyword}?"},
            {"type": "people_also_ask_element", "title": "Is it heavy?"},
            {"type": "people_also_ask_element", "title": f"How heavy is a {keyword}?"}]},  # a click repeats one
        {"type": "related_searches", "items": [f"{keyword} chart", f"{keyword} in kg"]}]}


class FakeClient:
    def __init__(self, tasks=(), ready_after=0):
        self.calls, self.tasks, self.ready_after, self.polls = [], list(tasks), ready_after, 0
        self.posted = []

    def _call(self, method, path, body=None):
        self.calls.append((method, path))
        if path == "/serp/id_list":
            return {"tasks": [{"status_code": 20000, "result": self.tasks}]}
        if "keyword_difficulty" in path:
            items = [{"keyword": k, "keyword_difficulty": 7} for k in body[0]["keywords"]]
            return {"cost": 0.0124, "tasks": [{"status_code": 20000, "result": [{"items": items}]}]}
        if path.endswith("task_post"):
            self.posted += body
            return {"cost": 0.0009 * len(body), "tasks": [{"status_code": 20100, "id": b["keyword"]} for b in body]}
        assert "/task_get/advanced/" in path, path  # questions only come in the advanced format
        self.polls += 1
        if self.polls <= self.ready_after:
            return {"tasks": [{"status_code": 40602}]}
        return {"tasks": [{"status_code": 20000, "result": [serp_result(path.rsplit("/", 1)[1])]}]}


def test_paa_questions_keep_order_and_drop_repeats():
    assert paa_questions(serp_result("brick")) == ["How heavy is a brick?", "Is it heavy?"]
    s = summarize_serp(serp_result("brick"))
    assert s["related"] == ["brick chart", "brick in kg"] and s["weak_results"] == 1


def test_recover_fills_old_entries_without_changing_their_date(tmp_path):
    ledger = tmp_path / "serp.json"
    old = Store(ledger)
    old.items[Store.key("brick sizes", 2840, "en")] = {"top10": [], "features": ["people_also_ask"],
                                                      "fetched_at": "2026-10-22T23:00:00+00:00"}
    old.save()
    tasks = [{"id": "brick sizes", "url": "https://api.dataforseo.com/v3/serp/google/organic/task_post",
              "datetime_done": "2026-10-22 23:05:00 +00:00",
              "metadata": {"keyword": "Brick Sizes", "location_code": 2840, "language_code": "en"}},
             {"id": "new one", "url": "https://api.dataforseo.com/v3/serp/google/organic/task_post",
              "datetime_done": "2026-10-23 01:00:00 +00:00",
              "metadata": {"keyword": "new one", "location_code": 2840, "language_code": "en"}},
             {"id": "maps", "url": "https://api.dataforseo.com/v3/serp/google/maps/task_post",
              "metadata": {"keyword": "maps", "location_code": 2840, "language_code": "en"}}]
    client = FakeClient(tasks)
    assert recover(client, ledger, now=NOW, log=lambda m: None) == 2
    s = Store(ledger)
    entry = s.get("brick sizes", 2840, "en")
    assert entry["paa"][0] == "How heavy is a brick sizes?" and entry["fetched_at"].startswith("2026-10-22")
    assert s.get("new one", 2840, "en")["fetched_at"].startswith("2026-10-23")
    assert s.get("maps", 2840, "en") is None
    assert not any(p.endswith("task_post") for _, p in client.calls)  # nothing bought

    again = FakeClient(tasks)
    assert recover(again, ledger, now=NOW, log=lambda m: None) == 0  # already has questions: not fetched again
    assert [p for _, p in again.calls] == ["/serp/id_list"]


def test_check_dry_run_and_cap_send_nothing(tmp_path):
    client = FakeClient()
    assert check(["brick weight"], tmp_path / "kd.json", tmp_path / "serp.json", client=client,
                 log=lambda m: None) is None
    with pytest.raises(VolumeError, match="max-cost"):
        check(["brick weight"], tmp_path / "kd.json", tmp_path / "serp.json", spend=True, max_cost=0.0001,
              client=client, log=lambda m: None)
    assert client.calls == []


def test_check_buys_once_with_click_depth(tmp_path):
    client = FakeClient(ready_after=1)
    cost = check(["Brick weight", "brick weight", "sonotube sizes"], tmp_path / "kd.json", tmp_path / "serp.json",
                 spend=True, paa_depth=2, poll_s=0, client=client, log=lambda m: None)
    assert cost == round(0.0124 + 0.0009 * 2, 4)
    assert [b["keyword"] for b in client.posted] == ["brick weight", "sonotube sizes"]
    assert all(b["people_also_ask_click_depth"] == 2 for b in client.posted)
    assert Store(tmp_path / "serp.json").get("sonotube sizes", 2840, "en")["paa"]

    again = FakeClient()
    assert check(["brick weight"], tmp_path / "kd.json", tmp_path / "serp.json", spend=True, poll_s=0,
                 client=again, log=lambda m: None) == 0
    assert again.calls == []  # both ledgers had it


def test_estimate_counts_clicks():
    assert estimate(30, 30, 2) == round(0.012 + 0.00012 * 30 + (0.0006 + 0.0003) * 30, 4)
    assert estimate(0, 10, 0) == 0.006


CATALOG = {"pages": [
    {"path": "/brick-calculator/", "type": "calculator", "name": "Brick", "targets": ["brick calculator"], "topics": ["brick"]},
    {"path": "/brick-weight/", "type": "article", "name": "How much does a brick weigh?",
     "targets": ["how much does a brick weigh", "brick weight"], "topics": []}]}


def test_report_groups_by_page(tmp_path):
    ledger = tmp_path / "serp.json"
    s = Store(ledger)
    s.put("brick weight", 2840, "en", summarize_serp(serp_result("brick")))
    s.put("brick sizes standard", 2840, "en", {"top10": [], "features": []})  # bought before questions were kept
    s.put("sonotube sizes", 2840, "en", summarize_serp({"items": []}))
    s.save()
    assert page_of("brick weight", CATALOG)["path"] == "/brick-weight/"  # the article that targets it
    assert page_of("brick sizes standard", CATALOG)["path"] == "/brick-calculator/"  # else its calculator
    rows = report(["brick weight", "brick sizes standard", "sonotube sizes", "never checked"], ledger,
                  tmp_path / "kd.json", CATALOG)
    assert [r["phrase"] for r in rows] == ["brick weight", "brick sizes standard", "sonotube sizes"]
    md = render_report(rows, 0.04)
    assert "## How much does a brick weigh? (/brick-weight/)" in md and "- How heavy is a brick?" in md
    assert "Questions not stored" in md and "No People Also Ask box." in md and "## No page yet" in md
    assert md.index("/brick-weight/") < md.index("/brick-calculator/") < md.index("No page yet")  # articles first
    json.dumps(rows)
