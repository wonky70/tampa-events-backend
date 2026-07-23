from __future__ import annotations

from datetime import date, timedelta

from api.shared import (
    advertise_html,
    bytes_response,
    html_for_request,
    html_response,
    json_response,
    markdown_response,
    payload_for_request,
    site_origin,
    text_response,
)


def app(environ, start_response):
    path = environ.get("PATH_INFO") or environ.get("REQUEST_URI", "").split("?", 1)[0] or "/"
    try:
        if path == "/api/events":
            return json_response(start_response, payload_for_request(environ))
        if path in {"/report.md", "/api/report"}:
            return markdown_response(start_response, payload_for_request(environ)["markdown"])
        if path in {"/robots.txt", "/api/robots"}:
            return text_response(start_response, robots_text())
        if path in {"/sitemap.xml", "/api/sitemap"}:
            return sitemap_response(start_response)
        if path in {"/advertise", "/api/advertise"}:
            return html_response(start_response, advertise_html())
        return html_response(start_response, html_for_request(environ))
    except Exception as exc:
        return text_response(start_response, str(exc), status="500 Internal Server Error")


def robots_text() -> str:
    return f"""User-agent: *
Allow: /

Sitemap: {site_origin()}/sitemap.xml
"""


def sitemap_response(start_response):
    origin = site_origin()
    today = date.today()
    dates = [today + timedelta(days=offset) for offset in range(45)]
    urls = [f"  <url><loc>{origin}/?date={day.isoformat()}</loc></url>" for day in dates]
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
    body += "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
    body += f"  <url><loc>{origin}/</loc></url>\n"
    body += "\n".join(urls)
    body += "\n</urlset>\n"
    return bytes_response(start_response, body.encode("utf-8"), "application/xml; charset=utf-8", cacheable=True)
