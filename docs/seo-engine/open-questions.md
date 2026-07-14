# WaypointLumen SEO Content Engine — Open Questions

Answer whenever you get to the build. Leave blank ones blank — Claude
Code will ask again at build time if something's still empty and it
actually blocks progress.

## Interface with the Keyword Engine
- **Q:** Exact field names/types the Keyword Engine will output per
  keyword (rank score, confidence score, any other metadata this engine
  needs to read from `pattern_library.json`)?
- **A:**

## Queue schema
- **Q:** Exact `content_queue.json` item schema — fields for cost log,
  prompt version, redundancy flag, fact-check flag, failure reason,
  `status`/`last_updated` placeholders?
- **A:**

## Model choice per stage
- **Q:** Which Claude model per pipeline stage? (e.g. cheaper model for
  brief/redundancy/QA checks, stronger model for the token-heavy draft
  stage — or same model throughout for simplicity at MVP?)
- **A:**

## Batch size
- **Q:** Exact batch size cap to pull into the queue at once (range
  discussed was 10-20)?
- **A:**

## Budget guardrail
- **Q:** Even before real cost numbers exist, is there an initial
  per-run token/cost ceiling you want enforced from day one (a stop-gap
  number), or is it fine to run the first batch uncapped and set the
  real cap after?
- **A:**

## Draft multi-pass criteria
- **Q:** How many draft passes max, and what decides "needs another
  pass" vs. "good enough, move to fact-check"?
- **A:**

## Redundancy / fact-check comparison depth
- **Q:** Confirm starting cheap (compare against titles/summaries only,
  not full published text) — and what's the trigger to expand to full
  text later if it's not catching real overlap?
- **A:**

## Output format
- **Q:** What format should exported content land in (plain markdown?
  markdown with frontmatter? HTML?) — driven by whatever
  waypointlumen.com actually expects to ingest for manual publishing?
- **A:**

## Output folder / published catalog location
- **Q:** Where does the local output folder live, and what does "the
  published catalog to dedup/redundancy-check against" actually look
  like on disk (a folder of finished files? a separate index file)?
- **A:**

## Voice profiles
- **Q:** Concrete JSON shape for a voice profile, and which pen name(s)
  exist at build time to seed the first profile?
- **A:**

## Invocation / runner
- **Q:** How does this actually get run — manual CLI command per batch,
  a scheduled job, or something else? (No DAP-style `runners/*.bat`
  exists for this project yet.)
- **A:**

## Repo / location
- **Q:** Where does this project's code live once it starts (new
  separate repo? same repo as the Keyword Engine)?
- **A:**

## Traffic/performance tracking (future)
- **Q:** When this becomes relevant — Search Console, GA4, or something
  else — and is it this engine's job to pull that data back in, or a
  separate future tool that just reads the same output folder?
- **A:**
