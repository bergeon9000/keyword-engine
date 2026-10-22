import csv
import json

from conftest import FakeHttp
from keyword_engine.cli import main
from keyword_engine.pipeline import load_niche, run_compare, run_expand


def _expand(tmp_path, http, **kw):
    return run_expand("reef tank", load_niche("reef"), cache_dir=tmp_path / "cache",
                      out_root=tmp_path / "runs", http=http, **kw)


def test_expand_writes_every_output(tmp_path):
    out, run = _expand(tmp_path, FakeHttp(), prefix_top=3)
    for name in ["phrases.jsonl", "phrases.csv", "clusters.json", "questions.json", "run.json", "report.md"]:
        assert (out / name).exists(), name
    assert run["validation_passed"], run["validation"]
    assert run["cost_usd"] == 0
    assert set(run["sources_ok"]) >= {"google", "youtube", "bing", "ddg", "amazon", "stackexchange"}

    rows = [json.loads(l) for l in (out / "phrases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["engine_count"] >= rows[-1]["engine_count"]
    assert all(r["cluster_id"] and "{keyword}" in r["shape"] for r in rows)
    with open(out / "phrases.csv", encoding="utf-8-sig") as f:
        assert len(list(csv.DictReader(f))) == len(rows)
    assert "## Top clusters" in (out / "report.md").read_text(encoding="utf-8")


def test_expand_survives_an_outage(tmp_path):
    out, run = _expand(tmp_path, FakeHttp(fail_hosts=["bing.com", "duckduckgo"]), prefix_top=0)
    assert run["validation_passed"]  # 3 of 5 engines is still enough
    assert run["sources"]["bing"]["failed"] == run["settings"]["queries_per_engine"]
    assert "First errors" in (out / "report.md").read_text(encoding="utf-8")


def test_expand_fails_validation_when_most_engines_are_down(tmp_path):
    http = FakeHttp(fail_hosts=["bing.com", "duckduckgo", "amazon", "suggestqueries"])
    _, run = _expand(tmp_path, http, prefix_top=0)
    assert not run["validation_passed"]


def test_rerun_is_all_cache(tmp_path):
    _expand(tmp_path, FakeHttp(), prefix_top=0)
    http = FakeHttp()
    _, run = _expand(tmp_path, http, prefix_top=0)
    assert http.calls == []
    assert all(s["live"] == 0 for s in run["sources"].values())


def test_compare_ranks_seeds(tmp_path):
    out, run = run_compare(["reef tank", "nano reef"], load_niche("reef"), cache_dir=tmp_path / "c",
                           out_root=tmp_path / "r", http=FakeHttp())
    seeds = json.loads((out / "seeds.json").read_text(encoding="utf-8"))
    assert [s["seed"] for s in seeds][0] == "reef tank"  # the fixtures only mention reef tank
    assert run["validation_passed"]


def test_cli_rejects_unknown_engine(capsys):
    try:
        main(["expand", "reef tank", "--engines", "google,yahoo"])
    except SystemExit as e:
        assert e.code == 2
    assert "unknown engines" in capsys.readouterr().err
