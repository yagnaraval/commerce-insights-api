from django.test import TestCase

from shop.errors import DuplicatePurchase, ExtraNotAllowed, InvalidChargeId, SoldOut, UnknownItem
from shop.models import Customer, Item, Order, OutboxMessage
from shop.services.purchases import record_purchase


def snapshot() -> dict:
    """Everything a failed purchase must leave untouched."""
    return {
        "remaining": dict(Item.objects.values_list("sku", "remaining")),
        "customers": Customer.objects.count(),
        "orders": Order.objects.count(),
        "outbox": OutboxMessage.objects.count(),
    }


class RecordPurchaseServiceTests(TestCase):
    def test_new_email_creates_customer_and_messages_in_order(self):
        order = record_purchase("  Ann@Example.COM ", "lamp", "ch_lamp00001")
        self.assertFalse(order.replayed)
        self.assertEqual(order.total_cents, 8000)
        self.assertEqual(Customer.objects.get().email, "ann@example.com")
        self.assertEqual(Item.objects.get(sku="lamp").remaining, 1)
        kinds = list(OutboxMessage.objects.order_by("id").values_list("kind", "to_email", "external_charge_id"))
        self.assertEqual(
            kinds,
            [
                ("account_created", "ann@example.com", "ch_lamp00001"),
                ("purchase_recorded", "ann@example.com", "ch_lamp00001"),
            ],
        )

    def test_extra_is_included_in_total(self):
        order = record_purchase("a@example.com", "stool", "ch_stool0001", "cushion")
        self.assertEqual(order.total_cents, 14500)
        self.assertEqual(order.extra.code, "cushion")
        self.assertEqual(Item.objects.get(sku="stool").remaining, 0)

    def test_existing_customer_gets_only_purchase_message(self):
        record_purchase("a@example.com", "lamp", "ch_lamp00001")
        record_purchase("A@example.com", "stool", "ch_stool0001")
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(OutboxMessage.objects.filter(kind="account_created").count(), 1)
        self.assertEqual(OutboxMessage.objects.filter(kind="purchase_recorded").count(), 2)

    def test_sold_out_does_not_save_new_email(self):
        record_purchase("a@example.com", "stool", "ch_stool0001")
        before = snapshot()
        with self.assertRaises(SoldOut):
            record_purchase("new@example.com", "stool", "ch_stool0002")
        self.assertEqual(snapshot(), before)
        self.assertFalse(Customer.objects.filter(email="new@example.com").exists())

    def test_duplicate_customer_and_sku(self):
        record_purchase("a@example.com", "lamp", "ch_lamp00001")
        before = snapshot()
        with self.assertRaises(DuplicatePurchase):
            record_purchase("A@Example.com ", "lamp", "ch_lamp00002")
        self.assertEqual(snapshot(), before)

    def test_extra_not_sold_with_item(self):
        before = snapshot()
        with self.assertRaises(ExtraNotAllowed):
            record_purchase("a@example.com", "lamp", "ch_lamp00001", "cushion")
        with self.assertRaises(ExtraNotAllowed):
            record_purchase("a@example.com", "stool", "ch_stool0001", "gold-leaf")
        self.assertEqual(snapshot(), before)

    def test_unknown_sku_and_invalid_charge_id_write_nothing(self):
        before = snapshot()
        with self.assertRaises(UnknownItem):
            record_purchase("a@example.com", "sofa", "ch_sofa00001")
        for bad in ["bad", "ch_short", "ch_has space1", "CH_lamp00001", "ch_lamp00001\n", ""]:
            with self.assertRaises(InvalidChargeId, msg=bad):
                record_purchase("a@example.com", "lamp", bad)
        self.assertEqual(snapshot(), before)

    def test_replay_ignores_new_sku_extra_and_email(self):
        first = record_purchase("a@example.com", "lamp", "ch_lamp00001")
        before = snapshot()
        again = record_purchase("other@example.com", "stool", "ch_lamp00001", "cushion")
        self.assertTrue(again.replayed)
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.item.sku, "lamp")
        self.assertEqual(snapshot(), before)

    def test_replay_of_sold_out_order_still_returns_original(self):
        record_purchase("a@example.com", "stool", "ch_stool0001")
        again = record_purchase("a@example.com", "stool", "ch_stool0001")
        self.assertTrue(again.replayed)

    def test_failure_after_customer_creation_path_rolls_back(self):
        """Lamp has 2 units: a third buyer is rejected and nothing of theirs persists."""
        record_purchase("a@example.com", "lamp", "ch_lamp00001")
        record_purchase("b@example.com", "lamp", "ch_lamp00002")
        before = snapshot()
        with self.assertRaises(SoldOut):
            record_purchase("c@example.com", "lamp", "ch_lamp00003")
        self.assertEqual(snapshot(), before)


class PurchaseApiTests(TestCase):
    def post(self, **body):
        return self.client.post("/api/purchases/", body, content_type="application/json")

    def test_worked_examples(self):
        r = self.post(email="ann@example.com", sku="lamp", externalChargeId="ch_lamp00001")
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertFalse(body["replayed"])
        self.assertEqual(body["order"]["totalCents"], 8000)
        self.assertIsNone(body["order"]["extraCode"])
        outbox = self.client.get("/api/outbox").json()
        self.assertEqual([m["kind"] for m in outbox], ["account_created", "purchase_recorded"])
        self.assertEqual(outbox[0], {"toEmail": "ann@example.com", "kind": "account_created", "externalChargeId": "ch_lamp00001"})

        r = self.post(email="ann@example.com", sku="stool", externalChargeId="ch_lamp00001")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["replayed"])
        self.assertEqual(r.json()["order"]["sku"], "lamp")

        r = self.post(email="ann@example.com", sku="lamp", externalChargeId="ch_lamp00002")
        self.assertEqual((r.status_code, r.json()), (409, {"error": "duplicate_purchase"}))

        r = self.post(email="bob@example.com", sku="stool", externalChargeId="ch_stool0001", extraCode="cushion")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["order"]["totalCents"], 14500)
        self.assertEqual(self.client.get("/api/catalog/stool").json()["remaining"], 0)

        r = self.post(email="cy@example.com", sku="stool", externalChargeId="ch_stool0002")
        self.assertEqual((r.status_code, r.json()), (409, {"error": "sold_out"}))
        self.assertFalse(Customer.objects.filter(email="cy@example.com").exists())

        r = self.post(email="dee@example.com", sku="lamp", externalChargeId="ch_lamp00003", extraCode="cushion")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "extra_not_allowed"}))

    def test_400s(self):
        r = self.post(email="a@example.com", sku="sofa", externalChargeId="ch_sofa00001")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "unknown_item"}))
        r = self.post(email="a@example.com", sku="lamp", externalChargeId="nope")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "invalid_charge_id"}))
        r = self.post(email="a@example.com", sku="lamp")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "missing_fields"}))
        r = self.client.post("/api/purchases/", "{not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_catalog(self):
        self.assertEqual(
            self.client.get("/api/catalog/lamp").json(),
            {"sku": "lamp", "name": "Maple lamp", "priceCents": 8000, "remaining": 2},
        )
        self.assertEqual(self.client.get("/api/catalog/nope").status_code, 404)
