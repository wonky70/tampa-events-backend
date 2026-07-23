from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True)
class Event:
    id: str
    title: str
    start: str
    end: str | None
    all_day: bool
    description: str
    url: str
    location: str
    city: str
    source: str
    source_kind: str = "event"
    category: str = ""
    cost_note: str = "Free / no-cover unless noted by source"
    needs_review: bool = False
    latitude: float | None = None
    longitude: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def asdict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def start_dt(self) -> datetime | None:
        if not self.start:
            return None
        try:
            return datetime.fromisoformat(self.start)
        except ValueError:
            return None

    @property
    def date(self) -> date | None:
        value = self.start_dt
        return value.date() if value else None


@dataclass(slots=True)
class SourceStatus:
    source: str
    ok: bool
    message: str
    count: int = 0
    url: str = ""

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceResult:
    source: str
    events: list[Event] = field(default_factory=list)
    statuses: list[SourceStatus] = field(default_factory=list)
