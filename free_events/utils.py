from __future__ import annotations

import hashlib
import html
import json
import re
import textwrap
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("America/New_York")

AREA_CITIES = {
    "apollo beach",
    "brandon",
    "clearwater",
    "dade city",
    "dunedin",
    "gibsonton",
    "gulfport",
    "land o lakes",
    "land o'lakes",
    "largo",
    "lutz",
    "new port richey",
    "odessa",
    "oldsmar",
    "palm harbor",
    "pinellas park",
    "plant city",
    "riverview",
    "safety harbor",
    "san antonio",
    "seminole",
    "st pete beach",
    "st petersburg",
    "st. pete beach",
    "st. petersburg",
    "sun city center",
    "tampa",
    "tarpon springs",
    "temple terrace",
    "treasure island",
    "trinity",
    "wesley chapel",
    "ybor",
    "zephyrhills",
}


def parse_date_token(value: str, today: date | None = None) -> date:
    today = today or datetime.now(LOCAL_TZ).date()
    normalized = value.strip().lower()
    if normalized == "today":
        return today
    if normalized == "tomorrow":
        return today + timedelta(days=1)
    return date.fromisoformat(value)


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def iso_at(day: date, hhmm: str | None, fallback: time = time(0, 0)) -> str:
    if not hhmm:
        t = fallback
    else:
        match = re.match(r"^(\d{1,2}):(\d{2})$", hhmm.strip())
        if not match:
            t = fallback
        else:
            t = time(int(match.group(1)), int(match.group(2)))
    return datetime.combine(day, t, LOCAL_TZ).isoformat()


def compact_text(value: str | None, max_len: int = 220) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= max_len:
        return value
    return textwrap.shorten(value, width=max_len, placeholder="...")


def strip_query(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def stable_id(*parts: str) -> str:
    joined = "|".join(part or "" for part in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def dedupe_events(events: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped = []
    for event in events:
        key = strip_query(event.url) if getattr(event, "url", "") else event.id
        key = f"{key}|{event.start}|{event.title.lower()}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def is_likely_paid(text: str) -> bool:
    lowered = text.lower()
    paid_patterns = [
        r"\bonly\s+\$\s*\d",
        r"starts?\s+at\s+\$",
        r"\bpay\s+\$\s*\d",
        r"required\s+purchase",
        r"purchase\s+required",
        r"when\s+you\s+buy",
        r"\bbuy\s+a\b",
        r"with\s+(?:a\s+)?(?:drink|ticket|food|meal)\s+purchase",
        r"\$\s*\d+(?:\.\d+)?\s*(?:entry|admission|cover|fee|registration|ticket|tickets|per|minimum)",
        r"(?:entry|admission|cover|registration|ticket|tickets|fee|cost)\s*(?:is|:)?\s*\$\s*\d",
        r"\bpaid\s+admission\b",
    ]
    return any(re.search(pattern, lowered) for pattern in paid_patterns)


def has_money_amount(text: str) -> bool:
    return bool(re.search(r"\$\s*\d", text))


def looks_free(text: str) -> bool:
    lowered = text.lower()
    free_markers = [
        "free",
        "no cover",
        "complimentary",
        "pay-as-you-will",
        "pay as you will",
        "donation based",
        "donations appreciated",
        "donation optional",
    ]
    return any(marker in lowered for marker in free_markers)


def in_area(city: str, location: str = "") -> bool:
    haystack = f"{city} {location}".lower().replace("’", "'")
    return any(area in haystack for area in AREA_CITIES)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
