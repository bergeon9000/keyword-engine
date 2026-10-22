"""Optional paid step: real Google Ads search volume from DataForSEO, plus a check of
how well the engine's free-evidence ranking matches it.

Cost guards, in order:
- Nothing paid happens without --spend; a plain run only prints the plan.
- A hard --max-cost cap (default $0.10) is checked before anything is sent.
- Every result goes into a ledger (data/volume_ledger.json); a keyword already
  in it is never sent again.
- A posted task is recorded as pending *before* waiting, so if the wait times
  out the next run collects it instead of paying again.
Collecting results and reading the balance are free.
"""
import json
import math
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PROD = "https://api.dataforseo.com/v3"
SANDBOX = "https://sandbox.dataforseo.com/v3"
TASK_COST_STANDARD = 0.06  # USD per task of up to 1,000 keywords (pricing page, 2026-10)
MAX_KEYWORDS = 1000
NOT_READY = {40601, 40602}  # task handed / in queue
SV_PATH = "/keywords_data/google_ads/search_volume"


class VolumeError(Exception):
    pass


def get_credentials():
    """(login, password) from the environment, else from the Windows user environment.

    Values are never printed or logged.
    """
    login, password = os.environ.get("DATAFORSEO_LOGIN"), os.environ.get("DATAFORSEO_PASSWORD")
    if not (login and password):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                login = login or winreg.QueryValueEx(key, "DATAFORSEO_LOGIN")[0]
                password = password or winreg.QueryValueEx(key, "DATAFORSEO_PASSWORD")[0]
        except (ImportError, OSError):
            pass
    if not (login and password):
        raise VolumeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are not set")
    return login, password


def google_ads_ok(keyword):
    """Google Ads limits: at most 80 characters and 10 words, plain characters only."""
    return (len(keyword) <= 80 and len(keyword.split()) <= 10
            and all(c.isalnum() and c.isascii() or c in " '-." for c in keyword))


class DataForSEO:
    def __init__(self, login, password, base=PROD, session=None, timeout=60):
        self.base = base.rstrip("/")
        self.auth = (login, password)
        self.session = session or requests.Session()
        self.timeout = timeout

    def _call(self, method, path, body=None):
        r = self.session.request(method, self.base + path, auth=self.auth, json=body, timeout=self.timeout)
        if r.status_code == 401:
            raise VolumeError("DataForSEO rejected the login (use the API login/password from the API Access page)")
        r.raise_for_status()
        data = r.json()
        if data.get("status_code") != 20000:
            raise VolumeError(f"DataForSEO error {data.get('status_code')}: {data.get('status_message')}")
        return data

    def balance(self):
        data = self._call("GET", "/appendix/user_data")
        return data["tasks"][0]["result"][0]["money"]["balance"]

    def post(self, keywords, location_code=2840, language_code="en", tag=None):
        """Queues one search-volume task. Returns (task_id, cost_usd)."""
        if not 0 < len(keywords) <= MAX_KEYWORDS:
            raise ValueError(f"1 to {MAX_KEYWORDS} keywords per task, got {len(keywords)}")
        body = [{"keywords": keywords, "location_code": location_code, "language_code": language_code}]
        if tag:
            body[0]["tag"] = tag
        data = self._call("POST", SV_PATH + "/task_post", body)
        task = data["tasks"][0]
        if task["status_code"] != 20100:
            raise VolumeError(f"task not created: {task['status_code']} {task.get('status_message')}")
        return task["id"], data.get("cost", 0)

    def get(self, task_id):
        """Result rows for a finished task, or None while it is still queued. Free."""
        data = self._call("GET", f"{SV_PATH}/task_get/{task_id}")
        task = data["tasks"][0]
        if task["status_code"] in NOT_READY:
            return None
        if task["status_code"] != 20000:
            raise VolumeError(f"task {task_id} failed: {task['status_code']} {task.get('status_message')}")
        return task.get("result") or []


class Ledger:
    """Every keyword ever checked, so nothing is paid for twice. Also holds unpaid-for pending tasks."""

    def __init__(self, path, max_age_days=180):
        self.path = Path(path)
        self.max_age = timedelta(days=max_age_days)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.keywords = data.get("keywords", {})
        self.pending = data.get("pending", [])

    @staticmethod
    def key(keyword, location_code, language_code):
        return f"{keyword}|{location_code}|{language_code}"

    def fresh(self, keyword, location_code, language_code):
        entry = self.keywords.get(self.key(keyword, location_code, language_code))
        if not entry:
            return False
        return datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"]) < self.max_age

    def get(self, keyword, location_code, language_code):
        return self.keywords.get(self.key(keyword, location_code, language_code))

    def add_results(self, rows, task_id, location_code, language_code, sent):
        now = datetime.now(timezone.utc).isoformat()
        seen = set()
        for row in rows:
            kw = (row.get("keyword") or "").lower()
            seen.add(kw)
            self.keywords[self.key(kw, location_code, language_code)] = {
                "search_volume": row.get("search_volume"),
                "cpc": row.get("cpc"),
                "competition": row.get("competition"),
                "competition_index": row.get("competition_index"),
                "monthly_searches": row.get("monthly_searches"),
                "spell": row.get("spell"),
                "fetched_at": now,
                "task_id": task_id,
            }
        for kw in sent:  # keywords Google returned nothing for: record as checked, no data
            if kw not in seen:
                self.keywords[self.key(kw, location_code, language_code)] = {
                    "search_volume": None, "fetched_at": now, "task_id": task_id, "no_data": True}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"keywords": self.keywords, "pending": self.pending}, indent=1), encoding="utf-8")
        tmp.replace(self.path)


# --- evaluation: does the free ranking agree with real volume? ---

def _ranks(values):
    """Average ranks (1 = smallest), ties share the mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return round(num / den, 3) if den else None


def evaluate(rows):
    """rows: phrases in engine order, each with a "search_volume" (int or None)."""
    with_data = [(i, r) for i, r in enumerate(rows) if r["search_volume"] is not None]
    engine_pos = [-i for i, _ in with_data]  # higher = ranked better by the engine
    volumes = [r["search_volume"] for _, r in with_data]
    by_engines = {}
    for _, r in with_data:
        by_engines.setdefault(r["engine_count"], []).append(r["search_volume"])
    real_top = {r["phrase"] for r in sorted((r for _, r in with_data), key=lambda r: -r["search_volume"])[:100]}
    engine_top50 = [r for r in rows[:50]]
    return {
        "phrases": len(rows),
        "with_volume": len(with_data),
        "nonzero_volume": sum(1 for v in volumes if v),
        "spearman_engine_rank_vs_volume": spearman(engine_pos, volumes),
        "spearman_engine_count_vs_volume": spearman([r["engine_count"] for _, r in with_data], volumes),
        "median_volume_by_engine_count": {k: statistics.median(v) for k, v in sorted(by_engines.items())},
        "count_by_engine_count": {k: len(v) for k, v in sorted(by_engines.items())},
        "engine_top50_in_real_top100": sum(1 for r in engine_top50 if r["phrase"] in real_top),
        "engine_top50_with_zero_or_no_volume": sum(1 for r in engine_top50 if not r["search_volume"]),
    }


def render_md(run, ev, rows, cost, balance_before, balance_after):
    def vol(v):
        return f"{v:,}" if isinstance(v, int) else "no data"
    lines = [f"# Real search volume check: {run['seed']}", "",
             f"Google Ads monthly volume (US, English) via DataForSEO for run `{run['run_id']}`.", "",
             f"- Phrases: {ev['phrases']}; with volume data: {ev['with_volume']}; above zero: {ev['nonzero_volume']}",
             f"- **Rank agreement** (Spearman, engine order vs real volume): "
             f"**{ev['spearman_engine_rank_vs_volume']}** (1 = perfect, 0 = none)",
             f"- Engines-agree count vs volume (Spearman): {ev['spearman_engine_count_vs_volume']}",
             f"- Engine's top 50 that are in the real top 100: {ev['engine_top50_in_real_top100']} of 50",
             f"- Engine's top 50 with zero or no volume: {ev['engine_top50_with_zero_or_no_volume']}",
             f"- Cost this run: ${cost:.4f}" + (f" · balance ${balance_before:.4f} → ${balance_after:.4f}"
                                                if balance_before is not None and balance_after is not None else ""),
             "", "## Median volume by how many engines suggested the phrase", "",
             "| Engines | Phrases | Median monthly searches |", "|---|---|---|"]
    for k, med in ev["median_volume_by_engine_count"].items():
        lines.append(f"| {k} | {ev['count_by_engine_count'][k]} | {med:,.0f} |")
    lines += ["", "## Engine's top 25 with real volume", "", "| # | Phrase | Engines | Volume | CPC |", "|---|---|---|---|---|"]
    for i, r in enumerate(rows[:25], 1):
        lines.append(f"| {i} | {r['phrase']} | {r['engine_count']} | {vol(r['search_volume'])} | {r.get('cpc') or ''} |")
    real = sorted((r for r in rows if r["search_volume"]), key=lambda r: -r["search_volume"])[:25]
    pos = {r["phrase"]: i for i, r in enumerate(rows, 1)}
    lines += ["", "## Real top 25 and where the engine ranked them", "",
              "| Phrase | Volume | Engine rank | Engines |", "|---|---|---|---|"]
    for r in real:
        lines.append(f"| {r['phrase']} | {vol(r['search_volume'])} | {pos[r['phrase']]} | {r['engine_count']} |")
    lines += ["", "Note: Google Ads merges close variants (plurals, word order), so pairs like "
                  "`reef tank light` / `reef tank lights` can show the same number."]
    return "\n".join(lines) + "\n"


def run_volume(run_dir, ledger_path, spend=False, max_cost=0.10, wait_minutes=180, poll_s=60,
               location_code=2840, language_code="en", client=None, log=print):
    run_dir = Path(run_dir)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    phrases = [json.loads(l) for l in (run_dir / "phrases.jsonl").read_text(encoding="utf-8").splitlines() if l]
    ledger = Ledger(ledger_path)

    usable = list(dict.fromkeys(p["phrase"] for p in phrases if google_ads_ok(p["phrase"])))
    queued = {k for t in ledger.pending for k in t["keywords"]}  # paid for, waiting to be collected
    to_send = [k for k in usable if k not in queued and not ledger.fresh(k, location_code, language_code)]
    tasks = math.ceil(len(to_send) / MAX_KEYWORDS)
    estimate = round(tasks * TASK_COST_STANDARD, 4)
    log(f"{len(phrases)} phrases; {len(usable)} fit Google Ads limits; {len(usable) - len(to_send)} already "
        f"checked or queued; {len(to_send)} to check in {tasks} task(s), about ${estimate:.2f}. "
        f"{len(ledger.pending)} pending task(s) to collect.")

    if estimate > max_cost:
        raise VolumeError(f"estimated ${estimate:.2f} is over the --max-cost cap of ${max_cost:.2f}; nothing sent")
    if to_send and not spend:
        log("Dry run: nothing sent. Add --spend to queue the task(s).")
        return None
    if client is None:
        raise VolumeError("no DataForSEO client")

    balance_before = _safe(client.balance)
    cost = 0.0
    for start in range(0, len(to_send), MAX_KEYWORDS):
        batch = to_send[start:start + MAX_KEYWORDS]
        task_id, task_cost = client.post(batch, location_code, language_code, tag=run["run_id"][:250])
        cost += task_cost or 0
        ledger.pending.append({"id": task_id, "keywords": batch, "location_code": location_code,
                               "language_code": language_code, "posted_at": datetime.now(timezone.utc).isoformat()})
        ledger.save()  # recorded before waiting, so a timeout never means paying twice
        log(f"Queued task {task_id} ({len(batch)} keywords, ${task_cost}).")

    deadline = time.monotonic() + wait_minutes * 60
    while ledger.pending:
        for task in list(ledger.pending):
            rows = client.get(task["id"])
            if rows is None:
                continue
            ledger.add_results(rows, task["id"], task["location_code"], task["language_code"], task["keywords"])
            ledger.pending.remove(task)
            ledger.save()
            log(f"Collected task {task['id']}: {len(rows)} rows.")
        if ledger.pending:
            if time.monotonic() > deadline:
                log(f"{len(ledger.pending)} task(s) still queued. Re-run the same command later to collect "
                    "them (collecting is free; nothing is re-sent).")
                return None
            time.sleep(poll_s)

    balance_after = _safe(client.balance)
    out_rows = []
    for p in phrases:
        entry = best_entry(ledger, [p["phrase"]] + p.get("variants", []), location_code, language_code)
        out_rows.append({"phrase": p["phrase"], "variants": p.get("variants", [p["phrase"]]),
                         "engine_count": p["engine_count"], "times_seen": p["times_seen"],
                         "cluster_id": p["cluster_id"], "intent": p["intent"],
                         "search_volume": entry.get("search_volume"), "cpc": entry.get("cpc"),
                         "competition": entry.get("competition"), "competition_index": entry.get("competition_index")})
    ev = evaluate(out_rows)
    with open(run_dir / "volume.jsonl", "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {"evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "source": "dataforseo google_ads search_volume (standard queue)",
               "location_code": location_code, "language_code": language_code,
               "cost_usd": round(cost, 4), "balance_before": balance_before, "balance_after": balance_after,
               "evaluation": ev}
    (run_dir / "volume.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (run_dir / "volume_report.md").write_text(render_md(run, ev, out_rows, cost, balance_before, balance_after),
                                              encoding="utf-8")
    return summary


def best_entry(ledger, keywords, location_code, language_code):
    """The ledger entry with the highest volume among a phrase and its variants ({} if none).

    Google reports one merged number for close variants, so taking the highest
    known one is the fair value for the merged phrase.
    """
    entries = [e for e in (ledger.get(k, location_code, language_code) for k in keywords) if e]
    with_volume = [e for e in entries if e.get("search_volume") is not None]
    if with_volume:
        return max(with_volume, key=lambda e: e["search_volume"])
    return entries[0] if entries else {}


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None
