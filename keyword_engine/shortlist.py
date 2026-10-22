"""Optional paid step 2: check a shortlist for keyword difficulty (DataForSEO Labs)
and who holds Google's top 10 right now (DataForSEO SERP API).

Runs after the volume check: the shortlist is the top N phrases by real search
volume. Same cost guards as volume.py: dry run unless --spend, a --max-cost cap
checked before sending, ledgers so nothing is paid for twice, and queued SERP
tasks saved before waiting.

Nothing here blends the numbers into a score. The report lays them side by side
for a person to choose from.
"""
import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .volume import MAX_KEYWORDS, VolumeError

KD_TASK_COST, KD_PER_KEYWORD = 0.012, 0.00012  # DataForSEO Labs, per task + per keyword (2026-10)
SERP_COST = 0.0006  # Google organic, standard queue, per SERP of 10 results (2026-10)
SERP_PER_POST = 100
LABS_KD = "/dataforseo_labs/google/bulk_keyword_difficulty/live"
SERP = "/serp/google/organic"

# Established sites that are hard to outrank, and results that signal a thin top 10.
BIG_SITES = {"homedepot.com", "lowes.com", "menards.com", "omnicalculator.com", "calculator.net",
             "inchcalculator.com", "calculatorsoup.com", "angi.com", "homeadvisor.com", "forbes.com",
             "thisoldhouse.com", "bobvila.com", "thespruce.com", "familyhandyman.com", "bhg.com",
             "quikrete.com", "sakrete.com", "wikipedia.org", "amazon.com", "homeguide.com", "fixr.com",
             "thumbtack.com", "bankrate.com", "nerdwallet.com", "remodelingcalculator.org"}
WEAK_SITES = {"reddit.com", "quora.com", "youtube.com", "pinterest.com", "facebook.com", "houzz.com",
              "contractortalk.com", "diychatroom.com", "garden.org", "stackexchange.com", "tiktok.com",
              "instagram.com", "answers.com"}


class Store:
    """A small ledger: results keyed by keyword/location/language, plus queued tasks."""

    def __init__(self, path, max_age_days=90):
        self.path = Path(path)
        self.max_age = timedelta(days=max_age_days)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.items = data.get("items", {})
        self.pending = data.get("pending", [])

    @staticmethod
    def key(keyword, loc, lang):
        return f"{keyword}|{loc}|{lang}"

    def fresh(self, keyword, loc, lang):
        e = self.items.get(self.key(keyword, loc, lang))
        return bool(e) and datetime.now(timezone.utc) - datetime.fromisoformat(e["fetched_at"]) < self.max_age

    def get(self, keyword, loc, lang):
        return self.items.get(self.key(keyword, loc, lang))

    def put(self, keyword, loc, lang, payload):
        self.items[self.key(keyword, loc, lang)] = dict(payload, fetched_at=datetime.now(timezone.utc).isoformat())

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": self.items, "pending": self.pending}, indent=1), encoding="utf-8")
        tmp.replace(self.path)


# --- client calls (the DataForSEO class lives in volume.py; these use its _call) ---

def fetch_kd(client, keywords, loc, lang):
    """Keyword difficulty for up to 1,000 keywords in one live call. Returns ({keyword: kd}, cost)."""
    data = client._call("POST", LABS_KD, [{"keywords": keywords, "location_code": loc, "language_code": lang}])
    task = data["tasks"][0]
    if task["status_code"] != 20000:
        raise VolumeError(f"KD task failed: {task['status_code']} {task.get('status_message')}")
    items = ((task.get("result") or [{}])[0] or {}).get("items") or []
    return {i["keyword"].lower(): i.get("keyword_difficulty") for i in items}, data.get("cost", 0)


def post_serps(client, keywords, loc, lang):
    """Queues one top-10 SERP task per keyword (up to 100 per call). Returns ([(keyword, id)], cost)."""
    body = [{"keyword": k, "location_code": loc, "language_code": lang, "depth": 10} for k in keywords]
    data = client._call("POST", SERP + "/task_post", body)
    out = []
    for kw, task in zip(keywords, data["tasks"]):
        if task["status_code"] != 20100:
            raise VolumeError(f"SERP task for '{kw}' not created: {task['status_code']} {task.get('status_message')}")
        out.append((kw, task["id"]))
    return out, data.get("cost", 0)


def get_serp(client, task_id):
    """The finished SERP result, or None while queued. Free."""
    data = client._call("GET", f"{SERP}/task_get/regular/{task_id}")
    task = data["tasks"][0]
    if task["status_code"] in (40601, 40602):
        return None
    if task["status_code"] != 20000:
        raise VolumeError(f"SERP task {task_id} failed: {task['status_code']} {task.get('status_message')}")
    return (task.get("result") or [{}])[0] or {}


def _base_domain(domain):
    parts = (domain or "").lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        return ".".join(parts[-3:])  # example.co.uk
    return ".".join(parts[-2:]) if len(parts) >= 2 else ".".join(parts)


def summarize_serp(result):
    """What a person needs from a top 10: who is there, how many big/weak, what features show."""
    organic = [i for i in result.get("items") or [] if i.get("type") == "organic"][:10]
    domains = [_base_domain(i.get("domain")) for i in organic]
    return {
        "top10": [{"rank": i.get("rank_group"), "domain": d, "title": i.get("title"), "url": i.get("url")}
                  for i, d in zip(organic, domains)],
        "big_sites": sum(1 for d in domains if d in BIG_SITES),
        "weak_results": sum(1 for d in domains if d in WEAK_SITES),
        "features": sorted(set(result.get("item_types") or []) - {"organic"}),
    }


# --- the step ---

def _load_rows(run_dir):
    path = Path(run_dir) / "volume.jsonl"
    if not path.exists():
        raise VolumeError("no volume.jsonl in that run; run the volume check first")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]


def pick(rows, top):
    """The top N phrases by real search volume (zero and unknown volume left out)."""
    return sorted((r for r in rows if r.get("search_volume")), key=lambda r: -r["search_volume"])[:top]


def estimate(n_kd, n_serp):
    return round((KD_TASK_COST + KD_PER_KEYWORD * n_kd if n_kd else 0) + SERP_COST * n_serp, 4)


def run_shortlist(run_dir, kd_path, serp_path, top=40, spend=False, max_cost=0.10, wait_minutes=60,
                  poll_s=30, loc=2840, lang="en", client=None, log=print):
    run_dir = Path(run_dir)
    shortlist = pick(_load_rows(run_dir), top)
    kd, serp = Store(kd_path), Store(serp_path)
    queued = {t["keyword"] for t in serp.pending}
    need_kd = [r["phrase"] for r in shortlist if not kd.fresh(r["phrase"], loc, lang)]
    need_serp = [r["phrase"] for r in shortlist if r["phrase"] not in queued and not serp.fresh(r["phrase"], loc, lang)]
    cost_est = estimate(len(need_kd), len(need_serp))
    log(f"Shortlist: {len(shortlist)} phrases (top {top} by volume). Need KD for {len(need_kd)}, top-10 results "
        f"for {len(need_serp)} ({len(serp.pending)} queued already). Estimated ${cost_est:.4f}.")
    if cost_est > max_cost:
        raise VolumeError(f"estimated ${cost_est:.4f} is over the --max-cost cap of ${max_cost:.2f}; nothing sent")
    if (need_kd or need_serp) and not spend:
        log("Dry run: nothing sent. Add --spend to run the checks.")
        return None
    if client is None:
        raise VolumeError("no DataForSEO client")

    cost = 0.0
    for start in range(0, len(need_kd), MAX_KEYWORDS):
        batch = need_kd[start:start + MAX_KEYWORDS]
        found, c = fetch_kd(client, batch, loc, lang)
        cost += c or 0
        for k in batch:
            kd.put(k, loc, lang, {"keyword_difficulty": found.get(k)})
        kd.save()
        log(f"KD: {len(batch)} keywords, ${c}.")

    for start in range(0, len(need_serp), SERP_PER_POST):
        tasks, c = post_serps(client, need_serp[start:start + SERP_PER_POST], loc, lang)
        cost += c or 0
        serp.pending += [{"keyword": k, "id": t, "loc": loc, "lang": lang} for k, t in tasks]
        serp.save()  # saved before waiting: a timeout never means paying twice
        log(f"SERP: queued {len(tasks)} tasks, ${c}.")

    deadline = time.monotonic() + wait_minutes * 60
    while serp.pending:
        for task in list(serp.pending):
            result = get_serp(client, task["id"])
            if result is None:
                continue
            serp.put(task["keyword"], task["loc"], task["lang"], summarize_serp(result))
            serp.pending.remove(task)
            serp.save()
        if serp.pending:
            if time.monotonic() > deadline:
                log(f"{len(serp.pending)} SERP task(s) still queued; re-run to collect them (free).")
                return None
            time.sleep(poll_s)

    rows = []
    for r in shortlist:
        k, s = kd.get(r["phrase"], loc, lang) or {}, serp.get(r["phrase"], loc, lang) or {}
        rows.append(dict(r, keyword_difficulty=k.get("keyword_difficulty"), big_sites=s.get("big_sites"),
                         weak_results=s.get("weak_results"), features=s.get("features", []),
                         top10=s.get("top10", [])))
    write_shortlist(run_dir, rows, cost)
    return {"cost_usd": round(cost, 4), "rows": len(rows)}


def render_md(rows, cost):
    def n(v, fmt="{:,}"):
        return fmt.format(v) if isinstance(v, (int, float)) else "–"
    lines = ["# Tool shortlist", "",
             f"Top {len(rows)} tool searches by real US Google volume, with keyword difficulty (KD, 0–100: "
             "chance of reaching the first page, lower is easier) and who holds the top 10 now. "
             "**Big** = established sites (Home Depot, Omnicalculator, Angi…); **weak** = forums, Reddit, "
             f"YouTube, Pinterest. Cost of this check: ${cost:.4f}.", "",
             "| # | Phrase | Type | Volume/mo | CPC | KD | Big | Weak | Top 3 now | Features |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        top3 = ", ".join(t["domain"] for t in r["top10"][:3])
        feats = ", ".join(f for f in r["features"] if f in ("featured_snippet", "people_also_ask", "answer_box",
                                                              "video", "local_pack", "shopping"))
        lines.append(f"| {i} | {r['phrase']} | {r.get('tool_type') or ''} | {n(r['search_volume'])} | "
                     f"{n(r.get('cpc'), '${:.2f}')} | {n(r['keyword_difficulty'])} | {n(r['big_sites'])} | "
                     f"{n(r['weak_results'])} | {top3} | {feats} |")
    lines += ["", "Reading it: a good build has real volume, a CPC that shows the topic pays, low-to-mid KD, "
                  "and a top 10 with few big sites or several weak results. `answer_box` means Google may "
                  "answer the question itself."]
    return "\n".join(lines) + "\n"


def write_shortlist(run_dir, rows, cost):
    run_dir = Path(run_dir)
    with open(run_dir / "shortlist.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    fields = ["phrase", "tool_type", "search_volume", "cpc", "keyword_difficulty", "big_sites", "weak_results",
              "top3", "features", "seeds"]
    with open(run_dir / "shortlist.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(dict(r, top3=" | ".join(t["domain"] for t in r["top10"][:3]),
                            features=" ".join(r["features"]), seeds=" | ".join(r.get("seeds", []))))
    (run_dir / "shortlist_report.md").write_text(render_md(rows, cost), encoding="utf-8")
