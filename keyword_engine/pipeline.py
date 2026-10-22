"""The two modes: expand (one seed -> ranked phrases + clusters) and compare (rank many seeds)."""
import json
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from .analyze import (Matcher, build_queries, build_rows, cluster, collect, min_prefix_len,
                      normalize, rank_questions)
from .cache import Cache, slug
from .report import validate, write_compare, write_expand
from .sources import SUGGEST_ENGINES, HttpClient, SourceLog, Sources

REPO_ROOT = Path(__file__).resolve().parent.parent
NICHES_DIR = REPO_ROOT / "niches"


def load_niche(name_or_path="default"):
    path = Path(name_or_path)
    if not path.suffix:
        path = NICHES_DIR / f"{name_or_path}.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    defaults = json.loads((NICHES_DIR / "default.json").read_text(encoding="utf-8"))
    return {**defaults, **cfg, "region": {**defaults["region"], **cfg.get("region", {})}}


def _make_sources(niche, cache_dir, refresh, http):
    log = SourceLog()
    cache = Cache(cache_dir or REPO_ROOT / "cache", refresh=refresh)
    return Sources(http or HttpClient(), cache, niche["region"], log), log


def _run_id(label):
    return f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}-{slug(label, 40)}"


def run_expand(seed, niche, engines=SUGGEST_ENGINES, cache_dir=None, out_root=None,
               refresh=False, prefix_top=25, progress=None, http=None):
    started = time.monotonic()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    sources, log = _make_sources(niche, cache_dir, refresh, http)
    matcher = Matcher(seed, niche.get("synonyms", []))

    plan = build_queries(seed)
    evidence = collect(sources, list(engines), plan, progress)
    phrases, dropped = build_rows(evidence, matcher, niche.get("skip_words", []), matcher.seed, niche["name"])
    clusters = cluster(phrases, matcher)

    if prefix_top and "google" in engines:
        for p in phrases[:prefix_top]:
            p["min_prefix_len"] = min_prefix_len(p["phrase"], lambda q: sources.suggest("google", q))

    questions = []
    for site in niche.get("stackexchange_sites", []):
        questions += sources.stackexchange(matcher.seed, site)
    questions = rank_questions(questions, matcher)

    wiki = sources.wiki_monthly_views(matcher.seed)

    run = {
        "engine_version": __version__,
        "run_id": _run_id(seed),
        "mode": "expand",
        "seed": matcher.seed,
        "niche": niche["name"],
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 1),
        "settings": {"engines": list(engines), "refresh": refresh, "prefix_top": prefix_top,
                     "queries_per_engine": len(plan), "region": niche["region"],
                     "stackexchange_sites": niche.get("stackexchange_sites", []),
                     "synonyms": niche.get("synonyms", [])},
        "sources": log.stats,
        "sources_ok": log.ok_sources(),
        "counts": {"raw_phrases": len(evidence), "phrases": len(phrases), "clusters": len(clusters),
                   "questions": len(questions), "dropped": dict(dropped)},
        "wikipedia": wiki,
        "cost_usd": 0,
    }
    run["validation"] = validate(run, phrases)
    run["validation_passed"] = all(c["ok"] for c in run["validation"])
    out = Path(out_root or REPO_ROOT / "runs") / run["run_id"]
    write_expand(out, run, phrases, clusters, questions)
    return out, run


def _avg_ranks(rows, signals):
    """Adds row["avg_rank"]: mean of per-signal ranks (1 = best, ties share, missing = last)."""
    n = len(rows)
    for key, higher_is_better in signals:
        values = [r[key] for r in rows if r[key] is not None]
        for r in rows:
            if r[key] is None:
                r.setdefault("_ranks", []).append(n)
            else:
                better = sum(1 for v in values if (v > r[key] if higher_is_better else v < r[key]))
                r.setdefault("_ranks", []).append(better + 1)
    for r in rows:
        r["avg_rank"] = round(sum(r["_ranks"]) / len(r["_ranks"]), 2)
        del r["_ranks"]
    rows.sort(key=lambda r: r["avg_rank"])
    return rows


def run_compare(seeds, niche, cache_dir=None, out_root=None, refresh=False, progress=None, http=None):
    started = time.monotonic()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    sources, log = _make_sources(niche, cache_dir, refresh, http)
    rows = []
    for i, raw in enumerate(seeds, 1):
        seed = normalize(raw)
        matcher = Matcher(seed, niche.get("synonyms", []))
        found = set()
        for kind, q in [x for x in build_queries(seed) if x[0] in ("seed", "suffix")]:
            for hit in sources.suggest("google", q):
                p = normalize(hit["phrase"])
                if matcher.span(p):
                    found.add(p)
        rows.append({
            "seed": seed,
            "suggest_phrases": len(found),
            "seed_prefix_len": min_prefix_len(seed, lambda q: sources.suggest("google", q)),
            "wikipedia": sources.wiki_monthly_views(seed),
        })
        rows[-1]["wiki_views"] = (rows[-1]["wikipedia"] or {}).get("avg_monthly_views")
        if progress:
            progress("compare", i, len(seeds))
    _avg_ranks(rows, [("suggest_phrases", True), ("seed_prefix_len", False), ("wiki_views", True)])
    for r in rows:
        del r["wiki_views"]
    run = {
        "engine_version": __version__,
        "run_id": _run_id(f"compare-{len(seeds)}-seeds"),
        "mode": "compare",
        "seeds": [r["seed"] for r in rows],
        "niche": niche["name"],
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "duration_s": round(time.monotonic() - started, 1),
        "settings": {"engines": ["google"], "refresh": refresh, "region": niche["region"]},
        "sources": log.stats,
        "sources_ok": log.ok_sources(),
        "cost_usd": 0,
    }
    run["validation"] = [
        {"name": "has_seeds", "ok": len(rows) > 0, "detail": f"{len(rows)} seeds"},
        {"name": "google_worked", "ok": "google" in run["sources_ok"], "detail": "Google autocomplete returned data"},
    ]
    run["validation_passed"] = all(c["ok"] for c in run["validation"])
    out = Path(out_root or REPO_ROOT / "runs") / run["run_id"]
    write_compare(out, run, rows)
    return out, run
