import csv
import io
import json
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from shop.models import HistoricalOrder, Item
from shop.services.importer import import_rows

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "order_history.csv"

# Worked out independently of the importer (see README, "How the expected numbers were derived").
EXPECTED_REJECTED = {
    "duplicate_order_id": 2,
    "invalid_email": 2,
    "unknown_sku": 1,
    "extra_not_allowed": 1,
    "bad_total": 1,
    "bad_date": 2,
    "bad_returned": 1,
}
EXPECTED_CREATED = 112  # 122 data rows - 10 rejected


def run_import() -> dict:
    out = io.StringIO()
    call_command("import_history", str(CSV_PATH), stdout=out)
    return json.loads(out.getvalue().strip().splitlines()[-1])


class ImportTests(TestCase):
    def test_import_counts_and_rerun_is_noop(self):
        first = run_import()
        self.assertEqual(first, {"created": EXPECTED_CREATED, "skippedExisting": 0, "rejected": EXPECTED_REJECTED})
        self.assertEqual(HistoricalOrder.objects.count(), EXPECTED_CREATED)

        second = run_import()
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["skippedExisting"], EXPECTED_CREATED)
        self.assertEqual(second["rejected"], EXPECTED_REJECTED)
        self.assertEqual(HistoricalOrder.objects.count(), EXPECTED_CREATED)

    def test_import_does_not_touch_stock_and_normalises(self):
        run_import()
        self.assertEqual(dict(Item.objects.values_list("sku", "remaining")), {"lamp": 2, "stool": 1})
        row = HistoricalOrder.objects.get(order_id="H0003")
        self.assertEqual((row.email, row.sku), ("ada@example.com", "lamp"))

    def test_row_rules_and_order_of_checks(self):
        base = dict(
            order_id="X1", email="a@b.co", sku="lamp", extra_code="", channel="Web",
            created_at="2026-05-01T10:00:00+02:00", total_cents="8000", returned="YES",
        )
        cases = [
            ({}, None),
            ({"email": "a b@c.d"}, "invalid_email"),
            ({"email": "a@@c.d"}, "invalid_email"),
            ({"email": "@c.d"}, "invalid_email"),
            ({"sku": "sofa", "email": "bad"}, "invalid_email"),  # first failure wins
            ({"sku": "sofa"}, "unknown_sku"),
            ({"extra_code": "cushion"}, "extra_not_allowed"),
            ({"total_cents": "8001"}, "bad_total"),
            ({"total_cents": "eighty"}, "bad_total"),
            ({"created_at": "2026-05-01"}, "bad_date"),
            ({"created_at": "2026-05-01T10:00:00"}, "bad_date"),
            ({"returned": "maybe"}, "bad_returned"),
            ({"channel": "phone"}, "bad_channel"),
        ]
        for i, (override, reason) in enumerate(cases):
            HistoricalOrder.objects.all().delete()
            summary = import_rows([{**base, "order_id": f"X{i}", **override}])
            if reason is None:
                self.assertEqual(summary["created"], 1)
                stored = HistoricalOrder.objects.get()
                self.assertEqual(stored.created_at.isoformat(), "2026-05-01T08:00:00+00:00")  # UTC
                self.assertEqual((stored.channel, stored.returned), ("web", True))
            else:
                self.assertEqual(summary, {"created": 0, "skippedExisting": 0, "rejected": {reason: 1}}, override)


class HistoryApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with open(CSV_PATH, encoding="utf-8") as handle:
            import_rows(csv.DictReader(handle))

    def test_pagination_and_ordering(self):
        r = self.client.get("/api/history/").json()
        self.assertEqual((r["count"], r["page"], len(r["results"])), (EXPECTED_CREATED, 1, 10))
        keys = [(x["createdAt"], x["orderId"]) for x in r["results"]]
        self.assertEqual(keys, sorted(keys))
        last = self.client.get("/api/history/?page=12").json()
        self.assertEqual(len(last["results"]), 2)
        self.assertEqual(self.client.get("/api/history/?page=99").json()["results"], [])
        self.assertEqual(self.client.get("/api/history/?page=0").status_code, 400)

    def test_filters(self):
        r = self.client.get("/api/history/?sku=stool&channel=web&from=2026-05-01&to=2026-05-31").json()
        self.assertTrue(r["count"] > 0)
        for x in r["results"]:
            self.assertEqual((x["sku"], x["channel"]), ("stool", "web"))
            self.assertTrue("2026-05-01" <= x["createdAt"][:10] <= "2026-05-31")
        self.assertEqual(self.client.get("/api/history/?from=05/01/2026").status_code, 400)

    def test_inclusive_date_bounds(self):
        HistoricalOrder.objects.all().delete()
        import_rows([
            dict(order_id="E1", email="a@b.co", sku="lamp", extra_code="", channel="web",
                 created_at="2026-05-31T23:59:59Z", total_cents="8000", returned="0"),
            dict(order_id="E2", email="a@b.co", sku="lamp", extra_code="", channel="web",
                 created_at="2026-06-01T00:00:00Z", total_cents="8000", returned="0"),
        ])
        r = self.client.get("/api/history/?from=2026-05-31&to=2026-05-31").json()
        self.assertEqual([x["orderId"] for x in r["results"]], ["E1"])

    def test_may_2026_stats_match_hand_count(self):
        # Counted from the CSV by an independent script: lamp 43 orders / 344000 / 12 returned,
        # stool 20 orders / 265000 / 6 returned.
        stats = self.client.get("/api/history/stats?from=2026-05-01&to=2026-05-31").json()
        self.assertEqual(stats["lamp"], {"orders": 43, "revenueCents": 344000, "returnRate": 0.28})
        self.assertEqual(stats["stool"], {"orders": 20, "revenueCents": 265000, "returnRate": 0.3})

    def test_stats_with_no_orders_has_null_rate(self):
        stats = self.client.get("/api/history/stats?from=2020-01-01&to=2020-01-31").json()
        self.assertEqual(stats["lamp"], {"orders": 0, "revenueCents": 0, "returnRate": None})

    def test_stats_uses_a_single_aggregate_query(self):
        with self.assertNumQueries(2):  # one GROUP BY + one catalogue lookup
            self.client.get("/api/history/stats?from=2026-05-01&to=2026-05-31")
