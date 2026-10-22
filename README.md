# keyword-engine

Finds what people actually search for around a topic, using only free sources, and ranks it
by evidence you can check. No made-up scores.

- **`expand`**: one seed → ranked phrases, grouped into article-sized clusters, plus the
  most-viewed Stack Exchange questions.
- **`compare`**: many seeds (counties, species, product ideas) → ranked against each other.

```
python -m keyword_engine expand "reef tank" --niche reef
python -m keyword_engine compare "reef tank" "blenny" "planted tank"
python -m keyword_engine compare --file seeds.txt
```

A first run of `expand` takes about 30–40 seconds (about 300 requests). Re-runs come from the
cache in a few seconds and work offline. Use `--refresh` to fetch again. Python 3.11; needs only
`requests` (`pip install -r requirements.txt`). Tests: `python -m pytest`.

## Sources

| Source | Used for | Notes |
|---|---|---|
| Google autocomplete | phrases + Google's relevance number + shortest typed prefix | `client=chrome` |
| YouTube autocomplete | phrases (video intent) | |
| Bing autocomplete | phrases | the browser suggestion endpoint, not the retired Azure API |
| DuckDuckGo autocomplete | phrases | |
| Amazon autocomplete | phrases (buying intent) | |
| Stack Exchange API | questions with view counts | sites come from the niche file; 300 requests/day without a key |
| Wikipedia page views | topic-level monthly views | exact title (following redirects), or a strict search match; blank when unsure |

None of these need an account. The autocomplete endpoints are unofficial, so any one of them
can change or throttle without notice. A failing source is logged and the run carries on.
Validation needs at least 3 autocomplete engines to have answered.

## Niches

`niches/<name>.json` sets the region, synonyms (other phrasings that count as on topic),
Stack Exchange sites, and skip words (phrases containing them are dropped). `default.json`
fills in anything a niche leaves out. Add a niche by copying `reef.json`.

## Output contract

Other engines (the WaypointLumen SEO engine, GildSpan, the nature data engine) read these
files. Field names are stable within a major version; `run.json` carries `engine_version`.

Each run writes `runs/<YYYY-MM-DD_HHMMSS>-<seed-slug>/`.

### expand

**`phrases.jsonl`**: one JSON object per line, best first.

| Field | Type | Meaning |
|---|---|---|
| `phrase` | str | the search phrase, normalized (lowercase, tidy spacing) |
| `seed`, `niche` | str | what the run was for |
| `engines` | list[str] | which autocomplete engines suggested it |
| `engine_count` | int | `len(engines)`, 1–5 |
| `times_seen` | int | how many different expansion queries surfaced it |
| `google_relevance` | int \| null | Google's own relevance number (higher = stronger) |
| `best_rank` | int | best position in any suggestion list (1 = top) |
| `min_prefix_len` | int \| null | characters you have to type before Google suggests it (top N only) |
| `modifier` | str | the words around the seed (`reef tank lights` → `lights`) |
| `shape` | str | the general pattern (`best {keyword} for {x}`) |
| `intent` | str | `local`, `commercial`, `question` or `informational` (simple word rules) |
| `cluster_id` | str | which cluster in `clusters.json` it belongs to |

Ranking is the tuple `(engine_count, times_seen, google_relevance, -best_rank)`, descending.

`phrases.csv` has the same rows for spreadsheets.

**`clusters.json`**: a list of clusters, best first.
- `id`, `key` (the shared word, e.g. `light`)
- `head`: its best phrase
- `size`, `engine_hits`, `max_engines`
- `intents`: count per intent
- `members`: phrases, best first

One cluster is roughly one article.

**`questions.json`**: Stack Exchange questions, most viewed first.
- `title`, `site`, `views`, `answers`, `answered`, `score`, `link`, `created`
- `on_topic`: whether the title contains the seed

**`run.json`**:
- the run: `run_id`, `mode`, `seed`, `niche`, `started_at`, `finished_at`, `duration_s`, `settings`
- `sources`: per source, how many requests went `attempted` / `live` / `cache` / `stale` / `failed`, plus the first `errors`
- `sources_ok`
- `counts`: raw phrases, phrases, clusters, questions, and dropped phrases by reason
- `wikipedia`: `{title, avg_monthly_views, months}` or null
- `validation`: a list of `{name, ok, detail}` checks, plus `validation_passed`
- `cost_usd`: always 0 for now

**`report.md`**: top clusters, top phrases, top questions, source health. Short enough for a phone.

**Before using a run, check `run.json` → `validation_passed`.** The command also exits with
code 1 when validation fails. The checks are:
- `has_phrases`: at least one phrase came back
- `no_duplicates`: no phrase appears twice
- `all_on_topic`: every phrase contains the seed or a synonym
- `enough_sources`: at least 3 autocomplete engines answered

### compare

**`seeds.json`** / **`seeds.csv`**: seeds, best first.
- `seed`
- `suggest_phrases`: distinct on-topic Google suggestions for the seed and seed + a–z
- `seed_prefix_len`: characters typed before Google suggests the seed
- `wikipedia`
- `avg_rank`

`avg_rank` is the plain average of each signal's rank (1 = best, ties share a rank, a missing
value ranks last). There are no weights.

`run.json` and `report.md` work as in `expand`.

## What the numbers are not

They are **not search volumes**. They show which phrases several engines agree people type,
and in what order. To get real volume, check the top of a report against Google Keyword
Planner (free, gives ranges), or later run a paid batch (DataForSEO, about $0.06 per 1,000
keywords). See `REBUILD-PLAN.md` for the roadmap.
