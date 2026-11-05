"""People Also Ask: the questions Google shows for a search, from the same DataForSEO top-10 tasks the shortlist buys.

Two ways in, both writing into the SERP ledger (the top-10 summary plus "paa" questions and "related" searches):

- recover (free). DataForSEO keeps a task's results for 30 days and task_get is free; serp/id_list (also free)
  names the tasks posted in the last month with their keywords. Tasks bought before the ledger kept questions
  get them back this way, until they expire.
- check (paid). A top 10 plus questions for a phrase list, with the People Also Ask box clicked open
  (people_also_ask_click_depth) for more questions, and KD for phrases that lack it. Same guards as the shortlist:
  a dry run unless --spend, a --max-cost cap checked before sending, ledgers so nothing is bought twice, and queued
  tasks saved before waiting.

Then a report lists each phrase's questions under the site page it belongs to (the catalog's article targets first,
then the calculator its words match), so a writer can see which questions a page hasn't answered.
"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .shortlist import (KD_PER_KEYWORD, KD_TASK_COST, PAA_CLICK_COST, SERP_COST, SERP_PER_POST, Store, fetch_kd,
                        get_serp, post_serps, summarize_serp)
from .volume import MAX_KEYWORDS, VolumeError

ID_LIST = "/serp/id_list"
ORGANIC = "serp/google/organic"


def _when(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S +00:00")


def list_tasks(client, since, until):
    """Every SERP task posted between two datetimes, with its keyword. Free; keywords only for the last month."""
    out, offset = [], 0
    while True:
        data = client._call("POST", ID_LIST, [{"datetime_from": _when(since), "datetime_to": _when(until),
                                                "limit": 1000, "offset": offset, "sort": "asc",
                                                "include_metadata": True}])
        task = data["tasks"][0]
        if task["status_code"] == 40404:  # no tasks in the window (the sandbox always says this)
            return out
        if task["status_code"] != 20000:
            raise VolumeError(f"id_list failed: {task['status_code']} {task.get('status_message')}")
        batch = task.get("result") or []
        out += batch
        if len(batch) < 1000:
            return out
        offset += 1000


def _is_organic(t):
    return ORGANIC in (t.get("url") or "") or ((t.get("metadata") or {}).get("se_type") == "organic"
                                                and (t.get("metadata") or {}).get("se") == "google")


def recover(client, serp_path, days=30, log=print, now=None):
    """Adds questions to ledger entries from tasks still on DataForSEO. Free. Returns how many entries gained them.

    Entries keep their original fetched_at: the top 10 and the questions are from the day the task ran."""
    serp = Store(serp_path)
    now = now or datetime.now(timezone.utc)
    # datetime_to has to be before the present; the window can't reach back more than a month for keywords
    tasks = [t for t in list_tasks(client, now - timedelta(days=days), now - timedelta(minutes=1)) if _is_organic(t)]
    log(f"Recover: {len(tasks)} Google organic task(s) posted in the last {days} days.")
    added = 0
    for t in tasks:
        meta = t.get("metadata") or {}
        keyword, loc, lang = (meta.get("keyword") or "").lower(), meta.get("location_code"), meta.get("language_code")
        if not keyword or "paa" in (serp.get(keyword, loc, lang) or {}):
            continue
        result = get_serp(client, t["id"])
        if result is None:
            continue
        entry = serp.get(keyword, loc, lang)
        if entry:
            entry.update({k: v for k, v in summarize_serp(result).items() if k in ("paa", "related")})
        else:
            serp.items[Store.key(keyword, loc, lang)] = dict(
                summarize_serp(result), fetched_at=_iso(t.get("datetime_done") or t.get("datetime_posted")))
        added += 1
    serp.save()
    log(f"Recover: questions added for {added} phrase(s).")
    return added


def _iso(dt):
    """DataForSEO's '2026-10-22 23:51:02 +00:00' as ISO, so Store.fresh() can read it."""
    try:
        return datetime.strptime(dt, "%Y-%m-%d %H:%M:%S %z").isoformat()
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat()


def estimate(n_kd, n_serp, paa_depth):
    return round((KD_TASK_COST + KD_PER_KEYWORD * n_kd if n_kd else 0)
                 + (SERP_COST + PAA_CLICK_COST * paa_depth) * n_serp, 4)


def check(phrases, kd_path, serp_path, spend=False, max_cost=0.10, paa_depth=2, kd=True, wait_minutes=60, poll_s=30,
          loc=2840, lang="en", client=None, log=print):
    """Top 10 + questions (and KD) for a phrase list. Returns the cost, or None for a dry run or a timeout."""
    phrases = list(dict.fromkeys(p.strip().lower() for p in phrases if p.strip()))
    kds, serp = Store(kd_path), Store(serp_path)
    queued = {t["keyword"] for t in serp.pending}
    need_kd = [p for p in phrases if not kds.fresh(p, loc, lang)] if kd else []
    need_serp = [p for p in phrases if p not in queued
                 and not (serp.fresh(p, loc, lang) and "paa" in (serp.get(p, loc, lang) or {}))]
    cost_est = estimate(len(need_kd), len(need_serp), paa_depth)
    log(f"Questions: {len(phrases)} phrases. Need KD for {len(need_kd)}, top 10 + questions for {len(need_serp)} "
        f"({len(serp.pending)} queued already), {paa_depth} People Also Ask click(s) each. Estimated ${cost_est:.4f}.")
    if cost_est > max_cost:
        raise VolumeError(f"estimated ${cost_est:.4f} is over the --max-cost cap of ${max_cost:.2f}; nothing sent")
    if (need_kd or need_serp) and not spend:
        log("Dry run: nothing sent. Add --spend to run the checks.")
        return None
    if (need_kd or need_serp or serp.pending) and client is None:
        raise VolumeError("no DataForSEO client")

    cost = 0.0
    for start in range(0, len(need_kd), MAX_KEYWORDS):
        batch = need_kd[start:start + MAX_KEYWORDS]
        found, c = fetch_kd(client, batch, loc, lang)
        cost += c or 0
        for k in batch:
            kds.put(k, loc, lang, {"keyword_difficulty": found.get(k)})
        kds.save()
        log(f"KD: {len(batch)} keywords, ${c}.")

    for start in range(0, len(need_serp), SERP_PER_POST):
        tasks, c = post_serps(client, need_serp[start:start + SERP_PER_POST], loc, lang, paa_depth)
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
    return round(cost, 4)


# --- the report ---

def page_of(phrase, catalog):
    """The site page a phrase belongs to: an article that targets it, else the calculator its words match."""
    from .articles import match  # here, not at the top: articles imports shortlist, like this module

    pages = (catalog or {}).get("pages", [])
    for p in pages:
        if p["type"] == "article" and phrase in [t.lower() for t in p.get("targets", [])]:
            return p
    page, _ = match(phrase, catalog)
    return page


def report(phrases, serp_path, kd_path, catalog, loc=2840, lang="en"):
    """Rows for every phrase with a ledger entry: page, KD, top-10 features, questions, related searches."""
    serp, kds = Store(serp_path), Store(kd_path)
    rows = []
    for p in phrases:
        s = serp.get(p, loc, lang)
        if not s:
            continue
        page = page_of(p, catalog)
        rows.append({"phrase": p, "page": page["path"] if page else None, "page_name": page["name"] if page else None,
                     "page_type": page["type"] if page else None,
                     "kd": (kds.get(p, loc, lang) or {}).get("keyword_difficulty"),
                     "fetched": (s.get("fetched_at") or "")[:10], "features": s.get("features", []),
                     "paa": s.get("paa"), "related": s.get("related", [])})
    return rows


def render_report(rows, cost=None):
    lines = ["# People Also Ask, by page", "",
             "Google's People Also Ask questions for each checked search (US), grouped under the site page that "
             "search belongs to. A question a page doesn't answer yet is a candidate for its questions section, or "
             "for a section of its own when several searches ask it."
             + (f" Cost of this check: ${cost:.4f}." if cost is not None else ""), ""]
    groups = {}
    for r in rows:
        groups.setdefault((r["page_type"] or "zz", r["page"] or "", r["page_name"] or "No page yet"), []).append(r)
    for (_, path, name), rs in sorted(groups.items()):
        lines += [f"## {name}" + (f" ({path})" if path else ""), ""]
        for r in rs:
            kd = f", KD {r['kd']}" if isinstance(r["kd"], (int, float)) else ""
            lines.append(f"### {r['phrase']} ({r['fetched']}{kd})")
            if r["paa"] is None:
                lines.append("- Questions not stored (bought before the ledger kept them; past DataForSEO's 30 days).")
            elif not r["paa"]:
                lines.append("- No People Also Ask box.")
            lines += [f"- {q}" for q in r["paa"] or []]
            if r["related"]:
                lines.append(f"- *Related searches:* {', '.join(r['related'][:8])}")
            lines.append("")
    return "\n".join(lines) + "\n"


def write_report(out_dir, rows, cost=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "questions_paa.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "questions_paa.md").write_text(render_report(rows, cost), encoding="utf-8")
    return out_dir / "questions_paa.md"
