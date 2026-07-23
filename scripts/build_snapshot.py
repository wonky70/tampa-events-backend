#!/usr/bin/env python3
"""Regenerate the cached events snapshot served by /api/events.

Runs the same scrapers the live site uses, once, and writes the result to
``snapshot/latest.json``. A scheduled job (GitHub Actions) reruns this so the
public API can serve the snapshot instantly instead of scraping on every cold
start.

Usage::

    python scripts/build_snapshot.py            # today + 8 days, no leads
    python scripts/build_snapshot.py --days 14  # wider window
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from free_events.web import (  # noqa: E402
    SNAPSHOT_DEFAULT_DAYS,
    SNAPSHOT_PATH,
    build_snapshot,
    load_snapshot,
    write_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Tampa Bay Free Events snapshot")
    parser.add_argument("--start", default=None, help="Start date: today/tomorrow/ISO (default: today)")
    parser.add_argument("--days", type=int, default=SNAPSHOT_DEFAULT_DAYS, help="Days to cover")
    parser.add_argument(
        "--leads",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include unverified search/social leads (shown only when the 'Leads' toggle is on)",
    )
    parser.add_argument("--out", default=str(SNAPSHOT_PATH), help="Output path")
    parser.add_argument(
        "--min-events",
        type=int,
        default=1,
        help="Refuse to overwrite an existing good snapshot with fewer events than this",
    )
    args = parser.parse_args()

    snapshot = build_snapshot(start=args.start, num_days=args.days, include_search=args.leads)
    count = len(snapshot.get("events", []))
    print(
        f"Built snapshot: {count} events across {len(snapshot.get('days', []))} days "
        f"({snapshot.get('start')} → {snapshot.get('end')})"
    )

    if count < args.min_events:
        existing = load_snapshot(args.out)
        if existing and len(existing.get("events", [])) >= args.min_events:
            print(
                f"Refusing to overwrite {args.out}: new snapshot has {count} events; "
                f"kept existing snapshot with {len(existing.get('events', []))}.",
                file=sys.stderr,
            )
            return 1
        print(f"Warning: snapshot has only {count} events.", file=sys.stderr)

    target = write_snapshot(snapshot, args.out)
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
