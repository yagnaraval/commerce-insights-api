"""Feature engineering shared by training and serving (single source of truth => no skew)."""
from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from datetime import datetime
from itertools import groupby
from operator import itemgetter

FEATURE_NAMES = ["is_stool", "has_extra", "channel_app", "channel_web", "hour", "prior_orders"]


def feature_vector(sku: str, extra_code: str | None, channel: str, created_at: datetime, prior_orders: int) -> list[float]:
    """Encode one order. ``store`` is the baseline channel; ``hour`` is the UTC hour."""
    return [
        float(sku == "stool"),
        float(extra_code is not None),
        float(channel == "app"),
        float(channel == "web"),
        float(created_at.hour),
        float(prior_orders),
    ]


def build_training_set(
    rows: Iterable[tuple[str, str, str | None, str, datetime, bool]],
) -> tuple[list[list[float]], list[int]]:
    """Turn ``(email, sku, extra_code, channel, created_at, returned)`` rows into ``(X, y)``.

    ``prior_orders`` is the number of orders by the same email with a strictly earlier
    ``created_at``. Rows must be sorted by ``(email, created_at)``; each customer's timestamps
    are then sorted and a bisect gives the count in O(log k) per row.
    """
    features: list[list[float]] = []
    labels: list[int] = []
    for _, group in groupby(rows, key=itemgetter(0)):
        customer_rows = list(group)
        times = [r[4] for r in customer_rows]
        for _email, sku, extra, channel, created_at, returned in customer_rows:
            features.append(feature_vector(sku, extra, channel, created_at, bisect_left(times, created_at)))
            labels.append(int(returned))
    return features, labels
