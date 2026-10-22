"""Writes a run folder: phrases.jsonl/.csv, clusters.json, questions.json, run.json, report.md."""
import csv
import json
from pathlib import Path

CSV_FIELDS = ["phrase", "cluster_id", "intent", "engine_count", "times_seen", "google_relevance",
              "min_prefix_len", "best_rank", "modifier", "shape", "engines", "seed", "niche"]


def validate(run, phrases, min_sources=3):
    """Checks that must pass before a run counts as done. Returns list of {name, ok, detail}."""
    names = [p["phrase"] for p in phrases]
    ok_sources = run["sources_ok"]
    needed = min(min_sources, len(run["settings"]["engines"]))
    checks = [
        {"name": "has_phrases", "ok": len(phrases) > 0, "detail": f"{len(phrases)} phrases"},
        {"name": "no_duplicates", "ok": len(names) == len(set(names)),
         "detail": f"{len(names) - len(set(names))} duplicates"},
        {"name": "all_on_topic", "ok": all(p["shape"].count("{keyword}") >= 1 for p in phrases),
         "detail": "every phrase contains the seed or a synonym"},
        {"name": "enough_sources", "ok": len([s for s in ok_sources if s in run["settings"]["engines"]]) >= needed,
         "detail": f"{len(ok_sources)} sources returned data (need {needed} autocomplete engines)"},
    ]
    return checks


def _md_escape(text):
    return str(text).replace("|", "\\|")


def render_expand_md(run, phrases, clusters, questions, top_clusters=20, top_phrases=30):
    lines = [f"# Keywords: {run['seed']}", "",
             f"Niche `{run['niche']}` · run `{run['run_id']}` · {run['counts']['phrases']} phrases in "
             f"{run['counts']['clusters']} clusters · {run['duration_s']} s", ""]
    if not run["validation_passed"]:
        failed = [c["name"] for c in run["validation"] if not c["ok"]]
        lines += [f"**Validation failed:** {', '.join(failed)}. Treat this run as incomplete.", ""]

    lines += ["## Top clusters", "", "| Cluster | Phrases | Head phrase | Also |", "|---|---|---|---|"]
    for c in clusters[:top_clusters]:
        also = ", ".join(c["members"][1:4])
        lines.append(f"| {_md_escape(c['key'])} | {c['size']} | {_md_escape(c['head'])} | {_md_escape(also)} |")

    lines += ["", "## Top phrases", "",
              "Ranked by engines that suggested it, then queries that surfaced it, then Google relevance.", "",
              "| Phrase | Engines | Seen | Google rel. | Prefix | Intent |", "|---|---|---|---|---|---|"]
    for p in phrases[:top_phrases]:
        prefix = p["min_prefix_len"] if p["min_prefix_len"] is not None else ""
        lines.append(f"| {_md_escape(p['phrase'])} | {p['engine_count']} | {p['times_seen']} | "
                     f"{p['google_relevance'] or ''} | {prefix} | {p['intent']} |")

    if questions:
        lines += ["", "## Top questions (Stack Exchange, by views)", "",
                  "| Question | Views | Answers | Site |", "|---|---|---|---|"]
        for q in questions[:15]:
            lines.append(f"| [{_md_escape(q['title'])}]({q['link']}) | {q['views']:,} | {q['answers']} | {q['site']} |")

    if run.get("wikipedia"):
        w = run["wikipedia"]
        lines += ["", f"Wikipedia: **{w['title']}** averages {w['avg_monthly_views']:,} views/month "
                      f"(last {w['months']} months)."]

    lines += ["", "## Source health", "", "| Source | Requests | Live | Cached | Stale | Failed |",
              "|---|---|---|---|---|---|"]
    for name, s in run["sources"].items():
        lines.append(f"| {name} | {s['attempted']} | {s['live']} | {s['cache']} | {s['stale']} | {s['failed']} |")
    errors = [(n, e) for n, s in run["sources"].items() for e in s["errors"]]
    if errors:
        lines += ["", "First errors:"] + [f"- {n}: {_md_escape(e)[:160]}" for n, e in errors[:8]]
    if run["counts"]["dropped"]:
        dropped = ", ".join(f"{k} {v}" for k, v in sorted(run["counts"]["dropped"].items()))
        lines += ["", f"Dropped phrases: {dropped}."]
    return "\n".join(lines) + "\n"


def render_compare_md(run, seeds):
    lines = [f"# Seed comparison ({len(seeds)} seeds)", "",
             f"Niche `{run['niche']}` · run `{run['run_id']}` · {run['duration_s']} s", "",
             "Each signal is ranked separately; **avg rank** is the plain average of those ranks "
             "(lower is better). Missing values rank last.", "",
             "| # | Seed | Avg rank | Suggest phrases | Seed prefix | Wikipedia views/mo | Wikipedia article |",
             "|---|---|---|---|---|---|---|"]
    for i, s in enumerate(seeds, 1):
        w = s["wikipedia"] or {}
        lines.append(f"| {i} | {_md_escape(s['seed'])} | {s['avg_rank']} | {s['suggest_phrases']} | "
                     f"{s['seed_prefix_len'] if s['seed_prefix_len'] is not None else ''} | "
                     f"{w.get('avg_monthly_views', '')} | {_md_escape(w.get('title', ''))} |")
    lines += ["", "## Source health", "", "| Source | Requests | Live | Cached | Stale | Failed |",
              "|---|---|---|---|---|---|"]
    for name, s in run["sources"].items():
        lines.append(f"| {name} | {s['attempted']} | {s['live']} | {s['cache']} | {s['stale']} | {s['failed']} |")
    return "\n".join(lines) + "\n"


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_expand(out_dir, run, phrases, clusters, questions):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "phrases.jsonl", "w", encoding="utf-8") as f:
        for p in phrases:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(out / "phrases.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for p in phrases:
            w.writerow(dict(p, engines=" ".join(p["engines"])))
    _write_json(out / "clusters.json", clusters)
    _write_json(out / "questions.json", questions)
    (out / "report.md").write_text(render_expand_md(run, phrases, clusters, questions), encoding="utf-8")
    _write_json(out / "run.json", run)
    return out


def write_compare(out_dir, run, seeds):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "seeds.json", seeds)
    with open(out / "seeds.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rank", "seed", "avg_rank", "suggest_phrases", "seed_prefix_len",
                    "wikipedia_views_per_month", "wikipedia_title"])
        for i, s in enumerate(seeds, 1):
            wk = s["wikipedia"] or {}
            w.writerow([i, s["seed"], s["avg_rank"], s["suggest_phrases"], s["seed_prefix_len"],
                        wk.get("avg_monthly_views"), wk.get("title")])
    (out / "report.md").write_text(render_compare_md(run, seeds), encoding="utf-8")
    _write_json(out / "run.json", run)
    return out
