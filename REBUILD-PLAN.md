# Keyword Engine: reassessment and rebuild plan

> **Status 2026-10-21:** Josh chose the fastest option ("too important to let it hold me up"), so
> Claude built M0–M5 the same day. The code is in this repo; `README.md` is the output contract.
> Still open: **M6** (Keyword Planner reality check, Josh) and **M7** (optional paid check).
> Changes from the plan below:
> - Clusters key on the first content word.
> - The Stack Exchange questions are a separate list, not a per-phrase field.
> - Wikipedia only counts an exact title or a strict match.

Written 2026-10-21 by Claude (Opus 5.5), from your request to reassess the engine yourself
rather than follow Fable's July roadmap as-is. Fable's plan is at
`../Keyword_engine/keyword_engine/ROADMAP.md`; the prototype is next to it. Nothing in either
folder was changed.

## The short version

- **Rebuild it, don't patch it.** The July prototype only fills its pattern library with raw
  phrases, and most of its scoring is made-up numbers. Very little of the code is worth keeping.
- **Free data comes first.** Five free autocomplete sources work today (checked live 10-21).
  On top of them come Stack Exchange question views and Wikipedia page views.
  The engine ranks by *evidence it can show*, not by a weighted score.
- **First milestone:** one seed in, a ranked list of real searches plus a report out. That's
  about 150 lines, and it's already useful to the SEO engine.
- **Paid volume data is an optional add-on, much later.** Your "tons of pulls for about $10"
  memory is DataForSEO: $0.06 per batch of up to 1,000 keywords. But they reportedly require
  a $50 minimum deposit.

## What's actually wrong with the prototype

I checked the saved output, not just the code.

| Problem | Evidence |
|---|---|
| Pattern discovery doesn't generalize | `pattern_library.json` has 650 "patterns". **632 were seen exactly once.** They're raw phrases with the keyword swapped out (`{keyword} monkey near me`), not patterns. Line 340 replaces each word with itself. |
| All the history is one niche | About 350 of the 650 are gravel. Patterns from different seeds land in one file with no niche label. |
| Wrong country | No region was set, so UK results got in (`b&q` shows up 7 times). |
| One error kills the run | No timeouts, no URL encoding, no try/except on autocomplete. No cache, so every run fetches everything again (2+ minutes). |
| Dead source | pytrends was archived on April 17, 2025. Google's official Trends API is an invite-only alpha. |
| Slow, noisy source | YouTube comments via yt_dlp: the slowest part and the noisiest text. |
| Stack Exchange is aimed at the wrong sites | The site list is hard-coded to DIY and woodworking. `reef tank` on `diy` found 1 question; on `pets` it found 5 good ones. |
| Scores are invented | Two scoring systems blended 0.7/0.3, word-count multipliers, template weights, and a `simulate_ctr` that is pure guesswork. Nothing measures demand. |
| Library file is getting corrupted | Every `sources` field is a list, but the merge code expects a dict. Source counts never update. |
| Code isn't where people look | GitHub `main` shows only a README; the code is on `master`. The real code lives in `Keyword_engine/`, which isn't a git repo. |

**Worth keeping:** `contains_word`, `is_probably_english`, the idea of turning phrases into
patterns, counting how many sources a phrase shows up in, and Fable's rule that **no score
gets tuned without real data**.

## Where I agree and disagree with Fable's plan

| Fable task | My call |
|---|---|
| 1. Cache + error handling | **Keep.** It becomes milestone 1, built in from the first line. |
| 2. Real pattern generalization | **Keep the idea and its tests.** It becomes milestone 2, used to group phrases into clusters. |
| 3. Word-boundary fixes | **Not needed.** New code uses `contains_word` everywhere from the start. |
| 4. Library merge fixes | **Drop for v1.** Each run gets its own output folder. A cross-run library, labeled by niche, comes later. |
| 5. Simple pre-filter + queue for paid checks | **Keep, but optional and late** (milestone 7). |
| 6. DataForSEO client | **Optional, last.** Free data is the core, as you said. |
| 7. Free spot-check in Keyword Planner | **Keep, and move it earlier.** It's how we know v1 actually works (milestone 6). |
| pytrends side quest | **Settled:** it's dead. Drop it. |

The big difference: Fable's plan ends at "deposit $50 and calibrate." Mine has to be useful
**without** paid data, because the projects waiting on it need output now.

## Free data, checked live on 2026-10-21 (seed: `reef tank`)

| Source | Works? | What it gives |
|---|---|---|
| Google autocomplete | Yes, 0.5 s | 10 suggestions. With the `chrome` client it also returns **relevance numbers** (601, 600, 562...), a free signal of ranking order. |
| YouTube autocomplete | Yes | What people search for videos ("reef tank setup", "tour", "build"). |
| Bing autocomplete | Yes | 16 suggestions. |
| DuckDuckGo autocomplete | Yes | 8 suggestions. |
| Amazon autocomplete | Yes | Buying intent ("par meter for reef tank", "kalkwasser for reef tank"). |
| Stack Exchange API | Yes, 300 requests/day without a key | Question titles **plus view counts**, which are real demand numbers. The site list has to come from the niche config. |
| Wikipedia page views | Yes | Monthly views per topic (Reef aquarium: 541 in Sept 2025). Good for comparing seeds. |
| Reddit | **No.** Blocked without a login. | Skip it. |
| eBay | Endpoint changed | Skip it for now. |

**Free evidence the engine can show for each phrase:**
- how many of the 5 autocomplete sources suggested it
- how many different expansion queries surfaced it
- Google's relevance number
- the shortest prefix that brings it up (`reef tank` shows up after typing just `reef t`; earlier means more popular)
- Stack Exchange views for matching questions

Each of these can be counted and checked, so no number is invented.

**Paid, later and optional:**
- **DataForSEO:** $0.06 per batch of up to 1,000 keywords, with results in 1–3 hours.
  Third-party reviews say there's a $50 minimum deposit and $1 of free credit at signup; their
  pricing page doesn't say either. $1 would cover about 16 batches, roughly 16,000 keywords.
- **Keywords Everywhere** is now much pricier: the smallest package is $80, at 1 credit per keyword.
- **Google Keyword Planner** is free with an Ads account. It gives volume ranges and is used
  for the milestone 6 check.

## What the projects waiting on it need

| Project | What it needs from the engine |
|---|---|
| WaypointLumen SEO engine | Ranked keywords with fixed field names, grouped so one cluster = one article. This is its first open question. |
| GildSpan marketing pipeline | The same output, with niche settings (sources and Stack Exchange sites) per pen name, plus Stack Exchange questions as forum leads. |
| Nature data engine | **Compare mode:** given many seeds (counties, species), rank which to do first. |
| Stock-photo finder, Gap Finder | The shared engine contract and a source plug-in shape. Their own sources live in their own code. |

That gives the engine **two modes**:
- **`expand`:** one seed turns into ranked phrases and clusters.
- **`compare`:** a list of seeds gets ranked against each other.

## The output contract (what other engines read)

This follows the engine contract in `workbench/ideas/research/production-engines-plan.md`:
an input plus a config, a standard output, validation checks, and a short report.
Every run writes one folder, `runs/<date>-<seed-slug>/`:

- **`phrases.jsonl`**, one line per phrase:
  - `phrase`, `seed`, `niche`
  - `engines` (list), `engine_count`, `times_seen`
  - `google_relevance`, `min_prefix_len`
  - `modifier` (the words beyond the seed), `shape` (`best {keyword} for {x}`)
  - `intent` (question / commercial / local / informational)
  - `cluster_id`
- **`clusters.json`:** each cluster's id, head phrase, members and total evidence.
- **`run.json`:** run id, seed, niche, settings, and for each source what was tried, what
  worked and what failed, plus how long it took. Cost is 0 unless a paid check ran.
- **`report.md`:** the top clusters, the top questions and a health summary, short enough to read on a phone.

Ranking is a plain tuple sort, `(engine_count, times_seen, google_relevance)`, so you can always
see why one phrase beats another. Validation checks before a run counts as done:
- no duplicate phrases
- every phrase is English and contains the seed or one of its synonyms
- at least 3 of the 5 sources worked
- the report exists

## Code layout (new, inside this repo)

```
keyword-engine/
  keyword_engine/
    sources.py     one shared fetch helper + one small function per source
    cache.py       cache/<source>/<query-slug>.json, with a refresh flag
    analyze.py     clean up, pull out modifier and shape, cluster, intent, rank
    report.py      writes the run folder (jsonl, json, md)
    cli.py         `python -m keyword_engine expand "reef tank" --niche reef`
  niches/reef.json  region, Stack Exchange sites, synonyms, skip words
  tests/            pytest, plus saved real responses in tests/fixtures/
  runs/             output (gitignored)
```

Five small files, each with one job. The July prototype stays where it is as read-only reference.

## Milestones, in typing order

Each milestone ends with something you can run and see. Function names are listed so we can go
one at a time, the usual way.

**M0 — Repo setup (one sitting)**
- Folder layout, `requirements.txt` (just `requests` and `pytest`), `.gitignore` for `cache/` and `runs/`.
- Decide what happens to the `master` branch (see decisions below).

**M1 — Fetch, cache, report (this is the point where it's useful)**
- `fetch_json(url, params)`: one helper with a timeout, error handling, a pause between
  requests, and a log of every failure.
- `cached(source, query, fetch_fn, refresh)`: save and load JSON on disk.
- One function each: `google_suggest`, `youtube_suggest`, `bing_suggest`, `ddg_suggest`, `amazon_suggest`.
- `build_queries(seed)`: the seed itself, seed + a–z, a–z + seed, question words, prepositions.
- `collect(seed)`: runs every query through every source and records which source and query surfaced each phrase.
- `rank(phrases)`, then `write_report(run)`.
- **Done when:** `expand "reef tank"` writes a run folder; the second run is near-instant and works
  offline; turning Wi-Fi off mid-run doesn't crash it, and the health summary shows the failures.

**M2 — Patterns and clusters**
- `normalize(phrase)`, `modifier(phrase, seed)`, and `shape(phrase, seed)` (Fable's `{x}`
  generalization, with his 4 tests).
- `cluster(phrases)`: phrases with the same main modifier word go together ("lights",
  "lighting", "light" end up in one cluster). Keep it rule-based, no AI.
- `intent(phrase)`.
- **Done when:** the tests pass and the reef report reads as sensible article-sized groups.

**M3 — Questions and demand**
- `stackexchange_questions(seed, sites)`, `wiki_pageviews(title)`, `min_prefix_len(phrase)`
  (only for the top 50 phrases, to keep requests low).
- Niche config files.
- **Done when:** the report has a "top questions by views" section and prefix length for the top phrases.

**M4 — Compare mode**
- `compare(seeds)`: ranks seeds by Wikipedia views, how many suggestions each gets, and prefix length.
- **Done when:** a list of 10 counties or species comes out ranked, with the evidence shown.

**M5 — Contract and validation**
- `validate(run)` with the checks above. Lock the field names in `README.md` so the SEO
  engine can read them.
- **Done when:** the SEO engine's first open question can be answered by pointing at the README.

**M6 — Reality check (no code)**
- Take the top 20 reef phrases and look them up in Google Keyword Planner (free volume ranges).
- If the top of the list is mostly real, v1 works. If it's mostly junk, we find out why before going further.

**M7 — Optional paid check**
- Simple pre-filter → `validation_queue.json` → DataForSEO client, tested against their free
  sandbox first. Results go in a ledger and never get re-sent.
- Only now does any weighted score get tuned, and only against real volume numbers.

M1–M3 is the core. With M1 alone, the SEO engine has something real to read.

## Decisions for you

1. **Who types it?** My default is your teaching rule: you type every function, I explain
   first and check each one. Faster option: you type the engine (sources, analyze, cli) and I
   write the test fixtures and saved responses, which are data, not logic. Fastest option:
   I build it all. That's quickest, but it changes the resume line that says you wrote it.
2. **First niche: reef keeping?** I'd pick it. You know reef keeping well enough to spot junk
   results at a glance. It also feeds the reef pen name, the reef how-to portfolio piece and the
   stock-photo test niche (blennies).
3. **Repo:** new code goes on `main` in this repo. The July prototype stays in
   `Keyword_engine/` untouched. Should `master` (the old code) get merged in as a `legacy/`
   folder or just left as a branch? I'd leave it as a branch.

## Not in v1 (on purpose)

- Competition and supply signals: Google results pages can't be scraped reliably and it breaks their terms.
- A cross-run pattern library.
- YouTube comments.
- Trends.
- Reddit.
- Running as a harness lane: teaching-mode code stays out of the autonomous lanes. Only the
  output format gets defined, per the engines plan.

## Sources

- DataForSEO Google Ads pricing (official page): https://dataforseo.com/pricing/keywords-data/google-ads
- DataForSEO minimum deposit and signup credit (third-party): https://costbench.com/software/seo-tools/dataforseo/ and https://meikuio.com/dataforseo-review/
- Keywords Everywhere credit packages (official page): https://keywordseverywhere.com/credits.html
- pytrends archived / Trends API alpha: https://dev.to/esteban_ortega/pytrends-is-dead-heres-how-to-get-google-trends-data-in-2026-1a18 and https://ppc.land/google-opens-alpha-testing-for-new-trends-api-targeting-developers-and-journalists/
- Source probes: run live from a scratch folder on 2026-10-21. These are free public endpoints, called with no keys and a handful of requests each.
