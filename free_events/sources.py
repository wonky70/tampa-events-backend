from __future__ import annotations

import calendar
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus

from .http import FetchError, Fetcher
from .models import Event, SourceResult, SourceStatus
from .utils import (
    compact_text,
    date_range,
    in_area,
    is_likely_paid,
    iso_at,
    looks_free,
    stable_id,
    strip_query,
)


EVENTBRITE_CITIES = [
    "tampa",
    "st-petersburg",
    "clearwater",
    "brandon",
    "lutz",
    "wesley-chapel",
    "dade-city",
    "zephyrhills",
    "san-antonio",
    "land-o-lakes",
    "odessa",
    "trinity",
    "new-port-richey",
]

LIBNET_SOURCES = {
    "Hillsborough libraries": "https://attend.hcplc.org/eeventcaldata",
    "Pasco libraries": "https://pascolibraries.libnet.info/eeventcaldata",
}


@dataclass(frozen=True)
class CollectOptions:
    include_search_leads: bool = True


def collect_all(days: list[date], fetcher: Fetcher, options: CollectOptions) -> SourceResult:
    collectors = [
        collect_eventbrite,
        collect_libnet_libraries,
        collect_pinellas_libraries,
        collect_new_port_richey_library,
        collect_tampa_downtown,
        collect_city_of_tampa,
        collect_recurring,
        collect_manual_sources,
    ]
    all_events: list[Event] = []
    statuses: list[SourceStatus] = []
    for collector in collectors:
        result = collector(days, fetcher)
        all_events.extend(result.events)
        statuses.extend(result.statuses)
    if options.include_search_leads:
        result = collect_search_leads(days, fetcher)
        all_events.extend(result.events)
        statuses.extend(result.statuses)
    else:
        statuses.append(SourceStatus("Web search leads", True, "Skipped by option", 0))
    return SourceResult("all", all_events, statuses)


def collect_eventbrite(days: list[date], fetcher: Fetcher) -> SourceResult:
    events: list[Event] = []
    statuses: list[SourceStatus] = []
    for city in EVENTBRITE_CITIES:
        city_count = 0
        city_failed = False
        failure_message = ""
        failure_url = ""
        for day in days:
            page = 1
            page_count = 1
            while page <= min(page_count, 8):
                url = (
                    f"https://www.eventbrite.com/d/fl--{city}/events/"
                    f"?start_date={day.isoformat()}&end_date={day.isoformat()}&page={page}&price=free"
                )
                cache_key = f"eventbrite-price-free-{city}-{day.isoformat()}-p{page}"
                try:
                    body = fetcher.get_text(url, cache_key, ttl_seconds=3600)
                    data = _extract_eventbrite_server_data(body)
                    search_events = data["search_data"]["events"]
                    pagination = search_events.get("pagination", {})
                    page_count = int(pagination.get("page_count") or 1)
                    results = search_events.get("results") or []
                except Exception as exc:
                    city_failed = True
                    failure_message = str(exc)
                    failure_url = url
                    break
                for item in results:
                    event = _eventbrite_event(item, day, city, fetcher)
                    if event:
                        events.append(event)
                        city_count += 1
                page += 1
        statuses.append(
            SourceStatus(
                f"Eventbrite {city}",
                not city_failed,
                failure_message if city_failed else "Fetched Eventbrite price=free pages",
                city_count,
                failure_url,
            )
        )
    return SourceResult("Eventbrite", events, statuses)


def _extract_eventbrite_server_data(body: str) -> dict:
    marker = "window.__SERVER_DATA__"
    marker_index = body.find(marker)
    if marker_index == -1:
        raise ValueError("Eventbrite page did not include server data")
    start = body.find("{", marker_index)
    if start == -1:
        raise ValueError("Eventbrite server data was not JSON")
    level = 0
    in_string = False
    escaped = False
    for pos, char in enumerate(body[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            level += 1
        elif char == "}":
            level -= 1
            if level == 0:
                return json.loads(body[start : pos + 1])
    raise ValueError("Eventbrite server data JSON was incomplete")


def _eventbrite_event(item: dict, day: date, city_slug: str, fetcher: Fetcher) -> Event | None:
    title = item.get("name") or ""
    url = strip_query(item.get("url") or "")
    start_date = item.get("start_date") or day.isoformat()
    end_date = item.get("end_date") or start_date
    if not (start_date <= day.isoformat() <= end_date):
        return None
    if _eventbrite_search_unavailable(item):
        return None
    description = compact_text(item.get("summary") or item.get("full_description"))
    tag_text = _eventbrite_tag_text(item)
    text_for_cost = f"{title} {description} {tag_text}"
    if _text_has_unavailable_marker(text_for_cost):
        return None
    if is_likely_paid(text_for_cost):
        return None
    paid_risk = _eventbrite_paid_risk(text_for_cost)
    if paid_risk:
        return None
    if _eventbrite_detail_unavailable(url, item.get("id"), fetcher):
        return None
    detail_cost = _eventbrite_detail_cost(url, item.get("id"), fetcher)
    if detail_cost == "paid":
        return None
    venue = item.get("primary_venue") or {}
    address = venue.get("address") or {}
    latitude = _float_or_none(address.get("latitude"))
    longitude = _float_or_none(address.get("longitude"))
    location = address.get("localized_address_display") or venue.get("name") or ""
    venue_name = venue.get("name") or ""
    if venue_name and venue_name not in location:
        location = f"{venue_name}, {location}" if location else venue_name
    city = address.get("city") or city_slug.replace("-", " ").title()
    if not item.get("is_online_event") and not in_area(city, location):
        return None
    return Event(
        id=f"eventbrite-{item.get('id') or stable_id(title, url)}-{day.isoformat()}",
        title=title,
        start=iso_at(day, item.get("start_time")),
        end=iso_at(day, item.get("end_time")) if item.get("end_time") else None,
        all_day=False,
        description=description,
        url=url,
        location=location or "Online" if item.get("is_online_event") else location,
        city=city,
        source="Eventbrite price=free",
        category="Eventbrite",
        cost_note="Eventbrite price=free result; excluded if text, tags, price details, or availability indicate paid/sold-out/unavailable",
        needs_review=False,
        latitude=latitude,
        longitude=longitude,
        raw={"eventbrite_id": item.get("id")},
    )


def _eventbrite_search_unavailable(item: dict) -> bool:
    if item.get("is_cancelled"):
        return True
    signals = item.get("urgency_signals") or {}
    signal_text = json.dumps(signals, separators=(",", ":")) if isinstance(signals, dict) else str(signals)
    normalized = re.sub(r"[^a-z0-9]+", "", signal_text.lower())
    unavailable_tokens = [
        "soldout",
        "soldoutsale",
        "ticketssoldout",
        "salessoldout",
        "salesended",
        "ticketswithsalesended",
    ]
    return any(token in normalized for token in unavailable_tokens) or _text_has_unavailable_marker(signal_text)


def _eventbrite_paid_risk(text: str) -> bool:
    lowered = text.lower()
    if _eventbrite_free_claim(lowered) and not is_likely_paid(lowered):
        return False
    money_windows = []
    for match in re.finditer(r"\$\s*\d+", lowered):
        window = lowered[max(0, match.start() - 50) : match.end() + 50]
        money_windows.append(window)
    for window in money_windows:
        if re.search(r"\b(prize|prizes|giveaway|give away|wins?|winner|free shot)\b", window):
            continue
        return True
    commercial_keywords = [
        "certification",
        "certified",
        "training",
        "scrum",
        "course",
        "academy",
        "camp",
        "class",
        "professional session",
        "negotiation",
        "deal-making",
        "tour",
        "scavenger hunt",
        "cozymeal",
        "open play",
        "cpr",
        "bls",
        "aed",
        "tattoo apprentice",
        "summer camp",
        "e-bike",
        "cooking",
        "food tour",
        "cooler party",
        "foodies + new friends",
        "foodies new friends",
        "night out with new friends",
        "over dinner",
        "restaurant of the week",
        "restaurant of the weekend",
        "ticket goes to charity",
        "tickets go to charity",
        "tickets are",
        "bridal show",
        "bridal expo",
        "bridalshow",
        "bridal_expo",
        "brideshow",
        "tradeshow",
        "consumer show",
        "fashion & beauty",
        "booking",
        "little mania",
        "midget mayhem",
        "wrestling",
        "comedy show",
        "drag brunch",
        "drag queen show",
        "drag queen party bus",
        "burlesque show",
        "variety & cabaret show",
    ]
    return any(keyword in lowered for keyword in commercial_keywords)


def _eventbrite_detail_cost(url: str, event_id: str | None, fetcher: Fetcher) -> str:
    if not url:
        return "unknown"
    cache_key = f"eventbrite-detail-{event_id or stable_id(url)}"
    try:
        body = fetcher.get_text(url, cache_key, ttl_seconds=86400)
    except FetchError:
        return "unknown"
    if _eventbrite_detail_has_required_payment(body):
        return "paid"
    if re.search(r'"isFree"\s*:\s*false', body):
        return "paid"
    prices: list[float] = []
    for script in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>([\s\S]*?)</script>', body):
        try:
            data = json.loads(unescape(script).strip())
        except json.JSONDecodeError:
            continue
        for item in _walk_jsonld(data):
            if not isinstance(item, dict):
                continue
            if _jsonld_type_is_event(item.get("@type")):
                prices.extend(_offer_prices(item.get("offers")))
    if not prices:
        if re.search(r'"isFree"\s*:\s*true', body):
            return "free"
        return "unknown"
    return "free" if min(prices) <= 0 else "paid"


def _eventbrite_detail_unavailable(url: str, event_id: str | None, fetcher: Fetcher) -> bool:
    if not url:
        return False
    cache_key = f"eventbrite-detail-{event_id or stable_id(url)}"
    try:
        body = fetcher.get_text(url, cache_key, ttl_seconds=86400)
    except FetchError:
        return False
    return _eventbrite_detail_body_unavailable(body)


def _eventbrite_detail_body_unavailable(body: str) -> bool:
    unavailable_patterns = [
        r'"salesStatus"\s*:\s*"(?:sold_out|sales_ended|unavailable)"',
        r'"messageCode"\s*:\s*"tickets_sold_out"',
        r'"availability"\s*:\s*"(?:SoldOut|OutOfStock|Unavailable|https://schema\.org/(?:SoldOut|OutOfStock))"',
        r'data-testid="conversion-bar-sold-out-signal"',
    ]
    return any(re.search(pattern, body) for pattern in unavailable_patterns)


def _eventbrite_detail_has_required_payment(body: str) -> bool:
    text = unescape(body).lower()
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    has_in_person_payment = re.search(r"\bpay\s+in\s+person\b", text)
    has_per_person_price = re.search(
        r"\$\s*\d+(?:\.\d{1,2})?\s*(?:per person|/person|per participant|per adult|per child)\b",
        text,
    )
    return bool(has_in_person_payment and has_per_person_price)


def _jsonld_type_is_event(value) -> bool:
    if isinstance(value, list):
        return any(_jsonld_type_is_event(item) for item in value)
    if not isinstance(value, str):
        return False
    return value == "Event" or value.endswith("Event")


def _offer_prices(offers) -> list[float]:
    prices: list[float] = []
    if isinstance(offers, list):
        for offer in offers:
            prices.extend(_offer_prices(offer))
    elif isinstance(offers, dict):
        for key in ("price", "lowPrice", "highPrice"):
            value = offers.get(key)
            if value in (None, ""):
                continue
            try:
                prices.append(float(str(value).replace("$", "").replace(",", "")))
            except ValueError:
                continue
        for key in ("offers", "itemOffered"):
            prices.extend(_offer_prices(offers.get(key)))
    return prices


def _eventbrite_free_claim(text: str) -> bool:
    lowered = text.lower()
    free_patterns = [
        r"\bfree\s+(?:admission|entry|event|events|ticket|tickets|rsvp|registration|class|workshop|seminar|lesson|show|expo|tradeshow|consumer show|concert|comedy|trivia|bingo|karaoke|open mic|pilates|yoga)\b",
        r"\b(?:admission|entry|tickets?|registration)\s+(?:is\s+)?free\b",
        r"\bno\s+cover\b",
        r"\bcomplimentary\b",
        r"\bpay[- ]as[- ]you[- ]will\b",
        r"\bdonation(?:s)?\s+(?:optional|appreciated|based)\b",
        r"\bfree\s+w(?:ith)?/?\s*rsvp\b",
    ]
    return any(re.search(pattern, lowered) for pattern in free_patterns)


def _eventbrite_tag_text(item: dict) -> str:
    tags = item.get("tags") or []
    if not isinstance(tags, list):
        return ""
    return " ".join(compact_text(tag.get("display_name"), 80) for tag in tags if isinstance(tag, dict))


def _text_has_unavailable_marker(text: str) -> bool:
    lowered = unescape(text).lower()
    lowered = re.sub(r"<[^>]+>", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    unavailable_patterns = [
        r"\btickets?\s+sold\s+out\b",
        r"\bsold\s+out\b",
        r"\bregistration\s+(?:is\s+)?(?:full|closed)\b",
        r"\bwait\s*list\s+(?:is\s+)?(?:full|closed)\b",
        r"\bwaitlist\s+(?:is\s+)?(?:full|closed)\b",
        r"\btickets?\s+(?:are\s+)?unavailable\b",
        r"\bno\s+(?:tickets|spots|seats)\s+(?:available|left)\b",
        r"\bsales?\s+(?:have\s+)?ended\b",
        r"\bevent\s+(?:is\s+)?(?:cancelled|canceled)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in unavailable_patterns)


def collect_libnet_libraries(days: list[date], fetcher: Fetcher) -> SourceResult:
    events: list[Event] = []
    statuses: list[SourceStatus] = []
    for name, base_url in LIBNET_SOURCES.items():
        count = 0
        for chunk in _date_chunks(days, 31):
            start = chunk[0]
            req = quote_plus(json.dumps({"private": False, "date": start.isoformat(), "days": len(chunk)}, separators=(",", ":")))
            url = f"{base_url}?event_type=all&req={req}"
            cache_key = f"{name.lower().replace(' ', '-')}-{start.isoformat()}-{len(chunk)}"
            try:
                body = fetcher.get_text(url, cache_key, ttl_seconds=1800)
                data = json.loads(body)
            except Exception as exc:
                statuses.append(SourceStatus(name, False, str(exc), count, url))
                continue
            for item in data:
                if _libnet_item_unavailable(item):
                    continue
                event = _libnet_event(item, name)
                if event and event.date in set(days) and not is_likely_paid(_event_cost_text(event)):
                    events.append(event)
                    count += 1
        statuses.append(SourceStatus(name, True, "Fetched structured library calendar API", count))
    return SourceResult("Libnet libraries", events, statuses)


def _libnet_event(item: dict, source_name: str) -> Event | None:
    raw_start = item.get("raw_start_time") or item.get("event_start")
    if not raw_start:
        return None
    start_dt = _parse_naive_local(raw_start)
    raw_end = item.get("raw_end_time") or item.get("event_end")
    end_dt = _parse_naive_local(raw_end) if raw_end else None
    title = item.get("title") or ""
    subtitle = item.get("sub_title") or ""
    if subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"
    url = item.get("url") or ""
    location_parts = [item.get("library") or item.get("location") or ""]
    if item.get("venues"):
        location_parts.append(item["venues"])
    description = compact_text(item.get("description") or item.get("long_description"))
    return Event(
        id=f"{source_name.lower().replace(' ', '-')}-{item.get('id') or stable_id(title, url)}",
        title=title,
        start=start_dt.isoformat(),
        end=end_dt.isoformat() if end_dt else None,
        all_day=(item.get("time_string") or "").lower() == "all day",
        description=description,
        url=url,
        location=", ".join(part for part in location_parts if part),
        city=_city_from_location(item.get("location") or item.get("library") or ""),
        source=source_name,
        source_kind="library",
        category="Library",
        cost_note="Library event; included unless source text indicates a fee",
        raw={"library_event_id": item.get("id")},
    )


def _libnet_item_unavailable(item: dict) -> bool:
    text_keys = [
        "title",
        "sub_title",
        "description",
        "long_description",
        "status",
        "event_status",
        "registration_status",
        "attendance_status",
    ]
    return _text_has_unavailable_marker(" ".join(str(item.get(key) or "") for key in text_keys))


def collect_pinellas_libraries(days: list[date], fetcher: Fetcher) -> SourceResult:
    events: list[Event] = []
    statuses: list[SourceStatus] = []
    for day in days:
        url = f"https://pplc.us/events/{day.isoformat()}/?ical=1"
        cache_key = f"pplc-{day.isoformat()}"
        try:
            body = fetcher.get_text(url, cache_key, ttl_seconds=1800)
            parsed = _parse_ics(body)
        except Exception as exc:
            statuses.append(SourceStatus("Pinellas libraries", False, str(exc), 0, url))
            continue
        day_count = 0
        for item in parsed:
            text = " ".join([item.get("SUMMARY", ""), item.get("DESCRIPTION", "")])
            if is_likely_paid(text) or _text_has_unavailable_marker(text):
                continue
            event = _ics_event(item, "Pinellas libraries", "Library")
            if event and event.date == day:
                events.append(event)
                day_count += 1
        statuses.append(SourceStatus("Pinellas libraries", True, f"Fetched iCal feed for {day.isoformat()}", day_count, url))
    return SourceResult("Pinellas libraries", events, statuses)


def _parse_ics(body: str) -> list[dict[str, str]]:
    unfolded: list[str] = []
    for line in body.splitlines():
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line.rstrip("\r"))
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split(";", 1)[0]
        current[key] = _decode_ics(value)
    return events


def _decode_ics(value: str) -> str:
    return (
        value.replace("\\n", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _ics_event(item: dict[str, str], source: str, category: str) -> Event | None:
    start = _parse_ics_datetime(item.get("DTSTART"))
    if not start:
        return None
    end = _parse_ics_datetime(item.get("DTEND"))
    title = compact_text(item.get("SUMMARY"), 180)
    description = compact_text(item.get("DESCRIPTION"), 220)
    location = compact_text(item.get("LOCATION"), 180)
    return Event(
        id=f"{source.lower().replace(' ', '-')}-{item.get('UID') or stable_id(title, item.get('URL', ''), start.isoformat())}",
        title=title,
        start=start.isoformat(),
        end=end.isoformat() if end else None,
        all_day=len(item.get("DTSTART", "")) == 8,
        description=description,
        url=item.get("URL") or "",
        location=location,
        city=_city_from_location(location),
        source=source,
        source_kind="library" if category == "Library" else "event",
        category=category,
        cost_note="Included unless source text indicates a required fee",
    )


def _parse_ics_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        if len(value) == 8:
            return datetime.combine(date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}"), time(0, 0)).astimezone()
        clean = value.replace("Z", "")
        return datetime.strptime(clean[:15], "%Y%m%dT%H%M%S").astimezone()
    except ValueError:
        return None


def collect_new_port_richey_library(days: list[date], fetcher: Fetcher) -> SourceResult:
    events: list[Event] = []
    statuses: list[SourceStatus] = []
    for day in days:
        url = f"https://newportrichey.librarycalendar.com/events/list?start={day.isoformat()}&end={day.isoformat()}"
        cache_key = f"new-port-richey-library-{day.isoformat()}"
        try:
            body = fetcher.get_text(url, cache_key, ttl_seconds=1800)
            parsed = _parse_npr_library_cards(body, day)
        except Exception as exc:
            statuses.append(SourceStatus("New Port Richey library", False, str(exc), 0, url))
            continue
        events.extend(parsed)
        statuses.append(SourceStatus("New Port Richey library", True, f"Parsed event list for {day.isoformat()}", len(parsed), url))
    return SourceResult("New Port Richey library", events, statuses)


def _parse_npr_library_cards(body: str, day: date) -> list[Event]:
    events: list[Event] = []
    for title_match in re.finditer(r'<a([^>]*class="[^"]*lc-event__link[^"]*"[^>]*)>\s*([\s\S]*?)\s*</a>', body):
        attrs, title_html = title_match.groups()
        href_match = re.search(r'href="([^"]+)"', attrs)
        if not href_match:
            continue
        path = href_match.group(1)
        title = compact_text(title_html, 180)
        url = "https://newportrichey.librarycalendar.com" + path if path.startswith("/") else path
        card = body[title_match.end() : body.find('class="lc-core--extra-field"', title_match.end())]
        if not card:
            card = body[title_match.end() : title_match.end() + 3000]
        time_match = re.search(r'lc-list-event-info-item--time">\s*([\s\S]*?)\s*</div>', card)
        time_text = compact_text(time_match.group(1) if time_match else "All day", 120)
        location_match = re.search(r'<div class="lc-list-event-location">\s*([\s\S]*?)\s*</div>', card)
        location = compact_text(location_match.group(1) if location_match else "New Port Richey Public Library", 180)
        start, end, all_day = _parse_card_time(day, time_text)
        text = compact_text(card, 300)
        if is_likely_paid(f"{title} {text}") or _text_has_unavailable_marker(f"{title} {text}"):
            continue
        events.append(
            Event(
                id=f"new-port-richey-library-{stable_id(title, url, start)}",
                title=title,
                start=start,
                end=end,
                all_day=all_day,
                description="",
                url=url,
                location=location,
                city="New Port Richey",
                source="New Port Richey library",
                source_kind="library",
                category="Library",
                cost_note="Library event; included unless source text indicates a fee",
            )
        )
    return events


def _parse_card_time(day: date, text: str) -> tuple[str, str | None, bool]:
    lowered = text.lower()
    if "all day" in lowered:
        return iso_at(day, "00:00"), iso_at(day, "23:59"), True
    times = re.findall(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", lowered)
    if not times:
        return iso_at(day, "00:00"), None, False
    start = _time_tuple_to_iso(day, times[0])
    end = _time_tuple_to_iso(day, times[1]) if len(times) > 1 else None
    return start, end, False


def _time_tuple_to_iso(day: date, parts: tuple[str, str, str]) -> str:
    hour = int(parts[0])
    minute = int(parts[1] or "0")
    if parts[2] == "pm" and hour != 12:
        hour += 12
    if parts[2] == "am" and hour == 12:
        hour = 0
    return iso_at(day, f"{hour:02d}:{minute:02d}")


def collect_tampa_downtown(days: list[date], fetcher: Fetcher) -> SourceResult:
    url = "https://www.tampasdowntown.com/all-events/"
    try:
        body = fetcher.get_text(url, "tampa-downtown-all-events", ttl_seconds=1800)
        events = _parse_jsonld_events(body, "Tampa Downtown Partnership", "Official calendar")
        wanted = set(days)
        filtered = [
            event
            for event in events
            if event.date in wanted and (looks_free(_event_cost_text(event)) or not is_likely_paid(_event_cost_text(event)))
            and not _text_has_unavailable_marker(_event_cost_text(event))
        ]
        return SourceResult(
            "Tampa Downtown Partnership",
            filtered,
            [SourceStatus("Tampa Downtown Partnership", True, "Parsed event JSON-LD", len(filtered), url)],
        )
    except Exception as exc:
        return SourceResult(
            "Tampa Downtown Partnership",
            [],
            [SourceStatus("Tampa Downtown Partnership", False, str(exc), 0, url)],
        )


def _parse_jsonld_events(body: str, source: str, category: str) -> list[Event]:
    events: list[Event] = []
    for script in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>([\s\S]*?)</script>', body):
        try:
            data = json.loads(unescape(script).strip())
        except json.JSONDecodeError:
            continue
        for item in _walk_jsonld(data):
            if item.get("@type") != "Event":
                continue
            title = compact_text(item.get("name"), 180)
            start = _parse_iso_datetime(item.get("startDate"))
            if not title or not start:
                continue
            end = _parse_iso_datetime(item.get("endDate"))
            location_data = item.get("location") or {}
            location_name = location_data.get("name") if isinstance(location_data, dict) else ""
            address = location_data.get("address") if isinstance(location_data, dict) else {}
            geo = location_data.get("geo") if isinstance(location_data, dict) else {}
            location = _format_schema_location(location_name, address)
            description = compact_text(item.get("description"), 220)
            start, end = _fix_jsonld_midnight_time(start, end, description)
            event_text = " ".join([title, description, json.dumps(item.get("offers", {}))])
            if is_likely_paid(event_text) or _text_has_unavailable_marker(event_text) or _jsonld_event_unavailable(item):
                continue
            events.append(
                Event(
                    id=f"{source.lower().replace(' ', '-')}-{stable_id(title, item.get('url', ''), start.isoformat())}",
                    title=title,
                    start=start.isoformat(),
                    end=end.isoformat() if end else None,
                    all_day=False,
                    description=description,
                    url=item.get("url") or "",
                    location=location,
                    city=_city_from_location(location),
                    source=source,
                    category=category,
                    cost_note="Official calendar event; excluded if source text indicates a required fee",
                    latitude=_float_or_none(geo.get("latitude")) if isinstance(geo, dict) else None,
                    longitude=_float_or_none(geo.get("longitude")) if isinstance(geo, dict) else None,
                )
            )
    return events


def _walk_jsonld(data):
    if isinstance(data, dict):
        if "@graph" in data:
            for item in data["@graph"]:
                yield from _walk_jsonld(item)
        elif "itemListElement" in data:
            for item in data["itemListElement"]:
                yield from _walk_jsonld(item.get("item", item) if isinstance(item, dict) else item)
        else:
            yield data
    elif isinstance(data, list):
        for item in data:
            yield from _walk_jsonld(item)


def _jsonld_event_unavailable(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if _text_has_unavailable_marker(str(item.get("eventStatus") or "")):
        return True
    unavailable_availability = {"soldout", "outofstock", "unavailable", "discontinued"}
    for value in _offer_availabilities(item.get("offers")):
        normalized = re.sub(r"[^a-z0-9]+", "", value.lower().rsplit("/", 1)[-1])
        if normalized in unavailable_availability:
            return True
    return False


def _offer_availabilities(offers) -> list[str]:
    values: list[str] = []
    if isinstance(offers, list):
        for offer in offers:
            values.extend(_offer_availabilities(offer))
    elif isinstance(offers, dict):
        availability = offers.get("availability")
        if availability:
            values.append(str(availability))
        for key in ("offers", "itemOffered"):
            values.extend(_offer_availabilities(offers.get(key)))
    return values


def collect_city_of_tampa(days: list[date], fetcher: Fetcher) -> SourceResult:
    url = "https://www.tampa.gov/calendar/rss.xml"
    try:
        body = fetcher.get_text(url, "city-of-tampa-rss", ttl_seconds=1800)
        items = _parse_city_rss(body)
    except Exception as exc:
        return SourceResult("City of Tampa", [], [SourceStatus("City of Tampa", False, str(exc), 0, url)])
    wanted = set(days)
    events = []
    for event in items:
        if event.date not in wanted:
            continue
        text = " ".join([event.title, event.description, event.location, event.raw.get("full_text", "")])
        if is_likely_paid(text) and not looks_free(text):
            continue
        if _text_has_unavailable_marker(text):
            continue
        lowered = text.lower()
        if "free/open to the public? no" in lowered or "free/open to the public no" in lowered:
            continue
        if "public meeting" in lowered or "public hearing" in lowered or "public hearings" in lowered or looks_free(text):
            events.append(event)
    return SourceResult("City of Tampa", events, [SourceStatus("City of Tampa", True, "Parsed calendar RSS", len(events), url)])


def _parse_city_rss(body: str) -> list[Event]:
    root = ET.fromstring(body)
    events: list[Event] = []
    for item in root.findall(".//item"):
        title = compact_text(item.findtext("title"), 180)
        link = item.findtext("link") or ""
        description_html = item.findtext("description") or ""
        description = compact_text(description_html, 220)
        full_text = compact_text(description_html, 2500)
        when_start = description_html.find("field--name-field-event-when")
        when_end = description_html.find("add-to-calendar", when_start)
        when_block = description_html[when_start:when_end] if when_start != -1 and when_end != -1 else description_html
        times = re.findall(r'<time datetime="([^"]+)"', when_block)
        if not times:
            continue
        location_match = re.search(r'field--name-field-event-location[\s\S]*?field__item[^>]*>([\s\S]*?)</div>', description_html)
        location = compact_text(location_match.group(1), 180) if location_match else "Tampa"
        for index in range(0, len(times), 2):
            start = _parse_iso_datetime(times[index])
            end = _parse_iso_datetime(times[index + 1]) if index + 1 < len(times) else None
            if not start:
                continue
            events.append(
                Event(
                    id=f"city-of-tampa-{stable_id(title, link, start.isoformat())}",
                    title=title,
                    start=start.isoformat(),
                    end=end.isoformat() if end else None,
                    all_day=False,
                    description=description,
                    url=link,
                    location=location,
                    city="Tampa",
                    source="City of Tampa",
                    category="Official calendar",
                    cost_note="Included only for public meetings or free-marked official listings",
                    raw={"full_text": full_text},
                )
            )
    return events


def collect_recurring(days: list[date], fetcher: Fetcher) -> SourceResult:
    del fetcher
    config_path = Path("config/recurring_events.json")
    if not config_path.exists():
        return SourceResult("Recurring local events", [], [SourceStatus("Recurring local events", False, "Missing config", 0)])
    data = json.loads(config_path.read_text(encoding="utf-8"))
    events: list[Event] = []
    for item in data:
        if _text_has_unavailable_marker(" ".join(str(value) for value in item.values())):
            continue
        allowed = {day.lower() for day in item.get("days_of_week", [])}
        for day in days:
            if calendar.day_name[day.weekday()].lower() not in allowed:
                continue
            events.append(
                Event(
                    id=f"recurring-{item['id']}-{day.isoformat()}",
                    title=item["title"],
                    start=iso_at(day, item.get("start_time")),
                    end=iso_at(day, item.get("end_time")) if item.get("end_time") else None,
                    all_day=False,
                    description=item.get("description", ""),
                    url=item.get("url", ""),
                    location=item.get("location", ""),
                    city=item.get("city", ""),
                    source="Recurring local events",
                    category="Recurring",
                    cost_note="Configured recurring free/no-cover event",
                )
            )
    return SourceResult("Recurring local events", events, [SourceStatus("Recurring local events", True, "Loaded local recurring event config", len(events))])


def collect_manual_sources(days: list[date], fetcher: Fetcher) -> SourceResult:
    del fetcher
    config_path = Path("config/manual_sources.json")
    if not config_path.exists():
        return SourceResult("Manual/social sources", [], [SourceStatus("Manual/social sources", False, "Missing config", 0)])
    data = json.loads(config_path.read_text(encoding="utf-8"))
    wanted = {day.isoformat(): day for day in days}
    events: list[Event] = []
    for item in data:
        item_date = item.get("date")
        if item_date not in wanted:
            continue
        if _text_has_unavailable_marker(" ".join(str(value) for value in item.values())):
            continue
        day = wanted[item_date]
        confirmed = bool(item.get("free_confirmed"))
        events.append(
            Event(
                id=f"manual-{stable_id(item.get('title', ''), item.get('url', ''), item_date)}",
                title=item.get("title") or "Manual event lead",
                start=iso_at(day, item.get("start_time")),
                end=iso_at(day, item.get("end_time")) if item.get("end_time") else None,
                all_day=not item.get("start_time"),
                description=item.get("description", ""),
                url=item.get("url", ""),
                location=item.get("location", ""),
                city=item.get("city", ""),
                source="Manual/social sources",
                source_kind="event" if confirmed else "lead",
                category="Manual" if confirmed else "Needs review",
                cost_note="Manually confirmed free" if confirmed else "Manual/social lead only; verify date, location, and free status",
                needs_review=not confirmed,
                latitude=_float_or_none(item.get("latitude")),
                longitude=_float_or_none(item.get("longitude")),
                raw=item,
            )
        )
    return SourceResult("Manual/social sources", events, [SourceStatus("Manual/social sources", True, "Loaded manual/social source config", len(events))])


def collect_search_leads(days: list[date], fetcher: Fetcher) -> SourceResult:
    if len(days) > 31:
        return SourceResult(
            "Web search leads",
            [],
            [SourceStatus("Web search leads", True, "Skipped for ranges over 31 days; run a shorter range or add --include-search-leads explicitly for periodic reports", 0)],
        )
    events: list[Event] = []
    statuses: list[SourceStatus] = []
    for day in days:
        pretty = f"{calendar.month_name[day.month]} {day.day}, {day.year}"
        queries = [
            f'"free events" "Tampa Bay" "{pretty}"',
            f'"no cover" "Tampa" "{pretty}"',
            f'"free admission" "Tampa Bay" "{pretty}"',
            f'"free events" "Pasco County" "{pretty}"',
            f'"free events" "Dade City" "{pretty}"',
            f'"free events" "San Antonio" "FL" "{pretty}"',
            f'"free events" "Wesley Chapel" "{pretty}"',
            f'"free events" "Zephyrhills" "{pretty}"',
            f'site:reddit.com/r/tampa free event "{pretty}"',
            f'site:reddit.com/r/StPetersburgFL free event "{pretty}"',
            f'site:facebook.com/events Tampa free "{pretty}"',
            f'site:facebook.com/events Pasco County free "{pretty}"',
        ]
        day_count = 0
        for index, query in enumerate(queries):
            url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
            cache_key = f"bing-leads-{day.isoformat()}-{index}"
            try:
                body = fetcher.get_text(url, cache_key, ttl_seconds=7200)
                leads = _parse_bing_rss(body, day, query)
            except Exception as exc:
                statuses.append(SourceStatus("Web search leads", False, str(exc), day_count, url))
                continue
            events.extend(leads)
            day_count += len(leads)
        statuses.append(SourceStatus("Web search leads", True, f"Fetched Bing/RSS public search leads for {day.isoformat()}", day_count))
    return SourceResult("Web search leads", events, statuses)


def _parse_bing_rss(body: str, day: date, query: str) -> list[Event]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    events: list[Event] = []
    for item in root.findall(".//item")[:8]:
        title = compact_text(item.findtext("title"), 180)
        link = item.findtext("link") or ""
        description = compact_text(item.findtext("description"), 220)
        text = f"{title} {description}"
        if not title or not link:
            continue
        if not in_area(text, link):
            continue
        if not (looks_free(text) or "reddit" in link or "facebook" in link):
            continue
        if _text_has_unavailable_marker(text):
            continue
        events.append(
            Event(
                id=f"search-lead-{stable_id(query, title, link, day.isoformat())}",
                title=title,
                start=iso_at(day, "00:00"),
                end=None,
                all_day=True,
                description=description,
                url=link,
                location="Needs review",
                city="Tampa Bay",
                source="Web search lead",
                source_kind="lead",
                category="Needs review",
                cost_note="Search/social lead only; verify date, location, and free status manually",
                needs_review=True,
                raw={"query": query},
            )
        )
    return events


def _parse_naive_local(value: str) -> datetime:
    parsed = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    return parsed.astimezone()


def _float_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(0, 0)).astimezone()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _fix_jsonld_midnight_time(start: datetime, end: datetime | None, description: str) -> tuple[datetime, datetime | None]:
    if start.time() != time(0, 0):
        return start, end
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", description.lower())
    if not match:
        return start, end
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if match.group(3) == "pm" and hour != 12:
        hour += 12
    if match.group(3) == "am" and hour == 12:
        hour = 0
    fixed_start = start.replace(hour=hour, minute=minute)
    fixed_end = end
    if not fixed_end or fixed_end <= fixed_start:
        fixed_end = fixed_start + timedelta(hours=1)
    return fixed_start, fixed_end


def _format_schema_location(name: str | None, address: dict | str | None) -> str:
    parts = []
    if name:
        parts.append(name)
    if isinstance(address, dict):
        line = address.get("streetAddress")
        city = address.get("addressLocality")
        region = address.get("addressRegion")
        postal = address.get("postalCode")
        formatted = ", ".join(part for part in [line, city, region, postal] if part)
        if formatted:
            parts.append(formatted)
    elif isinstance(address, str) and address:
        parts.append(address)
    return ", ".join(parts)


def _city_from_location(location: str) -> str:
    lowered = location.lower()
    for city in sorted(
        [
            "Dade City",
            "San Antonio",
            "Wesley Chapel",
            "Land O'Lakes",
            "New Port Richey",
            "Zephyrhills",
            "St. Petersburg",
            "St Pete Beach",
            "Clearwater",
            "Trinity",
            "Odessa",
            "Tampa",
            "Lutz",
            "Largo",
            "Dunedin",
            "Palm Harbor",
            "Tarpon Springs",
            "Seminole",
            "Gulfport",
            "Pinellas Park",
            "Riverview",
            "Brandon",
        ],
        key=len,
        reverse=True,
    ):
        if city.lower() in lowered:
            return city
    return ""


def _date_chunks(days: list[date], size: int) -> list[list[date]]:
    if not days:
        return []
    sorted_days = sorted(days)
    chunks: list[list[date]] = []
    current: list[date] = []
    previous: date | None = None
    for day in sorted_days:
        if previous and (day - previous).days != 1:
            if current:
                chunks.append(current)
            current = []
        current.append(day)
        if len(current) == size:
            chunks.append(current)
            current = []
        previous = day
    if current:
        chunks.append(current)
    return chunks


def _event_cost_text(event: Event) -> str:
    return " ".join([event.title, event.description, event.location, event.cost_note])
