"""Article picks and briefs: which articles a site should write next, and a data-only brief for each.

Runs at the end of every stage (expand, tools, volume, shortlist) and uses whatever evidence the run
has by then: engine agreement after a free run, real volume after the volume check, KD and the live
top 10 from the ledgers once a shortlist has checked them. No LLM and no blended score:

1. every phrase gets a role with a plain reason (calculator page, needs prices, local, off topic,
   low volume, article);
2. phrases become one article: Google's merged close variants (same volume, CPC and 12-month curve),
   phrases about the same calculator and the same facet (sizes, weight, per pallet…), and phrases whose
   live top 10s share SERP_OVERLAP or more URLs. The facet rule is there because Google often shows
   different results for searches one page should answer ("brick sizes" / "brick sizes standard"
   shared 2 of 10 in October 2026), and two pages would compete with each other;
3. a top 10 that is mostly calculators sends the topic to that calculator page's FAQ instead;
4. articles are ranked by the plain average of their ranks, like `compare`. Wave 1 is the articles
   that link to a calculator in the site catalog, because articles exist to send people to the tools.

Writes articles.json, articles.md and briefs/<slug>.md into the run folder. A brief whose status
someone changed from `candidate` is never overwritten.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from .analyze import QUESTION_STARTS, STOP_WORDS, avg_ranks, contains_word, normalize, stem
from .cache import slug
from .shortlist import Store
from .volume import Ledger

REPO_ROOT = Path(__file__).resolve().parent.parent
LOC, LANG = 2840, "en"
MIN_VOLUME = 100        # monthly searches below this aren't worth a page
SERP_OVERLAP = 4        # shared top-10 URLs that make two searches one article
CALCULATOR_SERP = 6     # calculators in the top 10 that make it a calculator page's job
BRIEFS = 10

TOOL_WORDS = re.compile(r"\b(calculators?|calculate|calculating|calculation|calc|estimators?|estimate|estimating)\b")
PRICE_WORDS = re.compile(r"\b(costs?|prices?|pricing|priced|cheap|cheapest|futures|for sale|quotes?)\b"
                         r"|\bhow much (is|are|to|for)\b")
CHART_WORDS = re.compile(r"\b(sizes?|chart|table|dimensions?|weights?|weigh|spans?|gauges?|grades?|types?)\b")
HOWTO_WORDS = re.compile(r"^(how to|how do (i|you))\b|\b(install|installing|build|building|pour|pouring|lay|"
                         r"laying|mix|mixing|repair|fix)\b")
ANSWER_WORDS = re.compile(r"^how (many|much|long|thick|deep|far|big)\b|\bper\b|\bin an? \b")
FILLER = STOP_WORDS | {  # words that say which facet or unit, not which topic
    "calculator", "chart", "table", "size", "sizes", "dimension", "dimensions", "standard", "weight", "weights",
    "weigh", "weighs", "heavy", "bag", "bags", "pallet", "pallets", "cube", "cubes", "coverage", "per",
    "inch", "inches", "feet", "foot", "ft", "cubic", "yard", "yards", "ton", "tons", "pound", "pounds", "lb", "lbs",
    "square", "sq"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
FACETS = [  # first match wins: "rebar weight chart" is about weight
    ("weight", re.compile(r"\b(weights?|weigh|weighs|heavy)\b")),
    ("pallet", re.compile(r"\b(pallets?|cubes?)\b")),
    ("bags", re.compile(r"\bbags?\b")),
    ("coverage", re.compile(r"\b(coverage|per square (foot|feet)|per sq ft)\b")),
    ("sizes", re.compile(r"\b(sizes?|dimensions?|chart|table)\b")),
]


# --- inputs ---

def load_catalog(path):
    """The site's page list (the-plumb-bob's catalog.json), or None when there isn't one."""
    path = Path(path) if path else None
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(run_dir):
    for name in ("volume.jsonl", "phrases.jsonl", "tools.jsonl"):
        path = run_dir / name
        if path.exists():
            rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]
            return rows, name == "volume.jsonl"
    return [], False


def _json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _has(word, phrase):
    """Whole-word match that tolerates a plural: 'block' finds 'blocks'."""
    return re.search(r"\b" + re.escape(word) + r"(?:s|es)?\b", phrase) is not None


def _page_words(page):
    return [w for w in [normalize(page["name"]).replace("&", "").strip()] + [normalize(t) for t in page.get("topics", [])] if w]


def match(phrase, catalog):
    """(page, topic word) for the catalog page whose name or topic words appear in the phrase.

    The longest matching word wins, so 'pea gravel' goes to pea gravel, not gravel. (None, None) if none.
    """
    best = (0, None, None)
    for page in (catalog or {}).get("pages", []):
        for w in _page_words(page):
            if len(w) > best[0] and _has(w, phrase):
                best = (len(w), page, w)
    return best[1], best[2]


def page_for(phrase, catalog):
    return match(phrase, catalog)[0]


def content_stems(phrase):
    return {stem(t) for t in phrase.split() if t not in FILLER and not t.isdigit() and len(t) > 1}


# --- roles ---

def role(row, niche, has_volume, catalog):
    """(role, reason) for one phrase."""
    p = row["phrase"]
    names = [p] + row.get("variants", [])
    skip = next((w for w in niche.get("skip_words", []) if any(contains_word(w, n) for n in names)), None)
    if skip:
        return "off topic", f"skip word '{skip}'"
    if any(TOOL_WORDS.search(n) for n in names):
        page = page_for(p, catalog)
        return "calculator page", (f"owned by {page['path']}" if page else "tool idea: no calculator page yet")
    if any(PRICE_WORDS.search(n) for n in names):
        return "needs prices", "parked: no invented prices"
    if row.get("intent") == "local":
        return "local", "parked: local search (lead-gen later)"
    if has_volume and (row.get("search_volume") or 0) < MIN_VOLUME:
        return "low volume", f"under {MIN_VOLUME} searches a month"
    return "article", ""


def page_type(phrases):
    """From the main phrase; the others only decide when the main phrase says nothing."""
    for group_ in ([phrases[0]], phrases):
        if any(HOWTO_WORDS.search(p) for p in group_):
            return "how-to"
        if any(ANSWER_WORDS.search(p) for p in group_):
            return "answer guide"
        if any(CHART_WORDS.search(p) for p in group_):
            return "reference chart"
    return "guide"


def season(series):
    """'peaks Apr, May, Jun' from a 12-month series, 'steady' when it barely moves, None when unknown."""
    months = {}
    for m in series or []:
        if m.get("search_volume") is not None:
            months.setdefault(m["month"], m["search_volume"])
    if len(months) < 6:
        return None
    hi, lo = max(months.values()), min(months.values())
    if hi < 1.5 * max(lo, 1):
        return "steady"
    top = sorted(sorted(months, key=lambda m: -months[m])[:3])
    return "peaks " + ", ".join(MONTHS[m - 1] for m in top)


def facet(phrase):
    return next((name for name, pattern in FACETS if pattern.search(phrase)), None)


def calculator_share(serp):
    """How many of the live top 10 are calculators."""
    return sum(1 for t in serp.get("top10", []) if TOOL_WORDS.search(f"{t.get('title') or ''} {t.get('url') or ''}".lower()))


# --- grouping ---

class _Union:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def group(rows, series_of, serp_of, match_of):
    """Article rows -> lists of rows, one list per article.

    - Same volume + CPC + 12-month series + calculator, sharing a content word = one Google search.
    - Same calculator, facet and main topic word ('block' in cinder block / concrete block), where one
      phrase's other words are a subset of the other's = one article. 'brick size in inches' joins
      'brick sizes'; 'sonotube sizes' and 'concrete patio size' stay apart.
    - Searches whose live top 10s share SERP_OVERLAP URLs = one article.
    """
    n = len(rows)
    u = _Union(n)
    matches = [match_of(r) for r in rows]
    by_key = {}
    for i, r in enumerate(rows):
        series = tuple(m.get("search_volume") for m in series_of(r) or [])
        if r.get("search_volume") is not None and series:
            page = matches[i][0]
            by_key.setdefault((r["search_volume"], r.get("cpc"), series, page and page["slug"]), []).append(i)
    for members in by_key.values():
        for a in members:
            for b in members:
                if a < b and content_stems(rows[a]["phrase"]) & content_stems(rows[b]["phrase"]):
                    u.join(a, b)
    by_facet = {}
    for i, r in enumerate(rows):
        (page, word), f = matches[i], facet(r["phrase"])
        if page and f:
            by_facet.setdefault((page["slug"], f, stem(word.split()[-1])), []).append(i)
    for members in by_facet.values():
        members.sort(key=lambda i: -(rows[i].get("search_volume") or 0))
        page = matches[members[0]][0]
        own = {stem(t) for w in _page_words(page) for t in w.split()}
        anchors = []  # (index, other words): each phrase joins the first compatible, bigger phrase
        for i in members:
            rest = content_stems(rows[i]["phrase"]) - own
            hit = next((a for a, words in anchors if rest <= words or words <= rest), None)
            if hit is None:
                anchors.append((i, rest))
            else:
                u.join(i, hit)
    urls = {i: {t["url"] for t in (serp_of(r) or {}).get("top10", []) if t.get("url")} for i, r in enumerate(rows)}
    with_serp = [i for i in range(n) if urls[i]]
    for x, a in enumerate(with_serp):
        for b in with_serp[x + 1:]:
            if len(urls[a] & urls[b]) >= SERP_OVERLAP:
                u.join(a, b)
    groups = {}
    for i in range(n):
        groups.setdefault(u.find(i), []).append(rows[i])
    return list(groups.values())


# --- the step ---

def pick(run_dir, niche, catalog=None, data_dir=None):
    """Everything the report and briefs need, as plain data."""
    run_dir = Path(run_dir)
    data_dir = Path(data_dir or REPO_ROOT / "data")
    rows, has_volume = _rows(run_dir)
    volume, kd, serp = Ledger(data_dir / "volume_ledger.json"), Store(data_dir / "kd_ledger.json"), Store(data_dir / "serp_ledger.json")

    def first(store, r):
        for k in [r["phrase"]] + r.get("variants", []):
            e = store.get(k, LOC, LANG)
            if e:
                return e
        return None

    def series_of(r):
        return (first(volume, r) or {}).get("monthly_searches") if has_volume else None

    targets = {normalize(t): p for p in (catalog or {}).get("pages", []) for t in p.get("targets", [])}
    roles, articles = {}, []
    for r in rows:
        name, reason = role(r, niche, has_volume, catalog)
        if name == "article":
            articles.append(r)
        else:
            roles.setdefault(name, []).append(dict(phrase=r["phrase"], volume=r.get("search_volume"), reason=reason))

    picks, faq = [], []
    for members in group(articles, series_of, lambda r: first(serp, r), lambda r: match(r["phrase"], catalog)):
        members.sort(key=lambda r: (-(r.get("search_volume") or 0), -r.get("engine_count", 0),
                                    -r.get("times_seen", 0), -(r.get("google_relevance") or 0), len(r["phrase"])))
        head = members[0]
        page = page_for(head["phrase"], catalog) or next(
            (p for p in (page_for(m["phrase"], catalog) for m in members) if p), None)
        k = next((e.get("keyword_difficulty") for e in (first(kd, m) for m in members)
                  if e and e.get("keyword_difficulty") is not None), None)
        s = next((e for e in (first(serp, m) for m in members) if e and e.get("top10")), None)
        searches = {}  # one volume per distinct Google search: merged variants share a number
        for m in members:
            key = (m.get("search_volume"), m.get("cpc"))
            searches.setdefault(key, m.get("search_volume") or 0)
        phrases = [m["phrase"] for m in members]
        covered = next((targets[normalize(p)]["path"] for p in phrases if normalize(p) in targets), None)
        item = {
            "head": head["phrase"], "phrases": phrases, "slug": slug(head["phrase"]).replace("_", "-"),
            "page_type": page_type(phrases), "links_to": page["path"] if page else None,
            "wave": 1 if page else 2, "volume": sum(searches.values()) if has_volume else None,
            "head_volume": head.get("search_volume"), "cpc": head.get("cpc"), "kd": k,
            "season": season(series_of(head)), "serp": s, "covered_by": covered,
            "serp_edge": (s["weak_results"] - s["big_sites"]) if s else None,
            "engine_count": head.get("engine_count"), "times_seen": head.get("times_seen"),
            "google_relevance": head.get("google_relevance"), "cluster_id": head.get("cluster_id"),
        }
        if covered:
            roles.setdefault("covered", []).append(dict(phrase=item["head"], volume=item["volume"],
                                                        reason=f"already covered by {covered}"))
        elif s and calculator_share(s) >= CALCULATOR_SERP:
            item["reason"] = f"{calculator_share(s)} of the top 10 are calculators"
            faq.append(item)
        else:
            picks.append(item)

    signals = ([("volume", True), ("kd", False), ("serp_edge", True)] if has_volume
               else [("engine_count", True), ("times_seen", True), ("google_relevance", True)])
    avg_ranks(picks, signals)
    picks.sort(key=lambda a: (a["wave"], a["avg_rank"]))
    for name in roles:
        roles[name].sort(key=lambda x: -(x["volume"] or 0))
    return {"has_volume": has_volume, "has_catalog": catalog is not None, "picks": picks,
            "calculator_faq": sorted(faq, key=lambda a: -(a["volume"] or 0)), "roles": roles,
            "signals": [s for s, _ in signals]}


def refresh(run_dir, data_dir=None, briefs=BRIEFS, log=print):
    """The articles step for a finished run: reads its niche from run.json and writes the outputs."""
    from .pipeline import load_niche  # here, not at the top: pipeline imports this module

    run_dir = Path(run_dir)
    run = _json(run_dir / "run.json", {})
    if run.get("mode") not in ("expand", "tools"):
        return None
    niche = load_niche(run.get("niche", "default"))
    cat_path = niche.get("site_catalog")
    catalog = load_catalog((REPO_ROOT / cat_path).resolve() if cat_path else None)
    result = pick(run_dir, niche, catalog, data_dir)
    result.update(run_id=run.get("run_id"), niche=niche["name"], catalog=cat_path if catalog else None,
                  generated_at=datetime.now().astimezone().isoformat(timespec="seconds"))
    (run_dir / "articles.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "articles.md").write_text(render_md(result), encoding="utf-8")
    written = write_briefs(run_dir, result, _json(run_dir / "questions.json", []), _json(run_dir / "clusters.json", []),
                           briefs)
    log(f"Articles: {len(result['picks'])} picks, {written} brief(s). Wrote {run_dir / 'articles.md'}")
    return result


# --- output ---

def _n(v, fmt="{:,}"):
    return fmt.format(v) if isinstance(v, (int, float)) else "–"


def render_md(r):
    evidence = ("real volume" + (", KD and live top 10 where checked" if any(a["kd"] is not None for a in r["picks"]) else "")
                if r["has_volume"] else "free engine signals only (no volume check yet)")
    lines = ["# Article picks", "",
             f"Run `{r.get('run_id')}`. Evidence: {evidence}. Ranked by the plain average of ranks on "
             f"{', '.join(r['signals'])}; no weights.",
             "Wave 1 links to a live calculator" + ("" if r["has_catalog"] else
                                                    " (no site catalog found, so every pick is wave 2)") + ".", ""]

    def table(items, top):
        out = ["| # | Article (main phrase) | Type | Searches/mo | KD | Big/weak | Links to | Season | Also covers |",
               "|---|---|---|---|---|---|---|---|---|"]
        for i, a in enumerate(items[:top], 1):
            s = a["serp"]
            big_weak = f"{s['big_sites']}/{s['weak_results']}" if s else "–"
            out.append(f"| {i} | {a['head']} | {a['page_type']} | {_n(a['volume'])} | {_n(a['kd'])} | {big_weak} | "
                       f"{a['links_to'] or '–'} | {a['season'] or '–'} | {', '.join(a['phrases'][1:4]) or '–'} |")
        return out

    wave1 = [a for a in r["picks"] if a["wave"] == 1]
    wave2 = [a for a in r["picks"] if a["wave"] == 2]
    if wave1:
        lines += ["## Wave 1: links to a calculator", ""] + table(wave1, 25) + [""]
    if wave2:
        lines += ["## Wave 2: no calculator to link yet", ""] + table(wave2, 10) + [""]
    if r["calculator_faq"]:
        lines += ["## Calculator page FAQ (the live top 10 is mostly calculators)", ""]
        lines += [f"- {a['head']} ({_n(a['volume'])}/mo) -> {a['links_to'] or 'no page yet'}: {a['reason']}"
                  for a in r["calculator_faq"][:15]] + [""]
    ideas = [x for x in r["roles"].get("calculator page", []) if x["reason"].startswith("tool idea")]
    if ideas:
        lines += ["## Tool ideas (calculator searches with no page)", ""]
        lines += [f"- {x['phrase']} ({_n(x['volume'])}/mo)" for x in ideas[:12]] + [""]
    lines += ["## Left out", "", "| Role | Phrases | Biggest |", "|---|---|---|"]
    for name, items in sorted(r["roles"].items()):
        top = ", ".join(f"{x['phrase']} ({_n(x['volume'])})" if x["volume"] else x["phrase"] for x in items[:3])
        lines.append(f"| {name} | {len(items)} | {top} |")
    lines += ["", "Briefs for the top picks are in `briefs/`. Reasons for every phrase are in `articles.json`."]
    return "\n".join(lines) + "\n"


def _related_questions(head, phrases, questions, limit=8):
    want = content_stems(head)
    need = min(2, len(want))
    out = [q for q in questions if need and len(want & content_stems(normalize(q.get("title", "")))) >= need]
    return out[:limit]


def _status(path):
    if not path.exists():
        return None
    m = re.search(r"^status:\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def render_brief(a, questions=(), subtopics=()):
    s = a["serp"] or {}
    fm = ["---", f"target: {a['head']}", f"variants: [{', '.join(a['phrases'][1:])}]",
          f"page_type: {a['page_type']}", f"links_to: {a['links_to'] or 'none yet'}",
          f"volume: {a['volume'] if a['volume'] is not None else 'unchecked'}   # Google US monthly, all phrases",
          f"kd: {a['kd'] if a['kd'] is not None else 'unchecked'}",
          f"season: {a['season'] or 'unknown'}", "status: candidate   # candidate -> writing -> review -> published",
          "---", "", f"# Brief: {a['head']}", ""]
    body = ["## Who ranks now", ""]
    if s.get("top10"):
        body += [f"{t['rank']}. {t['domain']}: {t.get('title') or ''}" for t in s["top10"]]
        feats = ", ".join(s.get("features", [])) or "none"
        body += ["", f"Big sites: {s['big_sites']}. Weak results (forums, video, Pinterest): {s['weak_results']}. "
                     f"Features: {feats}."]
        if "people_also_ask" in s.get("features", []):
            body.append("People Also Ask shows: answer each question in its own short section.")
        if {"featured_snippet", "answer_box", "ai_overview"} & set(s.get("features", [])):
            body.append("Google shows a direct answer: open with a 40–60 word answer to the main phrase.")
    else:
        body.append("Not checked yet (run the shortlist step for this phrase).")
    body += ["", "## Searches this page should answer", ""]
    body += [f"- {p}" for p in a["phrases"]]
    asked = [p for p in a["phrases"] if p.startswith(QUESTION_STARTS)]
    if asked or questions:
        body += ["", "## Questions people ask", ""] + [f"- {p}" for p in asked]
        body += [f"- [{q['title']}]({q['link']}) ({_n(q.get('views'))} views, {q.get('site')})" for q in questions]
    if subtopics:
        body += ["", "## Related phrases (candidate sections)", ""] + [f"- {p}" for p in subtopics]
    body += ["", "## Facts to source", "",
             "Fill in while writing. Every number on the page needs a source here, or it doesn't publish.", "",
             "- [ ] fact: … source: …", "",
             "## Don't", "",
             "- No prices: cost only appears from a price the visitor types.",
             "- No brand names in the title.",
             f"- Link to {a['links_to']} and don't compete with it for the calculator search." if a["links_to"]
             else "- No calculator to link yet: consider whether this belongs on the site now."]
    return "\n".join(fm + body) + "\n"


def write_briefs(run_dir, result, questions, clusters, limit=BRIEFS):
    out = Path(run_dir) / "briefs"
    picks = [a for a in result["picks"] if a["wave"] == 1] if result["has_catalog"] else result["picks"]
    by_cluster = {c["id"]: c for c in clusters}
    written = 0
    for a in picks[:limit]:
        path = out / f"{a['slug']}.md"
        if _status(path) not in (None, "candidate"):
            continue  # someone started on it; leave it alone
        cluster = by_cluster.get(a["cluster_id"]) or {}
        members = [m["phrase"] if isinstance(m, dict) else m for m in cluster.get("members", [])]
        subtopics = [p for p in members if p not in a["phrases"]][:12]
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(render_brief(a, _related_questions(a["head"], a["phrases"], questions), subtopics),
                        encoding="utf-8")
        written += 1
    return written
