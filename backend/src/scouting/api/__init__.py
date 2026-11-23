"""Layer 4: the read-only HTTP API over scouting.db (Phase 3, design in claude/PHASE3_API.md).

It never calls PitchAPI and never loads the API key; it opens scouting.db read-only.
Allowed imports: metrics, db, domain, config (never adapters, ingest or cli).
"""
