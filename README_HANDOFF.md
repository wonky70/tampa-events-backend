# Tampa Bay Free Events — backend handoff (no UI)

This is a **working backend only**. It scrapes and verifies free / no-cover
events across the Tampa Bay area and serves them as JSON. **There is no user
interface in this repo — that was removed on purpose.** Your job is to build the
front end from scratch.

If you deploy this as-is and open the home page, you'll see a plain
"Backend only — no UI here" placeholder. That's expected.

---

## The one endpoint you build against

```
GET /api/events?date=YYYY-MM-DD&days=1&search=0
```

Query params:
- `date` — ISO date, or `today` / `tomorrow`. Defaults to tomorrow.
- `days` — how many days forward to include, `1`–`31` (e.g. `1`, `3`, `7`).
- `search` — `0` = only confirmed-free events (**use this**). `1` = also include
  unverified "leads." Keep it `0` in production.
- `offline` — `1` to force the cached snapshot only (no live scrape).

Returns JSON:

```json
{
  "dates": ["2026-07-24"],
  "generated_at": "2026-07-23T12:00:00-04:00",
  "events": [ { ...event... } ],
  "statuses": [ { "source": "...", "ok": true, "message": "", "count": 12, "url": "" } ],
  "sponsors": [ { ...paid ad placements... } ],
  "play_spots": { ... },
  "markdown": "# ...text report..."
}
```

Each **event** object:

| field | meaning |
|---|---|
| `id` | stable unique id |
| `title` | event name |
| `start` / `end` | ISO datetime (`end` may be null) |
| `all_day` | boolean |
| `description` | text |
| `url` | **source link — every event card must link here** |
| `location` | venue / address text |
| `city` | town name |
| `source` | which site it came from |
| `source_kind` | `"event"` (confirmed) / `"library"` / `"lead"` (unverified) |
| `category` | category label |
| `cost_note` | e.g. "Free / no-cover unless noted by source" |
| `needs_review` | boolean — **if true, do NOT present it as confirmed free** |
| `latitude` / `longitude` | may be null |

---

## HARD RULES (do not break these)

1. **Never show a paid or sold-out event as free.** If `needs_review` is `true`,
   don't present it as confirmed-free. Keep `search=0` so only verified events load.
2. **Don't over-filter.** Show every confirmed-free event the API returns — don't
   let real free events disappear.
3. **Instant load.** The API already serves a cached snapshot in ~10ms. Just fetch
   it; never scrape on page load.
4. **Plain language, all ages.** No jargon. Every control self-explanatory. Clean,
   not overwhelming. Usable by old, young, and average visitors.
5. **Every event links out** to its `url`.
6. **Advertising:** provide simple, clearly-priced ad placements for local
   businesses using the `sponsors` data, plus a page explaining ad options.

**Design the entire interface yourself. There is nothing here to copy.**

---

## Run it locally

Python 3, standard library only — no `pip install` needed.

```
git clone https://github.com/wonky70/tampa-events-backend.git
cd tampa-events-backend
python3 -m unittest discover -s tests        # sanity check
python3 -m free_events                        # http://127.0.0.1:8765 (placeholder + working /api/events)
```

## Deploy (Vercel + Cloudflare)

- `vercel.json` is included; the whole app runs from `api/index.py` (WSGI).
- `.github/workflows/refresh-snapshot.yml` + `scripts/build_snapshot.py` rebuild
  `snapshot/latest.json` on a schedule so the feed refreshes daily automatically —
  no terminal needed to keep it live.
- Target domain: **tampabayfreeevents.com**.

## What lives where

- `free_events/sources.py` — the scrapers + the free/paid/sold-out verification.
- `free_events/web.py` — the data engine (`serve_payload`, snapshot logic). The two
  UI functions here are stubs on purpose.
- `api/` — the WSGI app and routing (`/api/events`, `/advertise`, `/robots.txt`, …).
- `config/` — `sponsors.json`, `play_spots.json`, recurring/manual sources.
- `snapshot/latest.json` — the prebuilt cached feed the API serves instantly.
