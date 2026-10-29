import json

from conftest import FakeHttp
from keyword_engine.articles import (group, match, pick, render_brief, role, season, write_briefs)
from keyword_engine.pipeline import load_niche, run_expand

CATALOG = {"site": "Test", "pages": [
    {"path": "/gravel-calculator/", "slug": "gravel-calculator", "name": "Gravel", "targets": ["gravel calculator"],
     "topics": ["gravel", "crushed stone"]},
    {"path": "/pea-gravel-calculator/", "slug": "pea-gravel-calculator", "name": "Pea gravel",
     "targets": ["pea gravel calculator"], "topics": ["pea gravel"]},
    {"path": "/brick-calculator/", "slug": "brick-calculator", "name": "Brick", "targets": ["brick calculator"],
     "topics": ["brick"]},
    {"path": "/concrete-calculator/", "slug": "concrete-calculator", "name": "Concrete",
     "targets": ["concrete calculator"], "topics": ["concrete", "sonotube"]},
    {"path": "/concrete-block-calculator/", "slug": "concrete-block-calculator", "name": "Concrete Block",
     "targets": ["concrete block calculator"], "topics": ["concrete block", "cinder block"]},
]}
NICHE = {"name": "construction", "skip_words": ["gold"]}


def row(phrase, volume=None, **kw):
    return dict({"phrase": phrase, "variants": [phrase], "search_volume": volume, "cpc": 1.0, "intent": "informational",
                 "engine_count": 3, "times_seen": 2}, **kw)


def series(*values):
    return [{"year": 2026, "month": m, "search_volume": v} for m, v in enumerate(values, 1)]


def test_longest_topic_word_wins_and_plurals_match():
    assert match("pea gravel sizes", CATALOG)[0]["slug"] == "pea-gravel-calculator"
    assert match("how many cinder blocks on a pallet", CATALOG)[0]["slug"] == "concrete-block-calculator"
    assert match("bricks per square foot", CATALOG)[0]["slug"] == "brick-calculator"
    assert match("mulch per yard", CATALOG) == (None, None)


def test_roles_carry_plain_reasons():
    assert role(row("gravel calculator", 5000), NICHE, True, CATALOG) == ("calculator page", "owned by /gravel-calculator/")
    assert role(row("deck calculator", 5000), NICHE, True, CATALOG)[1].startswith("tool idea")
    assert role(row("concrete price per yard", 5000), NICHE, True, CATALOG)[0] == "needs prices"
    assert role(row("how much does a brick of gold weigh", 9000), NICHE, True, CATALOG) == ("off topic", "skip word 'gold'")
    assert role(row("brick sizes", 40), NICHE, True, CATALOG)[0] == "low volume"
    assert role(row("brick sizes"), NICHE, False, CATALOG)[0] == "article"  # no volume yet: keep it
    assert role(row("how much does a cinder block weigh", 9900), NICHE, True, CATALOG)[0] == "article"


def test_season_names_the_top_three_months():
    assert season(series(*[100] * 12)) == "steady"
    assert season(series(50, 50, 200, 300, 250, 90, 60, 50, 50, 60, 50, 50)) == "peaks Mar, Apr, May"
    assert season([]) is None


def _groups(rows, serps=None):
    s = {r["phrase"]: (serps or {}).get(r["phrase"]) for r in rows}
    return sorted(sorted(r["phrase"] for r in g) for g in group(
        rows, lambda r: r.get("_series"), lambda r: s[r["phrase"]], lambda r: match(r["phrase"], CATALOG)))


def test_one_article_per_facet_but_different_topics_stay_apart():
    rows = [row("brick sizes", 12100), row("brick size in inches", 1300), row("brick sizes standard", 8100),
            row("sonotube sizes", 1900), row("concrete patio size", 50), row("concrete driveway curing time chart", 210),
            row("how much does a cinder block weigh", 9900), row("concrete block weight", 4400),
            row("how many bricks in a pallet", 1000)]
    assert _groups(rows) == [
        ["brick size in inches", "brick sizes", "brick sizes standard"],
        ["concrete block weight", "how much does a cinder block weigh"],
        ["concrete driveway curing time chart"], ["concrete patio size"],
        ["how many bricks in a pallet"], ["sonotube sizes"]]


def test_google_merged_variants_and_shared_top_tens_join():
    same = series(*range(100, 112))
    rows = [row("gravel driveway depth", 900, _series=same), row("depth of gravel driveway base", 900, _series=same),
            row("crushed stone driveway", 700), row("gravel road base", 300)]
    top = [{"url": f"https://site{i}.com/"} for i in range(10)]
    serps = {"crushed stone driveway": {"top10": top},
             "gravel road base": {"top10": top[:4] + [{"url": "https://other.com/"}]}}
    assert _groups(rows, serps) == [["crushed stone driveway", "gravel road base"],
                                    ["depth of gravel driveway base", "gravel driveway depth"]]


def _run(tmp_path, rows, kd=None, serp=None):
    run_dir, data = tmp_path / "run", tmp_path / "data"
    run_dir.mkdir()
    data.mkdir()
    (run_dir / "volume.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    key = lambda k: f"{k}|2840|en"
    (data / "kd_ledger.json").write_text(json.dumps({"items": {key(k): {"keyword_difficulty": v}
                                                               for k, v in (kd or {}).items()}}), encoding="utf-8")
    (data / "serp_ledger.json").write_text(json.dumps({"items": {key(k): v for k, v in (serp or {}).items()}}),
                                           encoding="utf-8")
    return run_dir, data


def test_pick_ranks_wave_one_first_and_routes_calculator_serps(tmp_path):
    calcs = {"top10": [{"rank": i, "domain": "x.com", "title": "Concrete Calculator", "url": f"https://c{i}.com/"}
                       for i in range(8)] + [{"rank": 9, "domain": "r.com", "title": "Thread", "url": "https://r.com/"}],
             "big_sites": 1, "weak_results": 1, "features": []}
    rows = [row("mulch per bag", 9000), row("brick sizes", 12100), row("brick weight", 900),
            row("how much concrete do i need", 5400), row("brick calculator", 8100), row("concrete calculator", 99)]
    run_dir, data = _run(tmp_path, rows, kd={"brick sizes": 0, "brick weight": 40},
                         serp={"how much concrete do i need": calcs})
    r = pick(run_dir, NICHE, CATALOG, data)
    assert [(a["head"], a["wave"]) for a in r["picks"]] == [("brick sizes", 1), ("brick weight", 1), ("mulch per bag", 2)]
    assert r["picks"][0]["links_to"] == "/brick-calculator/" and r["picks"][0]["page_type"] == "reference chart"
    assert [a["head"] for a in r["calculator_faq"]] == ["how much concrete do i need"]
    assert "8 of the top 10" in r["calculator_faq"][0]["reason"]
    assert {x["phrase"] for x in r["roles"]["calculator page"]} == {"brick calculator", "concrete calculator"}


def test_briefs_are_written_from_data_and_never_overwrite_started_work(tmp_path):
    run_dir, data = _run(tmp_path, [row("brick sizes", 12100), row("brick weight", 900)])
    r = pick(run_dir, NICHE, CATALOG, data)
    assert write_briefs(run_dir, r, [{"title": "What size is a standard brick?", "link": "https://x/", "views": 50,
                                      "site": "diy"}], []) == 2
    brief = (run_dir / "briefs" / "brick-sizes.md").read_text(encoding="utf-8")
    assert "status: candidate" in brief and "links_to: /brick-calculator/" in brief
    assert "What size is a standard brick?" in brief and "No prices" in brief
    started = brief.replace("status: candidate", "status: writing") + "\nmy notes\n"
    (run_dir / "briefs" / "brick-sizes.md").write_text(started, encoding="utf-8")
    assert write_briefs(run_dir, r, [], []) == 1
    assert (run_dir / "briefs" / "brick-sizes.md").read_text(encoding="utf-8") == started


def test_brief_without_top_ten_says_unchecked():
    a = {"head": "brick sizes", "phrases": ["brick sizes"], "page_type": "reference chart", "links_to": None,
         "volume": None, "kd": None, "season": None, "serp": None}
    text = render_brief(a)
    assert "volume: unchecked" in text and "Not checked yet" in text and "No calculator to link yet" in text


def test_every_expand_run_ends_with_article_picks(tmp_path):
    out, _ = run_expand("reef tank", load_niche("reef"), cache_dir=tmp_path / "cache", out_root=tmp_path / "runs",
                        http=FakeHttp(), prefix_top=0)
    md = (out / "articles.md").read_text(encoding="utf-8")
    assert "free engine signals only" in md and "no site catalog" in md
    picks = json.loads((out / "articles.json").read_text(encoding="utf-8"))["picks"]
    assert picks and all(a["wave"] == 2 for a in picks)
    assert len(list((out / "briefs").glob("*.md"))) == min(10, len(picks))
