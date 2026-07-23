from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs

from free_events.web import render_advertise, render_home, serve_payload


CACHE_DIR = Path(os.environ.get("FREE_EVENTS_CACHE_DIR", "/tmp/tampa-free-events-cache"))
CACHE_CONTROL = "public, max-age=60"
CDN_CACHE_CONTROL = "public, max-age=3600, stale-while-revalidate=21600"


def parsed_query(environ: dict) -> dict[str, list[str]]:
    params = parse_qs(environ.get("QUERY_STRING", ""))
    if os.environ.get("VERCEL"):
        params["offline"] = ["0"]
    return params


def payload_for_request(environ: dict) -> dict:
    return serve_payload(parsed_query(environ), cache_dir=CACHE_DIR)


def html_for_request(environ: dict) -> str:
    return render_home(parsed_query(environ))


def advertise_html() -> str:
    return render_advertise()


def json_response(start_response, payload: dict, status: str = "200 OK"):
    body = json.dumps(payload, indent=2).encode("utf-8")
    return response(start_response, body, "application/json; charset=utf-8", status, cacheable=status.startswith("200"))


def html_response(start_response, body: str, status: str = "200 OK"):
    return response(start_response, body.encode("utf-8"), "text/html; charset=utf-8", status)


def markdown_response(start_response, body: str, status: str = "200 OK"):
    return response(start_response, body.encode("utf-8"), "text/markdown; charset=utf-8", status, cacheable=status.startswith("200"))


def text_response(start_response, body: str, status: str = "200 OK"):
    return response(start_response, body.encode("utf-8"), "text/plain; charset=utf-8", status)


def bytes_response(start_response, body: bytes, content_type: str, status: str = "200 OK", cacheable: bool = False):
    return response(start_response, body, content_type, status, cacheable)


def response(start_response, body: bytes, content_type: str, status: str = "200 OK", cacheable: bool = False):
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    if cacheable:
        headers.append(("Cache-Control", CACHE_CONTROL))
        headers.append(("CDN-Cache-Control", CDN_CACHE_CONTROL))
        headers.append(("Vercel-CDN-Cache-Control", CDN_CACHE_CONTROL))
        headers.append(("Vercel-Cache-Tag", "tampa-free-events"))
    start_response(status, headers)
    return [body]


def site_origin() -> str:
    configured = os.environ.get("SITE_ORIGIN")
    if configured:
        return configured.rstrip("/")
    host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
    if host:
        cleaned = host.removeprefix("https://").removeprefix("http://").rstrip("/")
        scheme = "http" if cleaned.startswith(("127.0.0.1", "localhost")) else "https"
        return f"{scheme}://{cleaned}"
    return "http://127.0.0.1:8765"
