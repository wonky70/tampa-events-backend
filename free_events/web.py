from __future__ import annotations

import html
import json
import os
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .http import Fetcher
from .models import Event, SourceStatus
from .render import render_markdown
from .sources import CollectOptions, collect_all
from .utils import LOCAL_TZ, date_range, dedupe_events, parse_date_token, write_json


DEFAULT_PORT = 8765

# Cached snapshot of a rolling window of events. A scheduled job (see
# scripts/build_snapshot.py + .github/workflows/refresh-snapshot.yml) rebuilds
# this so /api/events can serve instantly instead of scraping on every cold
# start. Lives at a repo path that ships with the deploy (not .vercelignore'd).
SNAPSHOT_PATH = Path(os.environ.get("FREE_EVENTS_SNAPSHOT", "snapshot/latest.json"))
SNAPSHOT_DEFAULT_DAYS = 9


def run(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Tampa Bay Free Events local app: http://{host}:{port}")
    server.serve_forever()


class Handler(BaseHTTPRequestHandler):
    server_version = "FreeEventsLocal/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(render_home(parse_qs(parsed.query)))
            return
        if parsed.path == "/advertise":
            self._send_html(render_advertise())
            return
        if parsed.path == "/api/events":
            try:
                payload = serve_payload(parse_qs(parsed.query))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json(payload)
            return
        if parsed.path == "/report.md":
            try:
                payload = serve_payload(parse_qs(parsed.query))
            except Exception as exc:
                self._send_text(str(exc), status=500, content_type="text/plain; charset=utf-8")
                return
            self._send_text(payload["markdown"], content_type="text/markdown; charset=utf-8")
            return
        self._send_text("Not found", status=404)

    def log_message(self, format: str, *args) -> None:
        return

    def _send_html(self, body: str, status: int = 200) -> None:
        self._send_text(body, status, "text/html; charset=utf-8")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def collect_payload(
    params: dict[str, list[str]],
    cache_dir: Path | None = None,
    write_reports: bool = True,
) -> dict:
    days = _resolve_days(params)
    include_search = _param(params, "search", "1") != "0"
    offline = _param(params, "offline", "0") == "1"
    fetcher = Fetcher(cache_dir or Path(".cache/free-events"), offline=offline)
    result = collect_all(days, fetcher, CollectOptions(include_search_leads=include_search))
    events = dedupe_events(result.events)
    markdown = render_markdown(events, result.statuses, days)
    generated = {
        "dates": [day.isoformat() for day in days],
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "events": [event.asdict() for event in events],
        "statuses": [status.asdict() for status in result.statuses],
        "sponsors": _load_sponsors(days),
        "play_spots": _load_play_spots(),
        "markdown": markdown,
    }
    if write_reports:
        write_json(Path("data/reports/local-latest.json"), {k: v for k, v in generated.items() if k != "markdown"})
        Path("data/reports").mkdir(parents=True, exist_ok=True)
        Path("data/reports/local-latest.md").write_text(markdown, encoding="utf-8")
    return generated


def build_snapshot(
    start: str | None = None,
    num_days: int = SNAPSHOT_DEFAULT_DAYS,
    include_search: bool = False,
    cache_dir: Path | None = None,
) -> dict:
    """Scrape a rolling window once so the public API can serve it instantly.

    Leads (unverified search/social results) are excluded by default so the
    cached feed only ever carries confirmed free listings.
    """
    start_date = parse_date_token(start) if start else datetime.now(LOCAL_TZ).date()
    num_days = max(1, min(31, int(num_days)))
    days = date_range(start_date, start_date + timedelta(days=num_days - 1))
    fetcher = Fetcher(cache_dir or Path(".cache/free-events"), offline=False)
    result = collect_all(days, fetcher, CollectOptions(include_search_leads=include_search))
    events = dedupe_events(result.events)
    return {
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "start": start_date.isoformat(),
        "end": days[-1].isoformat(),
        "days": [day.isoformat() for day in days],
        "include_search": include_search,
        "events": [event.asdict() for event in events],
        "statuses": [status.asdict() for status in result.statuses],
    }


def write_snapshot(snapshot: dict, path: Path | str | None = None) -> Path:
    target = Path(path or SNAPSHOT_PATH)
    write_json(target, snapshot)
    return target


def load_snapshot(path: Path | str | None = None) -> dict | None:
    target = Path(path or SNAPSHOT_PATH)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("days"), list):
        return None
    return data


def _payload_from_snapshot(params: dict[str, list[str]], snapshot: dict) -> dict | None:
    """Build the API payload from the cached snapshot if it covers the request.

    Honours the ``search`` (leads) toggle exactly as a live scrape would: search
    leads are tagged ``source_kind == "lead"``, so dropping them reproduces
    ``CollectOptions(include_search_leads=False)`` without touching confirmed
    listings. The cache stays transparent — same output as live, just faster.
    """
    days = _resolve_days(params)
    want = [day.isoformat() for day in days]
    have = set(snapshot.get("days") or [])
    if not have or any(iso not in have for iso in want):
        return None
    include_search = _param(params, "search", "1") != "0"
    want_set = set(want)
    events: list[Event] = []
    for item in snapshot.get("events") or []:
        try:
            event = Event(**item)
        except TypeError:
            continue
        if not include_search and event.source_kind == "lead":
            continue
        day = event.date
        if day and day.isoformat() in want_set:
            events.append(event)
    events = dedupe_events(events)
    statuses: list[SourceStatus] = []
    for item in snapshot.get("statuses") or []:
        try:
            status = SourceStatus(**item)
        except TypeError:
            continue
        if not include_search and status.source == "Web search leads":
            continue
        statuses.append(status)
    if not include_search:
        statuses.append(SourceStatus("Web search leads", True, "Skipped by option", 0))
    markdown = render_markdown(events, statuses, days)
    return {
        "dates": want,
        "generated_at": snapshot.get("generated_at") or datetime.now(LOCAL_TZ).isoformat(),
        "events": [event.asdict() for event in events],
        "statuses": [status.asdict() for status in statuses],
        "sponsors": _load_sponsors(days),
        "play_spots": _load_play_spots(),
        "markdown": markdown,
    }


def serve_payload(
    params: dict[str, list[str]],
    cache_dir: Path | None = None,
    snapshot_path: Path | str | None = None,
) -> dict:
    """Serve the cached snapshot instantly when it covers the request.

    Falls back to a live scrape only for date ranges the snapshot does not
    cover (e.g. a 30-day view) or when no snapshot is present.
    """
    snapshot = load_snapshot(snapshot_path)
    if snapshot:
        payload = _payload_from_snapshot(params, snapshot)
        if payload is not None:
            return payload
    return collect_payload(params, cache_dir=cache_dir, write_reports=False)


def _load_json_config(name: str, default):
    path = Path("config") / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def _load_sponsors(days: list[date]) -> list[dict]:
    """Return active sponsor placements, filtered by optional date/city/lane targeting."""
    raw = _load_json_config("sponsors.json", [])
    if not isinstance(raw, list):
        return []
    today = date.today()
    day_isos = {day.isoformat() for day in days}
    sponsors: list[dict] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        if item.get("active") is False:
            continue
        starts = item.get("starts")
        ends = item.get("ends")
        if starts and today.isoformat() < str(starts):
            continue
        if ends and today.isoformat() > str(ends):
            continue
        only_dates = item.get("only_dates")
        if only_dates and not (set(only_dates) & day_isos):
            continue
        sponsors.append(
            {
                "id": str(item.get("id") or item.get("name")),
                "name": item.get("name"),
                "tagline": item.get("tagline", ""),
                "url": item.get("url", "/advertise"),
                "cta": item.get("cta", "Learn more"),
                "tier": item.get("tier", "native"),
                "sponsored_label": item.get("sponsored_label", "Sponsored"),
                "image": item.get("image", ""),
                "cities": item.get("cities", []),
                "lanes": item.get("lanes", []),
            }
        )
    return sponsors


def _load_play_spots() -> dict:
    data = _load_json_config("play_spots.json", {})
    if not isinstance(data, dict):
        return {}
    return data


def _resolve_days(params: dict[str, list[str]]) -> list[date]:
    start = parse_date_token(_param(params, "date", "tomorrow"))
    if "end" in params:
        end = parse_date_token(_param(params, "end", start.isoformat()))
    else:
        count = max(1, min(31, int(_param(params, "days", "1"))))
        end = start + timedelta(days=count - 1)
    return date_range(start, end)


def _param(params: dict[str, list[str]], name: str, default: str) -> str:
    values = params.get(name)
    return values[0] if values else default


def _is_public_host() -> bool:
    return bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_URL"))


def _contact_email() -> str:
    return os.environ.get("SITE_CONTACT_EMAIL", "ads@tampabayfreeevents.com")


# ---------------------------------------------------------------------------
# UI INTENTIONALLY REMOVED.
# This bundle ships the working event engine + JSON API on purpose, with NO
# interface to copy. Build the front end from scratch against GET /api/events.
# See README_HANDOFF.md for the full API contract and the hard rules.
# ---------------------------------------------------------------------------


def _stub_page() -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Tampa Bay Free Events — backend only</title></head><body>'
        '<main style="font-family:system-ui,-apple-system,sans-serif;max-width:640px;'
        'margin:12vh auto;padding:0 20px;line-height:1.55;color:#17333a">'
        '<h1>Backend only — no UI here</h1>'
        '<p>This bundle ships the working event engine and JSON API on purpose, '
        'with <strong>no interface</strong>. Build the UI from scratch.</p>'
        '<p>Live data: <a href="/api/events?days=1&amp;search=0">/api/events</a> '
        '(supports <code>?date=YYYY-MM-DD&amp;days=1&amp;search=0</code>).</p>'
        '<p>See <code>README_HANDOFF.md</code> for the API contract and the rules.</p>'
        '</main></body></html>'
    )


def render_home(params: dict[str, list[str]]) -> str:
    return _stub_page()


def render_advertise() -> str:
    return _stub_page()


def days_options(selected: str) -> str:  # kept only so old references resolve
    return ""

if __name__ == "__main__":
    run()
