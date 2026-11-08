"""Clip channel: Shorts made from authorized source videos plus original commentary.

Agents (one module each):
  discovery.py   1  find creators/source videos (NASA library, YouTube metadata, creator inbox) and rank them
  rights.py      2  mandatory rights gate: nothing is downloaded, edited or published without a cleared record
  ingest.py         fetch media for cleared sources only, from allowlisted pathways (never YouTube/social sites)
  segments.py    3  transcript → scored, non-duplicate candidate moments with timestamps and reasons
  editorial.py   4  original intro + explanation/payoff narration (the transformation), faithful to the source
  edit.py        5  9:16 render: reframed clip, narration, captions, on-screen attribution, loudness
  compliance.py  6  independent review: rights, permitted use, transformation share, technical, metadata
  package.py     7  title/description/attribution/hashtags, unique across the batch
  (publishing    8  reuses pipeline/orchestrator.py: identity check, slots, idempotent upload, verification)
  (analytics     9  reuses pipeline/analytics.py on this channel's own files, plus per-source breakdowns)
  orchestrator.py 10 daily run, queues, gates, recovery
"""
