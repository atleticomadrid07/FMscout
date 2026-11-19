# ARCHITECTURE.md

How the code is organized and which layer may depend on which. The reasons behind these choices are in [DECISIONS.md](../DECISIONS.md) (D-004, D-008).

## Two runtime paths

| Path | Runs | Talks to PitchAPI? | Writes to DB? |
|---|---|---|---|
| **Sync** (`scouting.cli`) | On demand or on a schedule | Yes, and it's the only thing that does | Yes |
| **Serve** (`scouting.api` + `frontend/`) | Always, when hosted | **Never** | No (reads only) |

```
Sync:   PitchAPI ──► adapters/pitchapi ──► raw cache ──► ingest ──► canonical tables ──► metrics ──► aggregates/percentiles
        FM .rtf  ──► adapters/fm ────────────────────────► ingest ──┘

Serve:  frontend ──► api ──► metrics (read) / db (read)
```

## Layers and folders

```
backend/
├── src/scouting/
│   ├── config/            Settings loaded from the environment (.env). Nothing else reads os.environ.
│   ├── domain/            Canonical model: Player, Team, Match, StatLine, Metric, Source.
│   │                      Plain typed objects with no I/O. Every other layer speaks this.
│   ├── adapters/          LAYER 1: source-specific I/O. One subpackage per source.
│   │   ├── pitchapi/        Fetching, raw-response cache, and Pydantic models of the raw JSON.
│   │   └── fm/              FM24 .rtf parser (Phase 6).
│   ├── db/                LAYER 2: SQLAlchemy tables, session handling, upsert helpers.
│   ├── ingest/            LAYER 2: turns adapter output into canonical rows; tracks sync state.
│   ├── metrics/           LAYER 3: metric registry, aggregation, per-90, peer groups, percentiles.
│   ├── mappings/          Data files (YAML), not code: FM header → metric map,
│   │                      FM ↔ PitchAPI comparability flags, position metric templates.
│   ├── api/               LAYER 4: FastAPI routers and response schemas.
│   └── cli/               Typer entry points: sync, rebuild aggregates, import FM.
├── migrations/            Alembic migration scripts.
├── scripts/               One-off scripts (e.g. the Phase 0 spike). Never imported by the package.
└── tests/
    ├── unit/              Pure logic: aggregation maths, percentiles, parsers, header mapping.
    ├── integration/       DB and HTTP boundaries (HTTP mocked at the transport).
    └── fixtures/
        ├── pitchapi/      Trimmed real API responses (see note on terms below).
        └── fm/            Real FM24 .rtf exports (Phase 6).
frontend/                  LAYER 5: created by the Vite scaffolder in Phase 3/4, not by hand.
docs/                      PLAN, ARCHITECTURE, PITCHAPI_NOTES.
data/                      Local SQLite DB and spike output. Gitignored.
```

## Dependency rules

Arrows show "may import":

```
cli ──► ingest, metrics, adapters, db, config, domain
api ──► metrics, db, config, domain
ingest ──► adapters, db, domain
metrics ──► db, domain
adapters ──► domain, config
db ──► domain
domain ──► (nothing in the package)
```

- **No layer imports `api` or `cli`.** They are entry points only.
- **`api` never imports `adapters`.** This enforces the "web app never calls PitchAPI" rule in the code itself, not only by convention.
- **Raw source shapes stop at `adapters`.** Everything after `ingest` only ever sees canonical types.
- **Never parse responses with the SDK's dataclass models.** They default missing counts to `0`, which breaks null ≠ 0. See [PITCHAPI_NOTES.md](PITCHAPI_NOTES.md).

These rules can be enforced automatically later with `import-linter` if they start slipping.

## Why a `src/` layout

Tests run against the *installed* package rather than whatever happens to be on the path. That catches packaging and import mistakes early, and it stops `scripts/` from accidentally importing package internals by relative path.

## Fixtures and terms of use

PitchAPI's documentation does not include a data license or redistribution terms. Until that's confirmed:
- Keep fixtures trimmed to the minimum a test needs.
- Keep the repo private, or move `tests/fixtures/pitchapi/` out of any public copy.
