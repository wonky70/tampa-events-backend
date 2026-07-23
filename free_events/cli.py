from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

from .http import Fetcher
from .render import render_markdown
from .sources import CollectOptions, collect_all
from .utils import LOCAL_TZ, date_range, dedupe_events, parse_date_token, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Tampa Bay free events.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--date", help="Single date: YYYY-MM-DD, today, or tomorrow")
    group.add_argument("--year", type=int, help="Collect every day in a year")
    parser.add_argument("--start", help="Start date: YYYY-MM-DD, today, or tomorrow")
    parser.add_argument("--end", help="End date: YYYY-MM-DD, today, or tomorrow")
    parser.add_argument("--days", type=int, help="Number of days from --start")
    parser.add_argument("--output", help="Markdown output path")
    parser.add_argument("--json-output", help="JSON output path")
    parser.add_argument("--cache-dir", default=".cache/free-events", help="Raw response cache directory")
    parser.add_argument("--offline", action="store_true", help="Use cached responses only")
    parser.add_argument("--no-search-leads", action="store_true", help="Skip Bing/search/social lead discovery")
    parser.add_argument("--include-search-leads", action="store_true", help="Force search/social lead discovery")
    args = parser.parse_args(argv)

    today = date.today()
    days = _resolve_days(args, today)
    include_search = not args.no_search_leads
    if len(days) > 31 and not args.include_search_leads:
        include_search = False
    elif args.include_search_leads:
        include_search = True

    fetcher = Fetcher(Path(args.cache_dir), offline=args.offline)
    result = collect_all(days, fetcher, CollectOptions(include_search_leads=include_search))
    events = dedupe_events(result.events)
    markdown = render_markdown(events, result.statuses, days)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        print(markdown)

    if args.json_output:
        write_json(
            Path(args.json_output),
            {
                "dates": [day.isoformat() for day in days],
                "events": [event.asdict() for event in events],
                "statuses": [status.asdict() for status in result.statuses],
            },
        )
        print(f"Wrote {args.json_output}")
    return 0


def _resolve_days(args: argparse.Namespace, today: date) -> list[date]:
    if args.date:
        single = parse_date_token(args.date, today)
        return [single]
    if args.year:
        return date_range(date(args.year, 1, 1), date(args.year, 12, 31))
    if args.start:
        start = parse_date_token(args.start, today)
    else:
        start = today
    if args.end:
        end = parse_date_token(args.end, today)
    elif args.days:
        end = start + timedelta(days=args.days - 1)
    else:
        end = start
    return date_range(start, end)

