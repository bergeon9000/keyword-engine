# WaypointLumen SEO Content Engine — Job Packet

**Status:** Prep only. No code exists yet. This is a spec to hand to Claude
Code when the build actually starts. Not part of the Department of
Autonomous Production — this is a primary project (requires steering),
consumed downstream of the (separate, in-progress) Keyword Engine.

---

## 1. System boundary

Two separate systems, in sequence:

1. **Keyword Engine** (separate project, in progress) — scrapes free
   sources (Google Autocomplete, Reddit, YouTube comments, niche forums),
   discovers patterns, **scores/ranks** them (intent, locality, job-seeker
   signal axes), and persists everything to `pattern_library.json`. Owns
   all scraping and all scoring. Not this packet's concern.
2. **SEO Content Engine** (this packet) — takes the Keyword Engine's
   ranked output and turns a batch of it into published-ready content
   through a human-checkpointed queue. Does **not** scrape. Does **not**
   score or rank. Purely a consumer of upstream output → producer of
   content.

Destination: **waypointlumen.com**. No CMS/publish automation at this
tier — output is a local folder, Josh handles publishing manually.

This engine is standalone from the future book tier / Success Wizard.
The only intentionally shared thing is voice-profile *data* (JSON), not
logic — no shared runner code until a second real project proves the
abstraction is worth extracting.

## 2. Inputs

- The Keyword Engine's ranked keyword list, backed by `pattern_library.json`.
- Exact field names/types (rank score, confidence score, pattern metadata)
  are owned by the Keyword Engine and not yet finalized — see Open
  Questions doc, this is a hard dependency before the intake stage can be
  coded for real.

## 3. Batch selection (upstream of the queue)

The full ranked list is not dumped into `content_queue.json` at once — it
keeps growing/re-ranking as the Keyword Engine runs. Batch selection runs
before anything touches the queue:

1. **Threshold filter** — drop anything below a minimum rank/confidence
   score.
2. **Topic clustering** — group remaining candidates by topic similarity
   (start with shared terms/n-gram overlap) so overlapping keywords are
   caught before any API spend, not later by the redundancy check.
3. **Cluster selection** — pick the top cluster(s) by aggregate rank.
4. **Dedup against published catalog** — coarse pre-filter against the
   output folder / published list, so batches trend toward filling gaps.
5. **Batch size cap** — pull a fixed number of items (range discussed:
   10-20; exact number TBD) into `content_queue.json` as `pending`. Next
   batch isn't pulled until this one is mostly worked through.

## 4. Pipeline stages

Each keyword is one item in `content_queue.json`, moving through:

```
pending
  -> brief_generated
  -> brief_approved
  -> redundancy_checked
  -> drafted
  -> fact_contradiction_checked
  -> qa_checked
  -> approved
  -> exported
```

Failed items move to `failed:<stage>` with reason + partial output
preserved. No silent auto-retry — manual requeue only.

1. **Intake** — pull next `pending` item (highest rank first), load
   keyword + pattern data from `pattern_library.json`.
2. **Brief Generation** (Claude API call) — intent, angle, target
   sections, internal link candidates. Checkpoint: review/approve or
   reject-and-regenerate.
3. **Redundancy Check** (Claude API call) — compare brief against
   titles/topics already published. Flag overlap/cannibalization.
   Checkpoint: proceed, adjust angle, or kill.
4. **Draft Generation** (Claude API call, multi-pass) — most
   token-heavy stage, tracked closely (see Cost Tracking).
5. **Fact & Contradiction Check** (Claude API call) — check draft
   against own published back-catalog (and source data, if supplied)
   for factual conflicts / restated claims. Checkpoint before QA.
6. **QA / Coverage Check** (Claude API call) — keyword coverage,
   structure, internal links, against the brief. Prose-quality editing
   stays manual for now.
7. **Approval** — final personal read-through and sign-off.
8. **Export** — finished piece written to a local output folder. Manual
   handling from there (including getting it onto waypointlumen.com).

## 5. Failure handling

Priority: minimize lost usable data, minimize wasted tokens.

On any stage failure: log error + stage + full input sent to the API
call, save any partial output even if incomplete, set state to
`failed:<stage>` with reason. Never silently revert to `pending`. No
automatic retry — manual requeue only, at least at MVP.

## 6. Cost tracking

Every API call logs tokens in/out, tagged by item + stage. Gives real
per-run cost data after the first 5–10 items, and shows which stage is
actually expensive (usually multi-pass drafting) so optimization targets
the right place.

## 7. Prompt versioning

Every generation-stage call tags its output with a prompt version
identifier, stored on the queue item record — traces quality drift back
to a specific prompt revision without guessing.

## 8. Voice profiles

One JSON file per pen name. Structured fields: tone anchors, banned
constructions (e.g. no em dashes, no AI prose fingerprints),
sentence-length tendencies, POV/narrative stance, domain-specific
do/don't notes. Injected selectively into whichever prompt needs it.
Shared data resource across this engine and the future book tier — the
one deliberate point of overlap.

## 9. Traffic / performance tracking (future, deferred)

Success/quality criteria for *keyword selection* live upstream in the
Keyword Engine — not this engine's job. But once pages are actually live
on waypointlumen.com, this engine will likely need some kind of
traffic/performance feedback loop (e.g. Search Console / GA4 data pulled
back in) to judge whether exported content is actually working. Not
designed now — flagged so the queue schema doesn't need a rework later.
The queue schema should reserve a couple of open fields (e.g. `status`,
`last_updated`) for this, same reasoning as the original roadmap's
Success Wizard placeholder.

## 10. Explicit non-goals (for now)

- No shared runner code with book tier or Success Wizard yet.
- No CMS/publish automation — output folder only.
- No traffic/analytics integration yet (see §9 — deferred, not designed).
- No auto-retry on failure.

## 11. Environment

- Python: whatever version is already on the build machine at build
  time — don't pin a minor version speculatively.
- Other dependencies (API client libraries, etc.) TBD at build time,
  driven by whatever the Keyword Engine's output format actually
  requires to consume.
- No repo exists yet for this project.

---

See the companion **Open Questions** doc for everything that needs an
answer before/during the build.
