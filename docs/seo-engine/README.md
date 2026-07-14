# SEO Content Engine — prep docs (parked here temporarily)

**This is not part of keyword-engine.** It's the downstream project that
will *consume* keyword-engine's ranked output once this engine is
finished — a separate system that turns a batch of ranked keywords into
published-ready content via a human-checkpointed queue, for
waypointlumen.com.

## Why it's sitting in this repo

No repo exists for the SEO engine yet. It was supposed to get its own
(`waypointlumen-seo-engine`), but repo creation isn't available from this
GitHub App integration (403: "Resource not accessible by integration").
These docs got parked here in the meantime so they're not stranded as
local-only files.

## What to do with this folder when you start the build

1. Create a new repo yourself (github.com/new) — suggested name
   `waypointlumen-seo-engine`, private.
2. Move `job-packet.md` and `open-questions.md` into it (this whole
   `docs/seo-engine/` folder can just become that repo's root, or its own
   `docs/` — your call).
3. Delete this folder from `keyword-engine` once moved, so this repo goes
   back to being just the keyword engine.
4. Fill in `open-questions.md` as you go — several of its answers depend
   on keyword-engine's actual output schema (`pattern_library.json`
   fields), so some of it may get easier to answer once keyword-engine
   itself is further along.

## Files here

- `job-packet.md` — the consolidated spec/scope for the SEO engine.
- `open-questions.md` — everything still undecided, as blank Q&A pairs.
