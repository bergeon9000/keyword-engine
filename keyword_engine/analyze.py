"""Turns raw suggestions into ranked, clustered phrases.

Nothing here invents a score. Ranking is a plain tuple of counted evidence:
how many engines suggested a phrase, how many queries surfaced it, Google's
own relevance number, and its best list position.
"""
import html
import re
import string
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# --- query plan ---

PRE_WORDS = ["best", "cheap", "diy", "top", "how to", "how much", "what is", "what", "why",
             "when", "where", "which", "can", "does", "is"]
POST_WORDS = ["for", "with", "without", "near", "vs", "under", "to", "in", "like", "or"]


def build_queries(seed):
    """[(kind, query)] for one seed: the seed, seed + a-z, pre-words, post-words."""
    seed = normalize(seed)
    plan = [("seed", seed)]
    plan += [("suffix", f"{seed} {c}") for c in string.ascii_lowercase]
    plan += [("pre", f"{w} {seed}") for w in PRE_WORDS]
    plan += [("post", f"{seed} {w}") for w in POST_WORDS]
    return plan


# --- text helpers ---

def normalize(text):
    t = html.unescape(text).lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^a-z0-9'&+.\- ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-'")
    return t


def contains_word(word, text):
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


def is_probably_english(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    return sum(c.isascii() for c in letters) / len(letters) >= 0.8


def stem(word):
    """Very light plural/-ing folding so light, lights and lighting group together."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 6 and word.endswith("ing"):  # lighting -> light, but dosing stays
        return word[:-3]
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


class Matcher:
    """Finds the seed (or a synonym) inside a phrase, tolerating plurals."""

    def __init__(self, seed, synonyms=()):
        self.seed = normalize(seed)
        terms = [self.seed] + [normalize(s) for s in synonyms]
        self.patterns = [self._pattern(t) for t in terms if t]

    @staticmethod
    def _pattern(term):
        parts = [re.escape(tok) + r"(?:s|es)?" for tok in term.split()]
        return re.compile(r"\b" + r"\s+".join(parts) + r"\b")

    def span(self, phrase):
        for p in self.patterns:
            m = p.search(phrase)
            if m:
                return m.span()
        return None


# --- per-phrase features ---

KEEP_WORDS = {
    "{keyword}", "{num}",
    "in", "near", "for", "with", "without", "within", "to", "of", "the", "a", "an", "my", "your",
    "is", "are", "does", "do", "me", "vs", "or", "and", "on", "at", "by", "from", "under", "over",
    "how", "what", "why", "when", "where", "which", "who", "can", "should", "much", "many",
    "best", "cheap", "cost", "price", "prices", "buy", "sale", "review", "reviews", "top", "free",
    "kit", "calculator", "guide", "ideas", "diy",
}
STOP_WORDS = {
    "a", "an", "the", "of", "for", "with", "without", "within", "to", "in", "on", "at", "by", "from",
    "and", "or", "vs", "is", "are", "do", "does", "can", "should", "my", "your", "me", "i", "it",
    "how", "what", "why", "when", "where", "which", "who", "much", "many", "up", "near", "under",
    "over", "like", "best", "cheap", "top", "good", "new", "diy", "you", "need", "no", "not",
}
LOCAL_WORDS = ["near me", "nearby", "in my area", "open now", "near"]
COMMERCIAL_WORDS = ["best", "cheap", "price", "prices", "cost", "costs", "buy", "for sale", "sale",
                    "review", "reviews", "vs", "deal", "deals", "kit", "brand", "brands", "top",
                    "affordable", "discount", "coupon", "used", "supplies", "equipment", "shop",
                    "store"]
QUESTION_STARTS = ("how", "what", "why", "when", "where", "which", "who", "can", "should",
                   "does", "do", "is", "are", "will")


def shape(phrase, matcher):
    """'best gravel for driveways' -> 'best {keyword} for {x}'"""
    span = matcher.span(phrase)
    if span:
        phrase = phrase[:span[0]] + " {keyword} " + phrase[span[1]:]
    out = []
    for tok in phrase.split():
        if re.fullmatch(r"\d+(\.\d+)?", tok):
            tok = "{num}"
        elif tok not in KEEP_WORDS:
            tok = "{x}"
        if tok == "{x}" and out and out[-1] == "{x}":
            continue
        out.append(tok)
    return " ".join(out)


def modifier(phrase, matcher):
    """The words around the seed: 'reef tank lights' -> 'lights'."""
    span = matcher.span(phrase)
    rest = phrase[:span[0]] + " " + phrase[span[1]:] if span else phrase
    return " ".join(rest.split())


def cluster_key(phrase, matcher):
    content = [t for t in modifier(phrase, matcher).split()
               if t not in STOP_WORDS and not t.isdigit() and len(t) > 1]
    return stem(content[0]) if content else "(general)"


def intent(phrase):
    if any(contains_word(w, phrase) for w in LOCAL_WORDS):
        return "local"
    if any(contains_word(w, phrase) for w in COMMERCIAL_WORDS):
        return "commercial"
    if phrase.startswith(QUESTION_STARTS):
        return "question"
    return "informational"


def drop_reason(phrase, matcher, skip_words, max_words=12):
    if not is_probably_english(phrase):
        return "not_english"
    if len(phrase.split()) > max_words:
        return "too_long"
    if any(contains_word(w, phrase) for w in skip_words):
        return "skip_word"
    if matcher.span(phrase) is None:
        return "off_topic"
    return None


# --- collecting evidence ---

@dataclass
class Evidence:
    phrase: str
    engines: set = field(default_factory=set)
    queries: set = field(default_factory=set)
    kinds: set = field(default_factory=set)
    google_relevance: int = None
    best_rank: int = 99

    def add(self, engine, kind, query, hit):
        self.engines.add(engine)
        self.queries.add(query)
        self.kinds.add(kind)
        self.best_rank = min(self.best_rank, hit["rank"])
        if engine == "google" and hit.get("relevance") is not None:
            self.google_relevance = max(self.google_relevance or 0, hit["relevance"])

    def absorb(self, other):
        self.engines |= other.engines
        self.queries |= other.queries
        self.kinds |= other.kinds
        self.best_rank = min(self.best_rank, other.best_rank)
        if other.google_relevance is not None:
            self.google_relevance = max(self.google_relevance or 0, other.google_relevance)


# Words Google ignores when it merges close variants ("reef tank with sump" = "reef tank sump").
VARIANT_IGNORED = {"a", "an", "the", "for", "of", "to", "in", "on", "with", "and", "my", "your"}


def variant_key(phrase):
    """Same key for word-order and plural variants: 'nano reef tank' = 'reef tanks nano'."""
    return " ".join(sorted(stem(t) for t in phrase.split() if t not in VARIANT_IGNORED)) or phrase


def merge_variants(evidence):
    """Pools the evidence of variant phrases. Returns {representative: (Evidence, [variants])}.

    The representative is the variant with the strongest evidence of its own, so
    the wording shown is the one people type most.
    """
    groups = defaultdict(list)
    for p, e in evidence.items():
        groups[variant_key(p)].append(e)
    merged = {}
    for members in groups.values():
        members.sort(key=lambda e: (len(e.engines), len(e.queries), e.google_relevance or 0, -e.best_rank,
                                    -len(e.phrase)), reverse=True)
        head = members[0]
        pooled = Evidence(head.phrase, set(head.engines), set(head.queries), set(head.kinds),
                          head.google_relevance, head.best_rank)
        for other in members[1:]:
            pooled.absorb(other)
        merged[head.phrase] = (pooled, [e.phrase for e in members])
    return merged


def collect(sources, engines, plan, progress=None):
    """Runs every query on every engine (one thread per engine). {phrase: Evidence}"""
    def run_engine(engine):
        hits = []
        for i, (kind, query) in enumerate(plan, 1):
            for hit in sources.suggest(engine, query):
                hits.append((kind, query, hit))
            if progress:
                progress(engine, i, len(plan))
        return engine, hits

    evidence = {}
    with ThreadPoolExecutor(max_workers=len(engines) or 1) as pool:
        for engine, hits in pool.map(run_engine, engines):
            for kind, query, hit in hits:
                p = normalize(hit["phrase"])
                if not p:
                    continue
                evidence.setdefault(p, Evidence(p)).add(engine, kind, query, hit)
    return evidence


def rank_key(row):
    return (row["engine_count"], row["times_seen"], row["google_relevance"] or 0, -row["best_rank"])


def build_rows(evidence, matcher, skip_words, seed, niche):
    """Filters, merges variants, describes and ranks phrases. Returns (rows, Counter of drop reasons)."""
    kept, dropped = {}, Counter()
    for p, e in evidence.items():
        reason = drop_reason(p, matcher, skip_words)
        if reason:
            dropped[reason] += 1
        else:
            kept[p] = e
    rows = []
    for p, (e, variants) in merge_variants(kept).items():
        rows.append({
            "phrase": p,
            "variants": variants,
            "seed": matcher.seed,
            "niche": niche,
            "engines": sorted(e.engines),
            "engine_count": len(e.engines),
            "times_seen": len(e.queries),
            "google_relevance": e.google_relevance,
            "best_rank": e.best_rank,
            "min_prefix_len": None,
            "modifier": modifier(p, matcher),
            "shape": shape(p, matcher),
            "intent": intent(p),
            "cluster_id": None,
        })
    rows.sort(key=rank_key, reverse=True)
    dropped["merged_variant"] = len(kept) - len(rows)
    return rows, +dropped


def cluster(rows, matcher):
    """Groups rows by their first content word; sets row["cluster_id"]. Returns clusters, biggest first."""
    groups = defaultdict(list)
    for r in rows:  # rows are already ranked, so each group's first member is its head
        groups[cluster_key(r["phrase"], matcher)].append(r)
    ordered = sorted(groups.items(),
                     key=lambda kv: (kv[0] != "(general)", len(kv[1]),
                                     sum(r["engine_count"] for r in kv[1])),
                     reverse=True)
    clusters = []
    for i, (key, members) in enumerate(ordered, 1):
        cid = f"c{i:03d}"
        for r in members:
            r["cluster_id"] = cid
        clusters.append({
            "id": cid,
            "key": key,
            "head": members[0]["phrase"],
            "size": len(members),
            "engine_hits": sum(r["engine_count"] for r in members),
            "max_engines": max(r["engine_count"] for r in members),
            "intents": dict(Counter(r["intent"] for r in members)),
            "members": [r["phrase"] for r in members],
        })
    return clusters


def min_prefix_len(phrase, suggest):
    """Shortest prefix of `phrase` whose Google suggestions include it, or None.

    Google never suggests a phrase back when it is typed in full, so the search
    covers prefixes up to one character short. Binary search, so it assumes that
    once a phrase appears it keeps appearing as more letters are typed (true in
    practice for autocomplete). `suggest` takes a query and returns suggestion dicts.
    """
    def shows(n):
        return any(normalize(h["phrase"]) == phrase for h in suggest(phrase[:n]))

    if len(phrase) < 2 or not shows(len(phrase) - 1):
        return None
    lo, hi = 1, len(phrase) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if shows(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def rank_questions(questions, matcher, limit=25):
    """Stack Exchange questions on topic, most viewed first, deduped by link."""
    seen, out = set(), []
    for q in sorted(questions, key=lambda q: q["views"], reverse=True):
        if q["link"] in seen:
            continue
        seen.add(q["link"])
        out.append(dict(q, on_topic=matcher.span(normalize(q["title"])) is not None))
    return out[:limit]
