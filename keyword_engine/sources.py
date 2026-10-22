"""Free data sources: five autocomplete engines, Stack Exchange and Wikipedia page views.

Every request goes through HttpClient (timeout, one retry, polite per-host pacing)
and through the Cache, and every outcome is counted in SourceLog for the run's
health summary. A failing source never stops a run.
"""
import html
import re
import threading
import time
from collections import defaultdict
from datetime import date
from urllib.parse import quote, urlparse

import requests

from .analyze import stem

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
API_UA = "keyword-engine/0.2 (+https://github.com/bergeon9000/keyword-engine)"

SUGGEST_ENGINES = ("google", "youtube", "bing", "ddg", "amazon")


class FetchError(Exception):
    pass


class HttpClient:
    def __init__(self, delay=0.25, timeout=10, retries=1, session=None):
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self._host_locks = defaultdict(threading.Lock)
        self._last_hit = {}

    def _pace(self, host):
        with self._host_locks[host]:
            wait = self.delay - (time.monotonic() - self._last_hit.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.monotonic()

    def get_json(self, url, params=None, headers=None):
        host = urlparse(url).netloc
        last_error = None
        for attempt in range(self.retries + 1):
            self._pace(host)
            try:
                r = self.session.get(url, params=params, headers=headers or {}, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise FetchError(f"HTTP {r.status_code}")
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError, FetchError) as e:
                last_error = e
                if attempt < self.retries:
                    time.sleep(2 * (attempt + 1))
        raise FetchError(f"{type(last_error).__name__}: {last_error}")


class SourceLog:
    """Thread-safe per-source counters: attempted, live, cache, stale, failed."""

    def __init__(self):
        self._lock = threading.Lock()
        self.stats = {}

    def record(self, source, how, error=None):
        with self._lock:
            s = self.stats.setdefault(source, {"attempted": 0, "live": 0, "cache": 0,
                                               "stale": 0, "failed": 0, "errors": []})
            s["attempted"] += 1
            s[how] += 1
            if error and len(s["errors"]) < 5:
                s["errors"].append(error)

    def ok_sources(self):
        return [name for name, s in self.stats.items() if s["attempted"] > s["failed"]]


# --- response parsers (pure, tested against saved real responses) ---

def parse_google(data):
    """client=chrome: [q, [phrases], [descs], [], {"google:suggestrelevance": [...], ...}]"""
    phrases = data[1]
    extra = data[4] if len(data) > 4 and isinstance(data[4], dict) else {}
    relevance = extra.get("google:suggestrelevance", [None] * len(phrases))
    types = extra.get("google:suggesttype", ["QUERY"] * len(phrases))
    out = []
    for i, (p, rel, kind) in enumerate(zip(phrases, relevance, types)):
        if kind == "NAVIGATION":
            continue
        out.append({"phrase": p, "rank": i + 1, "relevance": rel})
    return out


def parse_osjson(data):
    """Google firefox/youtube clients and Bing: [q, [phrases], ...]"""
    return [{"phrase": p, "rank": i + 1, "relevance": None} for i, p in enumerate(data[1])]


def parse_ddg(data):
    return [{"phrase": x["phrase"], "rank": i + 1, "relevance": None} for i, x in enumerate(data)]


def parse_amazon(data):
    return [{"phrase": x["value"], "rank": i + 1, "relevance": None}
            for i, x in enumerate(data.get("suggestions", []))]


def parse_stackexchange(data, site):
    out = []
    for item in data.get("items", []):
        out.append({
            "title": html.unescape(item["title"]),
            "site": site,
            "views": item.get("view_count", 0),
            "answers": item.get("answer_count", 0),
            "answered": item.get("is_answered", False),
            "score": item.get("score", 0),
            "link": item.get("link"),
            "created": item.get("creation_date"),
        })
    return out


def wiki_title_matches(title, query):
    """Guards against search drifting off topic: every query word (plurals folded)
    must appear in the title. Strict on purpose; no number beats a wrong number."""
    title_words = {stem(w) for w in re.findall(r"[a-z0-9]+", title.lower())}
    query_words = {stem(w) for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) >= 3}
    return bool(query_words) and query_words <= title_words


def last_full_months(n=12, today=None):
    """(start, end) in pageviews format covering the last n complete months."""
    today = today or date.today()
    y, m = today.year, today.month - 1  # last complete month
    if m == 0:
        y, m = y - 1, 12
    end = date(y, m, 1)
    sy, sm = y, m - (n - 1)
    while sm <= 0:
        sy, sm = sy - 1, sm + 12
    start = date(sy, sm, 1)
    return start.strftime("%Y%m%d00"), end.strftime("%Y%m%d00")


class Sources:
    def __init__(self, http, cache, region, log):
        self.http = http
        self.cache = cache
        self.region = region
        self.log = log

    def _cached(self, source, key, fetch_fn):
        try:
            data, how = self.cache.get_or_fetch(source, key, fetch_fn)
        except Exception as e:
            self.log.record(source, "failed", f"{key}: {e}")
            return None
        self.log.record(source, how)
        return data

    # --- autocomplete ---

    def _fetch_suggest(self, engine, query):
        r = self.region
        ua = {"User-Agent": BROWSER_UA}
        if engine == "google":
            data = self.http.get_json("https://suggestqueries.google.com/complete/search",
                                      {"client": "chrome", "hl": r["hl"], "gl": r["gl"],
                                       "ie": "utf-8", "oe": "utf-8", "q": query}, ua)
            return parse_google(data)
        if engine == "youtube":
            data = self.http.get_json("https://suggestqueries.google.com/complete/search",
                                      {"client": "firefox", "ds": "yt", "hl": r["hl"], "gl": r["gl"],
                                       "ie": "utf-8", "oe": "utf-8", "q": query}, ua)
            return parse_osjson(data)
        if engine == "bing":
            data = self.http.get_json("https://api.bing.com/osjson.aspx",
                                      {"query": query, "market": r["market"]}, ua)
            return parse_osjson(data)
        if engine == "ddg":
            data = self.http.get_json("https://duckduckgo.com/ac/", {"q": query, "kl": r["ddg_kl"]}, ua)
            return parse_ddg(data)
        if engine == "amazon":
            data = self.http.get_json("https://completion.amazon.com/api/2017/suggestions",
                                      {"mid": "ATVPDKIKX0DER", "alias": "aps", "prefix": query}, ua)
            return parse_amazon(data)
        raise ValueError(f"unknown engine {engine}")

    def suggest(self, engine, query):
        """List of {"phrase", "rank", "relevance"}; [] when the engine failed."""
        key = f"{query}|{self.region['hl']}-{self.region['gl']}"
        return self._cached(engine, key, lambda: self._fetch_suggest(engine, query)) or []

    # --- Stack Exchange ---

    def stackexchange(self, query, site, pagesize=30):
        def fetch():
            data = self.http.get_json("https://api.stackexchange.com/2.3/search/advanced",
                                      {"q": query, "site": site, "pagesize": pagesize,
                                       "order": "desc", "sort": "relevance"},
                                      {"User-Agent": API_UA})
            if data is None or "items" not in data:
                raise FetchError(f"no items: {str(data)[:120]}")
            if data.get("backoff"):
                time.sleep(int(data["backoff"]))
            return parse_stackexchange(data, site)
        return self._cached("stackexchange", f"{site}|{query}", fetch) or []

    # --- Wikipedia ---

    def wiki_monthly_views(self, query, months=12):
        """{"title", "avg_monthly_views", "months"} for the best-matching article, or None."""
        ua = {"User-Agent": API_UA}

        def fetch():
            # 1) the seed as an exact title, following redirects ("Reef tank" -> "Reef aquarium")
            exact = self.http.get_json("https://en.wikipedia.org/w/api.php",
                                       {"action": "query", "titles": query[:1].upper() + query[1:],
                                        "redirects": 1, "format": "json"}, ua)
            page = next(iter((exact or {}).get("query", {}).get("pages", {}).values()), {})
            title = page.get("title") if page and "missing" not in page and "invalid" not in page else None
            # 2) otherwise search, accepting only a title that contains every seed word
            if not title:
                found = self.http.get_json("https://en.wikipedia.org/w/api.php",
                                           {"action": "query", "list": "search", "srsearch": query,
                                            "srlimit": 3, "format": "json"}, ua)
                hits = (found or {}).get("query", {}).get("search", [])
                title = next((h["title"] for h in hits if wiki_title_matches(h["title"], query)), None)
            if not title:
                return None
            start, end = last_full_months(months)
            url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
                   f"en.wikipedia/all-access/user/{quote(title.replace(' ', '_'), safe='')}/monthly/{start}/{end}")
            views = self.http.get_json(url, None, ua)
            items = (views or {}).get("items", [])
            if not items:
                return {"title": title, "avg_monthly_views": 0, "months": 0}
            total = sum(i["views"] for i in items)
            return {"title": title, "avg_monthly_views": round(total / len(items)), "months": len(items)}

        return self._cached("wikipedia", f"exact-or-strict|{query}|{months}", fetch)
