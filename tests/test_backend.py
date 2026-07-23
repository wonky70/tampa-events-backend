from __future__ import annotations

import json
import unittest

import api.index as public_app
from free_events.sources import (
    _eventbrite_detail_body_unavailable,
    _eventbrite_detail_has_required_payment,
)
from free_events.web import load_snapshot, serve_payload


class NeverShowPaidAsFreeTests(unittest.TestCase):
    """The #1 rule: paid / sold-out items must never surface as free."""

    def test_sold_out_detail_is_unavailable(self) -> None:
        body = '{"salesStatus":{"salesStatus":"sold_out","messageCode":"tickets_sold_out"}}'
        self.assertTrue(_eventbrite_detail_body_unavailable(body))

    def test_required_payment_is_paid(self) -> None:
        body = "<p>RSVP a spot ON EVENTBRITE pay in person</p><p>$11 per person</p>"
        self.assertTrue(_eventbrite_detail_has_required_payment(body))


class PayloadContractTests(unittest.TestCase):
    def test_snapshot_payload_shape_and_leads_rule(self) -> None:
        snap = load_snapshot()
        if not snap or not snap.get("days"):
            self.skipTest("no snapshot bundled")
        day = snap["days"][0]
        payload = serve_payload({"date": [day], "days": ["1"], "search": ["0"], "offline": ["1"]})
        for key in ("dates", "generated_at", "events", "statuses", "sponsors"):
            self.assertIn(key, payload)
        # search=0 must drop unverified search leads.
        for ev in payload["events"]:
            self.assertNotEqual(ev.get("source_kind"), "lead")
        # every event carries a source link and a free-cost note.
        for ev in payload["events"]:
            self.assertIn("url", ev)
            self.assertIn("cost_note", ev)


class ApiRouteTests(unittest.TestCase):
    def test_api_events_returns_json(self) -> None:
        original = public_app.payload_for_request
        public_app.payload_for_request = lambda environ: {
            "dates": ["2026-07-21"], "generated_at": "x", "events": [],
            "statuses": [], "sponsors": [], "markdown": "# t\n",
        }
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        try:
            environ = {"PATH_INFO": "/api/events", "QUERY_STRING": "date=2026-07-21"}
            body = b"".join(public_app.app(environ, start_response))
        finally:
            public_app.payload_for_request = original
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(json.loads(body.decode("utf-8"))["dates"], ["2026-07-21"])


if __name__ == "__main__":
    unittest.main()
