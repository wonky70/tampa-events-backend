from __future__ import annotations

import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) free-events-local-tool/0.1",
    "Accept": "text/html,application/json,text/calendar;q=0.9,*/*;q=0.8",
}


class FetchError(RuntimeError):
    pass


class Fetcher:
    def __init__(self, cache_dir: Path, offline: bool = False, delay: float = 0.5) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.delay = delay

    def get_text(self, url: str, cache_key: str, ttl_seconds: int = 3600) -> str:
        cache_path = self.cache_dir / f"{cache_key}.txt"
        if cache_path.exists() and (self.offline or not self._expired(cache_path, ttl_seconds)):
            return cache_path.read_text(encoding="utf-8")
        if self.offline:
            raise FetchError(f"No cached response for {url}")
        request = Request(url, headers=DEFAULT_HEADERS)
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", "replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            if cache_path.exists():
                return cache_path.read_text(encoding="utf-8")
            raise FetchError(str(exc)) from exc
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(body, encoding="utf-8")
        if self.delay:
            time.sleep(self.delay)
        return body

    @staticmethod
    def _expired(path: Path, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return True
        return time.time() - path.stat().st_mtime > ttl_seconds
