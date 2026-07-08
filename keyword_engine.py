from datetime import datetime
from collections import defaultdict, Counter
from pytrends.request import TrendReq

import os
import json
import re
import requests
import html
import time
import string
import yt_dlp


DEFAULT_WORD_COUNT_MULTIPLIER = 0.3 #9+ falls here

QUESTION_WORDS = ["how", "why", "what", "when"]
COMMERCIAL_WORDS = ["best", "cheap", "near", "cost", "price"]
MUST_INCLUDE_WORDS = ["calculator"]
MUST_INCLUDE_MULTIPLIER = 1.5
QUESTION_WORD_BONUS = 1
COMMERCIAL_WORD_BONUS = 2

# --- Blending config: pattern discovery vs template guesses ---
PATTERN_SCORE_WEIGHT = 0.7 
TEMPLATE_SCORE_WEIGHT = 0.3 

TEMPLATE_MATCH_BONUS = 2.0 # applied when a discovered pattern's shape matches a known template
JSON_HISTORY_BONUS_PER_DIVERSITY = 1.0 # applied per unique source seen historically in pattern_library.json

active_fetch_autocomplete = False
def fetch_autocomplete(keyword):
    all_phrases = []
    
    alphabet = list(string.ascii_lowercase)
    suffixes = alphabet
    prefixes = alphabet
    questions = ["how to", "when to", "why does", "what is"]
    prepositions = ["for", "with", "without", "near", "over", "under"]

    total_requests = len(alphabet) * 2 + len(questions) + len(prepositions) * 2
    current = 0

    for suffix in suffixes:
        base = "http://suggestqueries.google.com/complete/search"
        params = f"?client=firefox&hl=en&q={keyword} {suffix}"
        url = base + params
        response = requests.get(url)
        data = response.json()
        all_phrases.extend(data[1])
        current +=1
        print(f"autocomplete {current}/{total_requests}: '{keyword} {suffix}")
        time.sleep(0.5)


    for prefix in prefixes:
        base = "http://suggestqueries.google.com/complete/search"
        params = f"?client=firefox&hl=en&q={prefix} {keyword}"
        url = base + params
        response = requests.get(url)
        data = response.json()
        all_phrases.extend(data[1])
        current +=1
        print(f"autocomplete {current}/{total_requests}: '{prefix} {keyword}")
        time.sleep(0.5)


    for question in questions:
        base = "http://suggestqueries.google.com/complete/search"
        params = f"?client=firefox&hl=en&q={question} {keyword}"
        url = base + params
        response = requests.get(url)
        data = response.json()
        all_phrases.extend(data[1])
        current +=1
        print(f"autocomplete {current}/{total_requests}: '{question} {keyword}")
        time.sleep(0.5)


    for pre_preposition in prepositions:
        base = "http://suggestqueries.google.com/complete/search"
        params = f"?client=firefox&hl=en&q={pre_preposition} {keyword}"
        url = base + params
        response = requests.get(url)
        data = response.json()
        all_phrases.extend(data[1])
        current +=1
        print(f"autocomplete {current}/{total_requests}: '{pre_preposition} {keyword}")
        time.sleep(0.5)


    for post_preposition in prepositions:
        base = "http://suggestqueries.google.com/complete/search"
        params = f"?client=firefox&hl=en&q={keyword} {post_preposition}"
        url = base + params
        response = requests.get(url)
        data = response.json()
        all_phrases.extend(data[1])
        current +=1
        print(f"autocomplete {current}/{total_requests}: '{keyword} {post_preposition}")
        time.sleep(0.5)


    all_phrases = list(set(p.strip().lower() for p in all_phrases))
    return all_phrases

def fetch_stackexchange_titles(keyword):
    sites = ["diy", "woodworking", "engineering", "mechanics", "outdoors", "lifehacks", "gardening", "sustainability"]
    titles = []
    post_qty = 30
    total_requests = len(sites)
    current = 0

    for site in sites:
        current += 1
        print(f"stackexchange {current}/{total_requests}: '{site}'")
        base = f"https://api.stackexchange.com/2.3/search"
        params = f"?order=desc&sort=relevance&intitle={keyword}&pagesize={post_qty}&site={site}&filter=default" 
        url = base + params
        response = requests.get(url)
        data = response.json()
        if "items" not in data:
            print(f"Stack Exchange error on {site}: {data}")
            continue
        data["items"]
        posts = data["items"]
        for post in posts:
            titles.append(html.unescape(post["title"]))
        time.sleep(0.5)

    return list(set(titles))

def fetch_pytrends(keyword):
    try:
        total_requests = 2
        current = 0
        pytrends = TrendReq()
        pytrends.build_payload([keyword])
        time.sleep(1)
        current += 1
        print(f"pytrends {current}/{total_requests}: '{keyword}'")
        related = pytrends.related_queries()
        top = related[keyword]["top"]
        rising = related[keyword]["rising"]

        total_requests = 2
        current = 1
        
        
        pytrend_top_phrases = top["query"].tolist() if top is not None else []
                    
        pytrend_rising_phrases = rising["query"].tolist() if rising is not None else []
        
        current += 1
        print(f"pytrends {current}/{total_requests}: '{keyword}'")
        trend_df = pytrends.interest_over_time()
        
        trend_score = float(trend_df[keyword].mean()) if not trend_df.empty else None
        

        return pytrend_top_phrases, pytrend_rising_phrases, trend_score
    except Exception as e:
        print(f"pytrends error: {e}")
        return [], [], None

def is_probably_english(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True # no letters at all (e.g. just numbers/emoji) - don't over filter

    ascii_letters = [c for c in letters if c.isascii()]
    ratio = len(ascii_letters) / len(letters)

    return ratio >= .8 # allow some accented characters, loanwords, etc



def fetch_youtube(keyword):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "getcomments": True,
        "ignoreerrors": True,
        "extractor_args": {"youtube": {"max_comments": ["20"]}}
    }
    print(f"youtube: fetching results for '{keyword}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        results = ydl.extract_info(f"ytsearch10:{keyword}", download=False)

    videos = results["entries"]
    yt_titles = []
    yt_comments = []

    skip_words = ["official video", "official audio", "official music", "lyrics", "visualizer", "music video", "comedy", "crystal castles", "cypher"]
    print(f"youtube: processing {len(videos)} videos")
    video_step = 0
    fetched_video = 0
    for video in videos:
        video_step += 1
        print(f"youtube: processing video {video_step}")
        if video is None:
            print ("youtube: video is None, skipping")
            continue
        title = video["title"]
        if not is_probably_english(title):
            print("youtube: video is probably not english, skipping")
            continue
        if any(skip in title.lower() for skip in skip_words):
            print("youtube: video contained a skip word, skipping")
            continue
        fetched_video += 1
        print(f"youtube: video {fetched_video} passed filters")
        yt_titles.append(title)
        if video.get("comments"):
            for yt_comment in video["comments"]:
                yt_comments.append(yt_comment["text"])
    
    print(f"youtube: collected {len(yt_titles)} titles, {len(yt_comments)}")

    yt_titles = list(set(yt_titles))
    yt_comments = list(set(yt_comments))

    return yt_titles, yt_comments
    

LOCALITY_WORDS = ["near me", "nearby", "in my area"]
JOB_SEEKER_WORDS = ["jobs", "hiring", "salary", "career"]
TRANSACTIONAL_WORDS = ["price", "cost", "buy"]
INFORMATIONAL_WORDS = ["how to", "what is", "why", "guide"]

def get_intent(keyword):
    k = keyword.lower()
   
    if any(contains_word(w, k) for w in TRANSACTIONAL_WORDS):
        return "transactional"
    if any(contains_word(w, k) for w in INFORMATIONAL_WORDS):
        return "informational"
    return "informational"

def is_local_search(keyword):
    k = keyword.lower()
    return any(contains_word(w, k) for w in LOCALITY_WORDS)

def is_job_seeker(keyword):
    return any(contains_word(w, keyword.lower()) for w in JOB_SEEKER_WORDS)

def contains_word(word, text):
    return re.search(r'\b' + re.escape(word) + r'\b', text) is not None

def is_valid_pattern(pattern):
    if "{keyword}" not in pattern:
        return False

    # No more than 3 consecutive identical characters
    if re.search(r'(.)\1{3,}', pattern):
        return False

    # must have at least 2 words after removing {keyword}
    stripped = pattern.replace("{keyword}", "").strip()
    words = stripped.split()

    if len(words) < 1:
        return False

    if len(words) == 1 and len(words[0]) == 1:
        return False

    return True

# --- Pattern scoring config ---
# Multiplier applied based on how many words are in the pattern
# (excluding {keyword} itself). Tune these to change how much
# short/long patterns are penalized or favored.

WORD_COUNT_MULTIPLIERS = {
    2: 0.5,
    3: 1.5, 
    4: 1.5,
    5: 1.0,
    6: 1.0,
    7: .6,
    8: .6
}


def score_pattern(pattern, count, sources):
    """
    Scores a discovered pattern based on:
    - source diversity (how many different platforms it appeared on)
    - word count (patterns in the "sweet spot" length score higher)
    - intent signals (question words, commercial modifiers)
    """

    diversity = len(sources)

    stripped = pattern.replace("{keyword}", "").strip()
    word_count = len(stripped.split())

    is_must_include = any(mw in pattern for mw in MUST_INCLUDE_WORDS)
    multiplier = 1.0 if is_must_include else WORD_COUNT_MULTIPLIERS.get(word_count, DEFAULT_WORD_COUNT_MULTIPLIER)

    intent_bonus = 0
    if any(qw in pattern for qw in QUESTION_WORDS):
        intent_bonus += QUESTION_WORD_BONUS
    if any (cw in pattern for cw in COMMERCIAL_WORDS):
        intent_bonus += COMMERCIAL_WORD_BONUS

    score = (count * diversity * multiplier) + intent_bonus

    if is_must_include:
        score *= MUST_INCLUDE_MULTIPLIER

    return score

class PatternDiscovery:

    def __init__(self):
        self.pattern_counts = defaultdict(int)
        self.examples = defaultdict(list)
        self.pattern_sources = defaultdict(Counter)
        self.combined_patterns = {}

    def extract_pattern(self, phrase, keyword):
        """
        Converts a real SERP phrase into a generalized pattern.
        Example:
            "best oak wood for furniture"
            → "best {keyword} for X"
        """

        phrase = phrase.lower().strip()

        # normalize keyword out first - use word boundaries so we don't match "gravel" inside" "gravely" etc
        placeholder = "{keyword}"
        phrase = re.sub(r'\b' + re.escape(keyword.lower()) + r'\b', placeholder, phrase)

        # generalization rules (lightweight NLP without libraries)
        phrase = re.sub(r"\b\d+\b", "{num}", phrase)
        phrase = re.sub(r"\b(in|near|for|with|without)\b", r"\1", phrase)

        return phrase
    
    def ingest(self, phrases, keyword, sources):
        for p in phrases:
            pattern = self.extract_pattern(p, keyword)
            self.pattern_counts[pattern] += 1
            self.examples[pattern].append(p)
            self.pattern_sources[pattern][sources] += 1

        
        #print(f"\n\ncombined patterns: {combined_patterns}\n\n")
    def build_combined(self, patterns):
        self.combined_patterns = {}

        for p in patterns:
            self.combined_patterns[p["pattern"]] = {
                "count": p["count"],
                "examples": p["examples"],
                "sources": p["sources"],
                "source_diversity": p["source_diversity"],
                "score": p["score"]
            }
        return self.combined_patterns
    
    def get_patterns(self):
        """
        Returns discovered patterns sorted by frequency
        """
        def has_intent_signal(pattern):
            return (
                any(contains_word(mw, pattern) for mw in MUST_INCLUDE_WORDS)
                or any(contains_word(cw, pattern) for cw in COMMERCIAL_WORDS)
                or any(contains_word(qw, pattern) for qw in QUESTION_WORDS) 
            )

        results = []
        got_pattern = 0
        for pattern, count in self.pattern_counts.items():
            if not is_valid_pattern(pattern):
                continue
            if count == 1 and not has_intent_signal(pattern):
                continue
            sources_dict = dict(self.pattern_sources[pattern])
            score = score_pattern(pattern,count,self.pattern_sources[pattern])
            got_pattern += 1
            print(f"pattern {got_pattern} got")
            results.append({
                "pattern": pattern,
                "count": count,
                "examples": self.examples[pattern][:3],
                "sources": list(self.pattern_sources[pattern]),
                "source_diversity": len(sources_dict),
                "score": score
            })


        return sorted(results, key=lambda x: x["score"], reverse=True)

def save_pattern_db(combined_patterns, filepath="pattern_library.json"):
    print(f"\n --- SAVING JSON --- \nwriting pattern DB to: {os.path.abspath(filepath)}")
    existing = {}
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            existing = json.load(f)

    for pattern, data in combined_patterns.items():
        if pattern in existing:
            existing[pattern]["count"] += data["count"]
            existing[pattern]["examples"].extend(data["examples"])
            merged_sources = Counter(existing[pattern]["sources"])
            merged_sources.update(data["sources"])
            existing[pattern]["sources"] = dict(merged_sources)
            existing[pattern]["score"] = score_pattern(
               pattern, 
               existing[pattern]["count"],
               existing[pattern]["sources"]
            )
        else:
            existing[pattern] = data

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent = 2)



def pattern_to_candidates(pattern_data, keyword):
    """
    Converts one discovered pattern into one or more candidate keyword rows, using the patterns own real examples from this run.
    """
    candidates = []

    examples = pattern_data["examples"]
    if not examples:
        # fallback: fill the abstract pattern directly (shouldn't normally happen)
        examples = [pattern_data["pattern"].replace("{keyword}", keyword)]

    for example in examples:
        candidates.append({
            "keyword": example,
            "pattern": pattern_data["pattern"],
            "pattern_score": pattern_data["score"],
            "sources": pattern_data["sources"],
            "source_diversity": pattern_data["source_diversity"]
        })
    return candidates

class Template:
    def __init__(self, template_id, pattern, weight):
        self.id = template_id
        self.pattern = pattern
        self.weight = weight


print("keyword engine online")

TEMPLATES = [
        
        Template("best", "best {keyword}", 1.5),
        Template("cheap", "cheap {keyword}", 1.3),
        Template("affordable", "affordable {keyword}", 1.3),
        Template("how_to", "how to {keyword}", 0.8),
        Template("definition", "what is {keyword}", 0.9),
        Template("explanation", "why is {keyword}", 0.9),
        Template("calculator", "{keyword} calculator", 1.1),
        Template("cost", "{keyword} cost", 1.4),
        Template("price", "{keyword} price", 1.6),
        Template("guide", "{keyword} guide", 1.2),
        Template("ideas", "{keyword} ideas", 1.0),
        Template("tools", "{keyword} tools", 1.0),
        Template("examples", "{keyword} examples", 1.0),
        Template("near_me", "{keyword} near me", 1.4)
]

def expand_keyword(keyword):
    
    """
    Takes a base keyword and generates structured variations.
    This is the core logic we'll keep improving.
    """
    
    variations = []

    for t in TEMPLATES:
        variations.append({
            "keyword": t.pattern.format(keyword=keyword),
            "template": t.pattern,
            "template_id": t.id,
            "template_weight": t.weight
        })
    
        
   
    return variations

def matches_known_template(pattern):
    return any(pattern == t.pattern for t in TEMPLATES)

def load_pattern_history(filepath="pattern_library.json"):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def json_history_bonus(pattern, history):
    if pattern in history:
        diversity = history[pattern].get("source_diversity", 0)
        return diversity * JSON_HISTORY_BONUS_PER_DIVERSITY
    return 0





LOCAL_BONUS = 1
JOB_SEEKER_BONUS = 1

def score_keyword(kw, intent):
        score = 0

        k = kw.lower()

        # intent weight
        if  intent == "transactional":
            score += 3
        else:
            score += 1
        if is_local_search(kw):
            score += 1
        if is_job_seeker(kw):
            score += JOB_SEEKER_BONUS
        # high-intent modifiers
        if "best" in k or "cheap" in k or "affordable" in k:
            score += 2

        # keyword structure
        word_count = len(kw.split())

        if 2 <= word_count <= 4:
            score += 2
        elif word_count > 4:
            score += 1

        # penalty for awkward phrasing
        if kw.startswith("how to") or kw.startswith("what is"):
            score -= 1

        return score



def get_template_type(template):
    # NOTE: "local" is still a flat branch here, same limitation classify_keyword
    # used to have before the get_intent/is_local_search/is_job_seeker split.
    # Full fix (deferred): make get_template_type return only the core type
    # (modifier/monetization/informational/other), and have analyze_templates
    # separately track is_local_search(template) as an independent flag per
    # template, with its own pulled-out section in the type-performance report
    # — same flag-based pattern as the row-level fix, applied to templates.
    # Not urgent: none of the current 14 TEMPLATES collide on this axis yet.
    if (template.startswith("best") or template.startswith("cheap") or template.startswith("affordable")):
        return "modifier"
    if "price" in template or "cost" in template:
        return "monetization"
    if "how to" in template or "what is" in template or "why" in template:
        return "informational"
    if is_local_search(template):
        return "local"
    return "other"
    
    



def analyze_templates(rows):
    template_stats = {}
    type_stats = {}

    for r in rows:
        t = r["template"]
        tp = get_template_type(t)
    
        # template stats
        if t not in template_stats:
            template_stats[t] = {
                "count": 0,
                "total_score": 0
            }
    
        template_stats[t]["count"] += 1
        template_stats[t]["total_score"] += r["score"]
    
        # type stats
        if tp not in type_stats:
            type_stats[tp] = {"count": 0, "total_score": 0}
    
        type_stats[tp]["count"] += 1
        type_stats[tp]["total_score"] += r["score"]
    
    # convert to sorted list
    template_results = []
    type_results = []
    
    for t, d in template_stats.items():
        template_results.append({
            "template": t,
            "avg_score": d["total_score"] / d["count"],
            "count": d["count"]
        })
    
    for t, d in type_stats.items():
        type_results.append({
            "type": t,
            "avg_score": d["total_score"] / d["count"],
            "count": d["count"]
        })


    
    return (
        sorted(template_results, key=lambda x: x["avg_score"], reverse=True),
        sorted(type_results, key=lambda x: x["avg_score"], reverse=True)
)

def get_trusted_template_info(template_pattern, history):
    """
    checks whether a static template's shape has been validated by real historical pattern data. Returns (is_trusted, historical_count)
    """

    if template_pattern in history:
        return True, history[template_pattern].get("count", 0)
    return False, 0

def build_template_row(item, keyword, intent, history, trend_score):
    is_trusted, trusted_count = get_trusted_template_info(item["template"], history)

    base_score = score_keyword(item["keyword"], intent)
    template_score = base_score * item["template_weight"]

    json_bonus = json_history_bonus(item["template"], history)

    final_score = (template_score * TEMPLATE_SCORE_WEIGHT) + json_bonus

    return {
        "category": intent,
        "is_local": is_local_search(item["keyword"]),
        "is_job_seeker": is_job_seeker(item["keyword"]),
        "keyword": item["keyword"],
        "intent": intent,
        "word_count": len(item["keyword"].split()),
        "length": len(item["keyword"]),
        "score": final_score,
        "ctr": simulate_ctr(item["keyword"], final_score, intent),
        "source_keyword": keyword,
        "template": item["template"],
        "template_id": item["template_id"],
        "search_volume": None,
        "cpc": None,
        "competition": None,
        "difficulty": None,
        "trend": trend_score,
        "origin": "template",
        "is_trusted_template": is_trusted,
        "trusted_count": trusted_count,
        "pattern_score": None,
        }

def build_pattern_rows(pattern_data, keyword, history, trend_score):
    rows = []
    candidates = pattern_to_candidates(pattern_data, keyword)

    matched_template = matches_known_template(pattern_data["pattern"])
    template_bonus = TEMPLATE_MATCH_BONUS if matched_template else 0
    json_bonus = json_history_bonus(pattern_data["pattern"], history)

    final_score = (pattern_data["score"] * PATTERN_SCORE_WEIGHT) + template_bonus + json_bonus

    for c in candidates:
        intent = get_intent(c["keyword"])
        rows.append({
            "category": intent,
            "is_local": is_local_search(c["keyword"]),
            "is_job_seeker": is_job_seeker(c["keyword"]),
            "keyword": c["keyword"],
            "intent": intent,
            "word_count": len(c["keyword"].split()),
            "length": len(c["keyword"]),
            "score": final_score,
            "ctr": simulate_ctr(c["keyword"], final_score, intent),
            "source_keyword": keyword,
            "template": pattern_data["pattern"],
            "template_id": "discovered",
            "search_volume": None,
            "cpc": None,
            "competition": None,
            "difficulty": None,
            "trend": trend_score,
            "origin": "discovered",
            "is_trusted_template": matched_template,
            "trusted_count": None,
            "pattern_score": pattern_data["score"]
        })

    return rows

def simulate_ctr(keyword, score, intent):
    ctr = 0.05 # base 5%

    if intent == "transactional":
        ctr += 0.08
    if "best" in keyword:
        ctr += 0.05
    if "near me" in keyword:
        ctr += 0.06
    if score >= 5:
        ctr += 0.04

    return round(ctr, 3)

def export_to_csv(rows):
    import csv
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename  = f"report_{timestamp}.csv"

    with open(filename, "w", newline="", encoding="utf8") as f:
        writer = csv.writer(f)

        # HEADERS
        writer.writerow([
            "category", 
            "keyword", 
            "intent", 
            "word_count", 
            "length", 
            "score",
            "ctr",
            "search_volume",
            "cpc",
            "competition",
            "difficulty",
            "trend",
            "source_keyword",
            "template",
            "template_id"
            ])

        for row in rows:
            writer.writerow([
                row["category"],
                row["keyword"],
                row["intent"],
                row["word_count"],
                row["length"],
                row["score"],
                row["ctr"],
                row["search_volume"],
                row["cpc"],
                row["competition"],
                row["difficulty"],
                row["trend"],
                row["source_keyword"],
                row["template"],
                row["template_id"]
            ])

    print(f"\nCSV saved as {filename}")

def export_to_md(rows, patterns=None):
        report = (
            "# Keyword Report\n"
        )

        if patterns:
            report += "\n\n# Discovered SERP patterns\n\n"

            for p in patterns:
                report += (
                    f"- Pattern: {p['pattern']}\n"
                    f" Count: {p['count']}\n"
                    f" Examples: {', '.join(p['examples'])}\n\n"
                )

        report += (
            f"\nGenerated: {datetime.now()}\n"
            f"Total Keywords: {len(rows)}\n"
        )
            
        
        grouped = {}

        for r in rows:
            grouped.setdefault(r["category"], []).append(r)

            

        for category, items in grouped.items():
            report += f"\n## {category.upper()} ({len(items)})\n"

            for r in items:
                report += (
                    f"  - {r['keyword']}\n"
                    f"  - score: {r['score']:.2f}\n"
                    f"  - ctr: {r['ctr']:.3f}\n"
                    f"  - search volume: {r['search_volume']}\n"
                    f"  - cpc: {r['cpc']}\n"
                    f"  - competition: {r['competition']}\n"
                    f"  - difficulty: {r['difficulty']}\n"
                    f"  - trend: {r['trend']}\n"
                    f"  - source: {r['source_keyword']}\n\n"
                    f"  - template: {r['template']}\n"
                    f"  - template: {r['template_id']}\n"
                )
                    
        local_rows = [r for r in rows if r["is_local"]]
        if local_rows:
            report += f"\n## LOCAL SEARCHES ({len(local_rows)})\n"
            for r in local_rows:
                report += f"  - {r['keyword']} (score: {r['score']: .2f})\n"

        job_seeker_rows = [r for r in rows if r["is_job_seeker"]]
        if job_seeker_rows:
            report += f"\n## JOB SEEKER SEARCHERS ({len(job_seeker_rows)})\n"
            for r in job_seeker_rows:
                report += f"  - {r['keyword']} (score: {r['score']:.2f})\n"

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"report_{timestamp}.md"

        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)

        print(f"\nReport saved as {filename}")



def main():
    # step 1: input keyword
    keyword = input("Enter base keyword: ")
    source_keyword = keyword

    # step 2: generate expansions
    results = expand_keyword(keyword)

    grouped_keywords = {
        "informational": [],
        "local": [],
        "transactional": []
    }
    rows = [];

    # step 3: output results
    print ("\n--- CLASSIFIED KEYWORDS --- \n")

    for r in results:
        
        category = get_intent(r["keyword"])
        grouped_keywords[category].append(r)
        
    print ("\n--- GROUPED KEYWORDS --- \n")

    for category, items in grouped_keywords.items():
        print(f"\n{category.upper()} ({len(items)})")
        for item in items:
            print(f" - {item}")

    autocomplete_phrases = fetch_autocomplete(keyword)
    stackexchange_titles = fetch_stackexchange_titles(keyword)
    pytrend_top_phrases, pytrend_rising_phrases, trend_score = fetch_pytrends(keyword)
    yt_titles, yt_comments = fetch_youtube(keyword)

    

# step 4: build rows (THIS is the important part)
    history = load_pattern_history()
    for category, keywords in grouped_keywords.items():
       for item in keywords:
           intent = category
           rows.append(build_template_row(item, keyword, intent, history, trend_score))

    
    #print("\nSAMPLE ROWS: \n")           
    #print(rows[:3])

    #export_to_md(grouped_keywords)

 

    # step 5.5: REAL PHRASES + PATTERN DISCOVERY

    

    discovery = PatternDiscovery()
    discovery.ingest(autocomplete_phrases, keyword, "autosuggest")
    discovery.ingest(stackexchange_titles, keyword, "stackexchange")
    discovery.ingest(pytrend_top_phrases, keyword, "pytrends_top")
    discovery.ingest(pytrend_rising_phrases, keyword, "pytrends_rising")
    discovery.ingest(yt_titles, keyword, "yt_titles")
    discovery.ingest(yt_comments, keyword, "yt comments")

    patterns = discovery.get_patterns()

    for pattern_data in patterns:
        rows.extend(build_pattern_rows(pattern_data, keyword, history, trend_score))

    combined_patterns = discovery.build_combined(patterns)
    save_pattern_db(combined_patterns)

    print ("\n--- DISCOVERED PATTERNS ---")
    for p in patterns:
        print(p)

    # step 5: sort
    rows.sort(
        key=lambda r: (r["score"], r["word_count"]), 
        reverse=True)
    # step 6: exports
    export_to_csv(rows)
    export_to_md(rows)

    # step 7: analysis
    template_report, type_report = analyze_templates(rows)


    print("\n--- TEMPLATE PERFORMANCE ---\n")

    for t in template_report:
        print(f"{t['template']} | avg: {t['avg_score']:.2f} | count: {t['count']}")

    print("\n--- TYPE PERFORMANCE ---\n")
    for t in type_report:
        print(f"{t['type']} | avg: {t['avg_score']:.2f} | count: {t['count']}")

    print("\n--- REAL PHRASES ---\n")
    print(f"autocomplete: {autocomplete_phrases}\n")
    print(f"stackexchange_titles: {stackexchange_titles}\n")
    print(f"pytrend_top_phrases: {pytrend_top_phrases},\n pytrend_rising_phrases: {pytrend_rising_phrases}/n trend_score: {trend_score}\n")
    print(f"\nyoutube_titles: {yt_titles}, \nyoutube_comments: {yt_comments}")

    
if __name__ == "__main__":
        main()



