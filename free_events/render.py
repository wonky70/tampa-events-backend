from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from .models import Event, SourceStatus
from .utils import dedupe_events


def render_markdown(events: list[Event], statuses: list[SourceStatus], days: list[date]) -> str:
    confirmed = [event for event in dedupe_events(events) if not event.needs_review]
    leads = [event for event in dedupe_events(events) if event.needs_review]
    confirmed.sort(key=_sort_key)
    leads.sort(key=_sort_key)

    lines: list[str] = []
    title_range = _format_range(days)
    lines.append(f"# Tampa Bay Free Events - {title_range}")
    lines.append("")
    lines.append("Free/no-cover listings only where the source supports that. Food, drinks, parking, rentals, optional donations, eligibility-based offers, and vendor purchases may still cost money.")
    lines.append("")

    for day in sorted(days):
        day_confirmed = [event for event in confirmed if event.date == day]
        day_leads = [event for event in leads if event.date == day]
        lines.append(f"## {day.strftime('%A, %B %-d, %Y')}")
        if not day_confirmed:
            lines.append("")
            lines.append("No confirmed free events found from the implemented sources.")
        else:
            non_libraries = [event for event in day_confirmed if event.source_kind != "library"]
            libraries = [event for event in day_confirmed if event.source_kind == "library"]
            if non_libraries:
                lines.append("")
                lines.append("### Public Events / Activities")
                lines.extend(_event_lines(non_libraries))
            if libraries:
                lines.append("")
                lines.append(f"### Library Events ({len(libraries)})")
                lines.extend(_event_lines(libraries))
        if day_leads:
            lines.append("")
            lines.append(f"### Search / Social Leads To Verify ({len(day_leads)})")
            lines.extend(_event_lines(day_leads))
        lines.append("")

    lines.append("## Source Status")
    for status in statuses:
        icon = "OK" if status.ok else "WARN"
        suffix = f" - {status.url}" if status.url else ""
        lines.append(f"- **{icon} {status.source}**: {status.message} ({status.count} records){suffix}")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def _event_lines(events: list[Event]) -> list[str]:
    lines = []
    for event in events:
        time_text = _format_time(event)
        link = f"[{event.title}]({event.url})" if event.url else event.title
        location = f" - {event.location}" if event.location else ""
        desc = f" - {event.description}" if event.description else ""
        source = f" _Source: {event.source}._"
        cost = f" _{event.cost_note}_" if event.needs_review else ""
        lines.append(f"- **{time_text}** - {link}{location}{desc}{cost}{source}")
    return lines


def _format_time(event: Event) -> str:
    if event.all_day:
        return "All day"
    start = _parse_dt(event.start)
    end = _parse_dt(event.end) if event.end else None
    if not start:
        return "Time not listed"
    start_text = start.strftime("%-I:%M%p").lower()
    if end:
        end_text = end.strftime("%-I:%M%p").lower()
        return f"{start_text}-{end_text}"
    return start_text


def _format_range(days: list[date]) -> str:
    if not days:
        return "No dates"
    if len(days) == 1:
        return days[0].strftime("%B %-d, %Y")
    return f"{days[0].strftime('%B %-d, %Y')} to {days[-1].strftime('%B %-d, %Y')}"


def _sort_key(event: Event):
    dt = _parse_dt(event.start)
    return (event.date or date.max, dt.time() if dt else datetime.max.time(), event.title.lower())


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

