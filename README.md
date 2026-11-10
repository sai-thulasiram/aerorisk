# AeroRisk Explorer — starter kit

Flight number + date → operating aircraft → cabin configuration (with honest confidence tier) → seat map + per-seat layout facts. Pure Python 3.11+ stdlib; `pytest` for tests. Also generates the public website (see **DEPLOY.md**).

**No seat risk scores.** Seat facts are geometry only: position, side, seats to aisle, pitch, exit-row membership, rows to nearest exits. `historical_seat_risk.estimable` is always `false` until an occupant-level evidence layer exists.

## Coverage (v0.1)

| Config | Operator | Type | Seats | Status |
|---|---|---|---|---|
| `FR-B738-189Y` | Ryanair (+ group) | B738 | 189 | draft |
| `FR-B38M-197Y` | Ryanair (+ group) | B38M (737 MAX 8-200) | 197 | draft |
| `6E-A20N-186Y` / `6E-A20N-180Y` | IndiGo | A20N | 186 / 180 | draft |
| `6E-A21N-232Y` | IndiGo | A21N | 232 (~64% of fleet) | draft |
| `6E-A320-180Y` | IndiGo | A320 | 180 | draft |
| `AI-A20N-164` | Air India | A20N | 8J / 24W / 132Y | draft |
| `IX-B38M-189Y` | Air India Express | B38M | 189 | section map (rows + exits) |
| `U2-A20N-186Y`, `U2-A320-186Y`, `U2-A21N-235Y` | easyJet (+ EJU, EZS) | A20N, A320, A21N | 186 / 186 / 235 | draft |
| `W6-A21N-239Y` | Wizz Air (+ UK, Abu Dhabi, Malta) | A21N | 239 | draft |

Multi-cabin layouts use `cabins` (per row range: name, letters, aisles, pitch). `detail: "sections_only"` marks layouts with only rows and exits verified. `other_versions_unquantified` flags airlines with further versions of unknown share.

These are hand-authored from public secondary sources (listed in each JSON's `provenance`), **not licensed LOPA data**. Known source conflicts are recorded in `provenance.conflicts`. Verify each against the airline's live seat map before calling anything "verified".

## Research status (9 Nov 2026)

Each config records `provenance.corroboration` (which sources back each fact), `conflicts`, and `primary_source_check`.

| Fact | Ryanair 737-800 | Ryanair MAX 8-200 | IndiGo A320neo 186/180 |
|---|---|---|---|
| Row range / skipped rows | 2 sources | 1 source (sums to 197) | **IndiGo's own fees page** + AeroLOPA + 1 |
| Exit rows | 3 sources (1 dissenting outlier) | 2 sources | **IndiGo's own fleet page** + 2 |
| Missing / partial seats | 2 sources | 1 source | n/a (full rows) |
| Windowless seats | 2 sources; 11A disputed | 2 sources | 2 sources (186 only) |
| Pitch values | 1 source | 1 source | 1 source; row 1 left unknown |
| Fleet split (variant prior) | n/a | n/a | AeroLOPA 151/21, seatcompare 171/22 → ~88/12 |
| First-party confirmation | none (Ryanair help page unreachable) | none | partial |

AeroDataBox endpoint, headers and response fields were checked against the OpenAPI-generated client (github.com/lucasbstn/aerodatabox, July 2024) and the apis.io listing — not yet exercised live. OpenSky aircraft metadata: open data, academic/non-commercial use with attribution per secondary sources; read the terms at opensky-network.org before commercial use.

## Quick start

```bash
python -m pytest -q
python -m aerorisk configs
python -m aerorisk seat   --config FR-B738-189Y --seat 21C
python -m aerorisk render --config 6E-A20N-186Y --out out/indigo.html     # interactive inspector
python -m aerorisk render --config FR-B738-189Y --seat 16A --out out/fr.svg

# Offline flight lookup with a synthetic fixture
python -m aerorisk lookup --flight 6E9999 --date 2026-11-20 \
  --fixture fixtures/adb_sample_indigo.json --html out/flight.html

# Live lookup (AeroDataBox via RapidAPI)
export AERODATABOX_RAPIDAPI_KEY=...
python -m aerorisk lookup --flight 6E2134 --date 2026-11-20 --html out/flight.html

# Tail -> type fallback and fleet census from the OpenSky aircraft database (download it yourself)
python -m aerorisk fleet  --opensky aircraftDatabase.csv --operators RYR,IGO
python -m aerorisk lookup --flight FR1234 --date 2026-11-20 --opensky aircraftDatabase.csv
```

## Website

```bash
SITE_URL=https://yourdomain.com UMAMI_WEBSITE_ID=... python -m aerorisk site --out site
npx wrangler dev        # preview locally with production headers
```

Builds static HTML seat-map pages, Markdown twins, a JSON API, `robots.txt`, `sitemap.xml`, `llms.txt`, `llms-full.txt`, `openapi.json`, `/.well-known/api-catalog`, JSON-LD, and `_headers` (CSP, content types). Deployment, analytics and indexing steps are in `DEPLOY.md`.

## Research backlog (not published: sources conflict)

- **IndiGo ATR 72-600**: 76 vs 77 vs 78 seats across sources; exits not documented.
- **Akasa 737 MAX 8 (189)**: row 13 and exit rows unclear; one source shows a full row 1, another implies a partial row.
- **IndiGoStretch A321neo (12C + 208Y)**: row counts don't reconcile across sources.
- **AirAsia A320**: 180 vs 186-seat split at AirAsia Malaysia unknown.
- **Air India Express 737-8 176 (8 business)**: single source; 2 aircraft.
- **Air India A320neo 180Y, A321neo**: second Air India A320neo version undocumented.

## Layout

```
configs/*.json          cabin specs (rows, skipped rows, missing seats, exits, pitch, flags, provenance)
data/tail_overrides.csv registration -> config_id, hand-verified. Only source of an "exact" match.
aerorisk/layout.py      spec -> seats with geometry + facts; seat_facts()
aerorisk/render.py      SVG and interactive HTML inspector
aerorisk/flights.py     flight number parsing, AeroDataBox client, model-name -> ICAO type
aerorisk/fleet.py       OpenSky aircraftDatabase.csv loader, tail overrides
aerorisk/matcher.py     exact / likely / generic / unavailable tiers, fleet-share priors
aerorisk/preferences.py comfort ranker + plugin hook; rankers cannot emit safety/risk wording
aerorisk/site.py        static site + agent files + analytics events
static/og.png           social preview (regenerate: python tools/make_og.py)
wrangler.jsonc          Cloudflare Workers static-assets config
.github/workflows/      test -> build -> deploy -> IndexNow
```

## Match tiers

- **exact** — registration present in `data/tail_overrides.csv`.
- **likely** — operator + type known; candidates ranked by `fleet_share_prior`.
- **generic** — type known, no operator layout; requires a config with `"generic": true` (none authored yet).
- **unavailable** — type unknown or not covered.

## Things to verify before trusting this

1. **AeroDataBox** — field names checked against the generated client, but no live call has been made. Registration is often missing for future flights.
2. **OpenSky licence** — free for research/non-commercial use; check terms before commercial use. Column names vary between dumps; `fleet.py` handles common aliases.
3. **Ryanair** — no first-party check yet. Exit rows 16–17 are 3-vs-1 across sources; the MAX 8-200 front rows rest on one source.
4. **Fleet-share priors** — IndiGo 186Y/180Y split from AeroLOPA and seatcompare.ai (agree at ~88/12).
5. **Exit geometry** is approximate (cumulative pitch). Fine for "rows between"; not for evacuation modelling.

## Adding a configuration

Copy a JSON, edit `rows`, `missing_seats`, `exits`, `pitch_in`, `seat_flags`, `provenance`. `build_cabin` fails loudly if the generated seat count disagrees with `total_seats`. Mark pitch you can't source as `unknown_rows` rather than guessing.

## Next

- A tail-verification workflow (registration seen on a live seat map → `tail_overrides.csv`).
- Evidence layer: NTSB/AAIB report ingestion with human review (LangGraph, interrupt before write).
