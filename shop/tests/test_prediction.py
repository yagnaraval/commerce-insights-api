import io
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from shop.ml.registry import registry
from shop.models import HistoricalOrder
from shop.tests.test_history import CSV_PATH, run_import

URL = "/api/predictions/return-risk"
PAYLOAD = {
    "email": "ada@example.com",
    "sku": "stool",
    "extraCode": "cushion",
    "channel": "app",
    "createdAt": "2026-07-01T10:00:00Z",
}


class PredictionTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        override = override_settings(MODEL_DIR=Path(self._tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        registry.reset()
        self.addCleanup(registry.reset)

    def train(self) -> dict:
        run_import()
        out = io.StringIO()
        call_command("train_return_model", stdout=out)
        return json.loads(out.getvalue())

    def post(self, **overrides):
        return self.client.post(URL, {**PAYLOAD, **overrides}, content_type="application/json")

    def test_503_before_training(self):
        r = self.post()
        self.assertEqual((r.status_code, r.json()), (503, {"error": "model_not_trained"}))

    def test_probability_and_version_after_training(self):
        metrics = self.train()
        r = self.post()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(0.0 <= body["probability"] <= 1.0)
        self.assertEqual(body["probability"], round(body["probability"], 3))
        self.assertEqual(body["modelVersion"], metrics["version"])

    def test_metrics_file_and_version_changes_per_run(self):
        first = self.train()
        on_disk = json.loads((Path(self._tmp.name) / "metrics.json").read_text())
        self.assertEqual(on_disk, first)
        self.assertEqual(set(first), {"version", "rows", "trainRows", "holdoutAccuracy", "baselineAccuracy", "features"})
        self.assertEqual((first["rows"], first["trainRows"]), (112, 89))
        self.assertTrue((Path(self._tmp.name) / "return_model.joblib").exists())
        out = io.StringIO()
        call_command("train_return_model", stdout=out)
        second = json.loads(out.getvalue())
        self.assertNotEqual(first["version"], second["version"])
        self.assertEqual(self.post().json()["modelVersion"], second["version"])  # retrain picked up

    def test_model_is_loaded_once(self):
        self.train()
        self.post()
        loaded = registry.get()
        for _ in range(3):
            self.post()
        self.assertIs(registry.get(), loaded)

    def test_validation_errors(self):
        self.train()
        cases = [
            ({"sku": "sofa"}, "unknown_item"),
            ({"sku": "lamp"}, "extra_not_allowed"),
            ({"channel": "phone"}, "bad_channel"),
            ({"createdAt": "yesterday"}, "bad_date"),
            ({"email": "nope"}, "invalid_email"),
        ]
        for override, code in cases:
            r = self.post(**override)
            self.assertEqual((r.status_code, r.json()), (400, {"error": code}), override)
        self.assertEqual(self.client.post(URL, {"sku": "lamp"}, content_type="application/json").status_code, 400)

    def test_unknown_sku_beats_missing_model(self):
        r = self.post(sku="sofa")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "unknown_item"}))

    def test_prior_orders_feature_counts_strictly_earlier_orders(self):
        from shop.ml.features import build_training_set
        from datetime import datetime, timezone

        t = lambda h: datetime(2026, 5, 1, h, tzinfo=timezone.utc)
        rows = [
            ("a@x.co", "lamp", None, "web", t(1), False),
            ("a@x.co", "lamp", None, "web", t(2), True),
            ("a@x.co", "stool", None, "app", t(2), False),  # same instant => not "earlier"
            ("b@x.co", "lamp", None, "store", t(1), False),
        ]
        X, y = build_training_set(rows)
        self.assertEqual([r[-1] for r in X], [0.0, 1.0, 1.0, 0.0])
        self.assertEqual(y, [0, 1, 0, 0])

    def test_training_without_data_fails_cleanly(self):
        self.assertEqual(HistoricalOrder.objects.count(), 0)
        with self.assertRaises(CommandError):
            call_command("train_return_model", stdout=io.StringIO())
