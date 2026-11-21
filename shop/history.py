"""Read-side queries over :class:`HistoricalOrder`. Aggregation is done in the database."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Q, QuerySet, Sum

from .models import HistoricalOrder, Item

PAGE_SIZE = 10


def _utc_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def filter_history(
    sku: str | None = None,
    channel: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> QuerySet[HistoricalOrder]:
    """Filter by sku/channel and by inclusive UTC calendar-date range.

    ``date_to`` is converted to an exclusive upper bound (next midnight) so the filter is a
    plain range scan on ``created_at`` and can use the index.
    """
    qs = HistoricalOrder.objects.all()
    if sku:
        qs = qs.filter(sku=sku)
    if channel:
        qs = qs.filter(channel=channel)
    if date_from:
        qs = qs.filter(created_at__gte=_utc_start(date_from))
    if date_to:
        qs = qs.filter(created_at__lt=_utc_start(date_to + timedelta(days=1)))
    return qs


def paginate(qs: QuerySet[HistoricalOrder], page: int) -> tuple[int, list[HistoricalOrder]]:
    """Return ``(total_count, rows_of_page)`` ordered by ``created_at`` then ``order_id``."""
    ordered = qs.order_by("created_at", "order_id")
    start = (page - 1) * PAGE_SIZE
    return ordered.count(), list(ordered[start : start + PAGE_SIZE])


def _rate(returned: int, orders: int) -> float | None:
    if orders == 0:
        return None
    ratio = (Decimal(returned) / Decimal(orders)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(ratio)


def sku_stats(date_from: date | None, date_to: date | None) -> dict[str, dict]:
    """Per-sku order count, revenue and return rate via a single ``GROUP BY`` query.

    Every catalogue sku is present; skus without orders get zeros and ``returnRate: null``.
    """
    rows = (
        filter_history(date_from=date_from, date_to=date_to)
        .values("sku")
        .annotate(
            orders=Count("pk"),
            revenue=Sum("total_cents"),
            returned=Count("pk", filter=Q(returned=True)),
        )
    )
    by_sku = {r["sku"]: r for r in rows}
    result: dict[str, dict] = {}
    for sku in Item.objects.order_by("sku").values_list("sku", flat=True):
        r = by_sku.pop(sku, None)
        orders = r["orders"] if r else 0
        result[sku] = {
            "orders": orders,
            "revenueCents": (r["revenue"] or 0) if r else 0,
            "returnRate": _rate(r["returned"], orders) if r else None,
        }
    for sku, r in by_sku.items():  # skus present in history but missing from the catalogue
        result[sku] = {
            "orders": r["orders"],
            "revenueCents": r["revenue"] or 0,
            "returnRate": _rate(r["returned"], r["orders"]),
        }
    return result
