# Keyword Engine — Pre-Spend Roadmap

Goal: get the engine **mechanically correct and data-ready** before depositing the $50 at DataForSEO.
Rule that keeps us honest: every task below has a pass/fail you can observe for free. If you catch
yourself tweaking a score constant and re-running to see if the ranking "looks better" — stop, that's
the arbitrary-numbers trap. Score tuning waits for real data.

**How we work:** you code in VS Code, save, then tell Claude "check it." Claude reviews the diff,
flags problems, hands you the next step. Decisions marked **DECIDE** are open — talk them through
before implementing.

---

## Task 1 — Disk cache + fetch resilience

**Why first:** every later task needs repeated runs. Right now each run costs ~2+ minutes of
rate-limited fetching, and a single timeout kills the whole thing.

**Where:** `fetch_autocomplete` (line 32), `fetch_stackexchange_titles` (107), `fetch_youtube` (178),
and `main()` where they're called (866–869).

**Steps:**

1. **Collapse the four autocomplete loops into one helper.** They're identical except for the query
   string. Build the full list of queries first (suffixes, prefixes, questions, prepositions ×2),
   then loop once over it calling a single `_get_suggestions(query)` helper.
2. **Proper encoding + timeout + guard in the helper:**
   ```python
   def _get_suggestions(query):
       base = "http://suggestqueries.google.com/complete/search"
       try:
           response = requests.get(base, params={"client": "firefox", "hl": "en", "q": query}, timeout=10)
           response.raise_for_status()
           return response.json()[1]
       except Exception as e:
           FETCH_ERRORS.append(("autosuggest", query, str(e)))
           return []
   ```
   Module-level `FETCH_ERRORS = []`. Same try/except + `timeout=10` treatment for the Stack Exchange
   request. Wrap the yt_dlp call in `fetch_youtube` in try/except too.
3. **Fetch-health summary** at the end of `main()`: attempted / succeeded / failed per source, and
   print the `FETCH_ERRORS` entries.
4. **Disk cache.** One wrapper used by `main()`:
   ```python
   def cached_fetch(source, keyword, fetch_fn, refresh=False):
       # cache/{source}/{slug(keyword)}.json  →  {"fetched_at": iso_timestamp, "data": ...}
   ```
   `slug()` = lowercase, spaces→underscores, strip anything not alphanumeric/underscore.
   Load from cache if present unless `refresh=True`. Add `refresh` as a simple flag/constant at the
   top of `main()` for now (CLI arg later).

**Done when:**
- [ ] First run on a fresh keyword creates `cache/autosuggest/...json` etc.
- [ ] Second run of the same keyword finishes the fetch phase in seconds, offline.
- [ ] Killing wifi mid-fetch does NOT crash the run; the health summary reports the failures.

---

## Task 2 — Real pattern generalization (the big one)

**Why:** `extract_pattern` (line 324) promises `"best oak wood for furniture"` → `"best {keyword} for X"`
but line 340 is a no-op: `re.sub(r"\b(in|near|for|with|without)\b", r"\1", phrase)` replaces each word
with itself. So patterns only collapse when phrases are near-identical strings, and the `count == 1`
gate in `get_patterns()` (line 382) kills most of the long tail. This is a **candidate generation** bug —
it starves the engine's recall, which is its whole job.

**Design:**

1. Module-level keep-list (named constant):
   ```python
   PATTERN_KEEP_WORDS = {
       "{keyword}", "{num}",
       "in", "near", "for", "with", "without", "to", "of", "the", "a", "an",
       "my", "your", "is", "does", "me", "vs",
       *QUESTION_WORDS, *COMMERCIAL_WORDS, *MUST_INCLUDE_WORDS,
   }
   ```
   (Note: "near me" in LOCALITY_WORDS is two tokens — "near" and "me" both need to be in the set.)
2. In `extract_pattern`, after the existing `{keyword}` and `{num}` substitution: tokenize on
   whitespace, replace every token not in the keep-list with `{x}`, then collapse consecutive `{x}`
   runs into one.
3. **DECIDE with Claude before coding:** what does `is_valid_pattern` do with slot-heavy patterns?
   Proposal: reject patterns where, after removing `{keyword}`/`{num}`/`{x}`, no real word remains
   (e.g. `"{x} {keyword}"` carries no signal). Punctuation handling is also open — autocomplete
   phrases can contain `?` and `'`; decide whether to strip punctuation before tokenizing.

**Tests (write these FIRST — create `test_engine.py`, run with `python -m pytest`):**
```python
def test_generalizes_tail_word():
    pd = PatternDiscovery()
    assert pd.extract_pattern("best gravel for driveways", "gravel") == "best {keyword} for {x}"

def test_cross_keyword_collapse():
    pd = PatternDiscovery()
    a = pd.extract_pattern("best gravel for driveways", "gravel")
    b = pd.extract_pattern("best mulch for gardens", "mulch")
    assert a == b

def test_consecutive_slots_collapse():
    pd = PatternDiscovery()
    assert pd.extract_pattern("gravel for large modern driveways", "gravel") == "{keyword} for {x}"

def test_num_slot():
    pd = PatternDiscovery()
    assert pd.extract_pattern("gravel near me within 20 mi", "gravel") == "{keyword} near me within {num} {x}"
```

**Done when:**
- [ ] Tests green.
- [ ] Re-run a cached keyword: compare pattern count and survival past the count-gate before vs.
      after (Claude will diff the outputs with you). Expect a big jump — do NOT re-tune
      `WORD_COUNT_MULTIPLIERS` yet; just record the before/after numbers here.

---

## Task 3 — Word-boundary fixes

**Why:** raw substring checks make "asbestos" count as commercial ("best") and "showcase" match "how".
`contains_word()` already exists (line 248) — it's just not used everywhere.

**Where to replace `in` with `contains_word(w, text)`:**
- [ ] `score_pattern` line 300 (`MUST_INCLUDE_WORDS`), line 304 (`QUESTION_WORDS`), line 306 (`COMMERCIAL_WORDS`)
- [ ] `score_keyword` line 533 (`"best" in k or "cheap" in k or "affordable" in k`)
- [ ] `simulate_ctr` lines 711, 713 (`"best"`, `"near me"` — multi-word phrases work fine with the `\b` regex)
- [ ] `get_template_type` lines 563–565 (low risk, fix for consistency)

**Tests:**
```python
def test_asbestos_is_not_best():
    assert not contains_word("best", "asbestos removal cost")

def test_best_at_boundary():
    assert contains_word("best", "the best gravel")

def test_multiword():
    assert contains_word("near me", "gravel near me")
```

**Done when:** tests green AND Claude has re-read each edited region to confirm the edits actually
landed (this exact fix was claimed and lost once before).

---

## Task 4 — `save_pattern_db` merge fixes

**Why:** the JSON is becoming the permanent ledger; the merge path (line 400) corrupts it slowly.

**Fixes:**
1. After merging sources (line 413): recompute `existing[pattern]["source_diversity"] = len(existing[pattern]["sources"])` — it currently goes stale forever.
2. `examples.extend(...)` (line 410): dedupe (preserve order) and cap at 10.
3. Schema consistency: first save stores `sources` as a **list** (from `get_patterns` line 392), merge
   converts it to a **dict** of counts. Normalize: store `dict(self.pattern_sources[pattern])` from the
   start (in `get_patterns`/`build_combined`) so the file always holds dict-of-counts.
   `score_pattern` takes `len(sources)` either way — confirm diversity means *unique sources*, which
   `len()` of the dict gives you.

**Done when:**
- [ ] Run the same cached keyword twice; open `pattern_library.json`: diversity correct, examples
      capped and unique, every `sources` value is a dict.

---

## Task 5 — The dumb pre-filter + validation batch

**Why:** this is the piece that feeds DataForSEO. Deliberately dumb — at $0.06 per 1,000 keywords,
cleverness here saves fractions of a cent.

**Build:** `build_validation_batch(rows, ledger)` →
list of keyword strings that are: unique (case-folded), `is_probably_english`, 2–9 words,
and **not already in the validated ledger**. Write the batch to `validation_queue.json`.

The ledger is a new file `validated_keywords.json`: `{keyword_string: {"validated_at": iso, "search_volume": ..., "cpc": ..., "competition": ...}}`.
Empty for now — Task 6 fills it. Keywords in the ledger are never re-sent (refresh anything older
than ~6–12 months, later).

**Done when:**
- [ ] A run produces `validation_queue.json` with no dupes/junk, and re-running produces the same
      queue (nothing validated yet, so nothing drops out).

---

## Task 6 — DataForSEO client, tested for free

**Why:** build and debug the integration against their sandbox/fixture data BEFORE the $50 deposit,
so day one of paid credits is spent on data, not bugs.

**Build:** new file `dataforseo_client.py`:
- Endpoint: `keywords_data/google_ads/search_volume` standard queue (`task_post`, then `task_get`).
  $0.06 per task, up to 1,000 keywords per task, results in 1–3 hours.
- Basic-auth from environment variables (`DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`) — never hardcode.
- Base URL as a constant so it can point at `sandbox.dataforseo.com` vs production.
- `submit_batch(keywords)` → task id; `collect_results(task_id)` → parse into ledger entries;
  `update_ledger(results)` → merge into `validated_keywords.json` with `validated_at` stamps.
- Save a sample response from their docs as `fixtures/search_volume_response.json`; test the parse/
  ledger path against it in `test_engine.py`.

**Done when:**
- [ ] Parse + ledger tests green against the fixture.
- [ ] A sandbox round-trip works end to end (fake numbers, real plumbing).

---

## Task 7 — Free sanity spot-check (no code)

Take the top 20 scored candidates for ONE real niche and hand-check them in Google Keyword Planner
(free account, volume ranges). One binary question: is the top of the report obviously garbage, or
plausibly real? Several candidates showing real volume ranges = the machine is worth feeding.

---

## Side quest — pytrends verdict

Google broke the backend pytrends scrapes; `related_queries` often 429s or throws. Run once and
observe. If dead: delete the source (keep the `trend` column as a placeholder like the others).
Don't sink time into retry logic for a dead API.

---

## 💰 The spend trigger

Deposit the $50 when ALL of these are true — and note that scores, weights, and multipliers appear
nowhere in this list:

- [ ] Full run on one real niche completes end-to-end using cache
- [ ] Run survives forced fetch failures; health summary reports them
- [ ] Pattern-generalization tests green; before/after candidate counts recorded
- [ ] Word-boundary + merge fixes verified
- [ ] `validation_queue.json` is clean (deduped, filtered, ledger-aware)
- [ ] DataForSEO client round-trips against sandbox/fixtures

Then: submit the first real batch → results land in `validated_keywords.json` → **calibration begins**
(correlate each heuristic signal against real volume/CPC; reality assigns the weights; signals that
don't correlate get deleted).

---

## Deferred until real data exists (do not touch)

Score normalization / blend design (the 0.7/0.3 weights), per-keyword scoring inside pattern rows,
`WORD_COUNT_MULTIPLIERS` re-tuning, `TEMPLATE_MATCH_BONUS` / `JSON_HISTORY_BONUS` values, Tier 2
trusted-template feedback loop, pattern-DB niche namespacing. All of it is untunable without ground
truth — that's not a backlog, it's a quarantine.
