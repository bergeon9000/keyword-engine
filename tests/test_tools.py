import json

from keyword_engine.analyze import build_tool_queries, tool_type
from keyword_engine.pipeline import load_niche, run_tools


def test_tool_types():
    assert tool_type("gravel calculator") == "quantity"
    assert tool_type("how much gravel do i need") == "quantity"
    assert tool_type("how many bags of concrete") == "quantity"
    assert tool_type("deck cost calculator") == "cost"
    assert tool_type("how much does a fence cost") == "cost"
    assert tool_type("concrete cost per yard") == "cost"
    assert tool_type("cubic yards to tons gravel") == "conversion"
    assert tool_type("gravel yards to tons") == "conversion"
    assert tool_type("rebar size chart") == "chart"
    assert tool_type("deck joist span") == "sizing"
    assert tool_type("stair stringer formula") == "sizing"
    assert tool_type("joist span table") == "chart"
    assert tool_type("patio table and chairs") is None
    assert tool_type("gravel driveway ideas") is None
    assert tool_type("pea gravel") is None


def test_tool_query_plan():
    plan = build_tool_queries("Gravel")
    queries = [q for _, q in plan]
    assert queries[0] == "gravel"
    assert "gravel calculator" in queries and "how much gravel" in queries
    assert len(queries) == len(set(queries))


class ToolFakeHttp:
    """Bing-style answers: each query returns two tool phrases and one non-tool phrase."""

    def get_json(self, url, params=None, headers=None):
        q = params.get("query") or params.get("q")
        seed = q.split()[-1] if q.startswith(("how", "calculate", "estimate")) else q.split()[0]
        phrases = [f"{seed} calculator", f"how much {seed} do i need", f"{seed} ideas"]
        if "duckduckgo" in url:
            return [{"phrase": p} for p in phrases]
        return [q, phrases]


def test_run_tools_merges_across_seeds_and_keeps_only_tools(tmp_path):
    out, run = run_tools(["gravel", "mulch"], load_niche("construction"), engines=["bing", "ddg"],
                         cache_dir=tmp_path / "c", out_root=tmp_path / "r", http=ToolFakeHttp())
    rows = [json.loads(l) for l in (out / "tools.jsonl").read_text(encoding="utf-8").splitlines()]
    phrases = {r["phrase"] for r in rows}
    assert {"gravel calculator", "how much gravel do i need", "mulch calculator"} <= phrases
    assert not any("ideas" in p for p in phrases)
    assert all(r["tool_type"] for r in rows)
    assert run["validation_passed"] and run["counts"]["per_seed"]["gravel"]["tool_phrases"] >= 2
    assert (out / "tools.csv").exists() and "Per seed" in (out / "report.md").read_text(encoding="utf-8")
