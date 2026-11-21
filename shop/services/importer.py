"""Re-runnable bulk import of historical orders from CSV."""
from __future__ import annotations

import csv
import logging
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from django.db import transaction

from ..catalog import PriceCatalog, load_price_catalog
from ..models import HistoricalOrder
from ..validation import (
    CHANNELS,
    is_valid_email,
    normalise_email,
    normalise_optional,
    normalise_token,
    parse_bool,
    parse_iso_datetime,
)

logger = logging.getLogger(__name__)
BATCH_SIZE = 500


def validate_row(row: dict[str, str | None], catalog: PriceCatalog) -> tuple[HistoricalOrder | None, str | None]:
    """Validate one CSV row. Returns ``(unsaved_model, None)`` or ``(None, first_failing_reason)``.

    ``duplicate_order_id`` is file-level state and is checked by the caller before this.
    """
    email = normalise_email(row.get("email") or "")
    if not is_valid_email(email):
        return None, "invalid_email"

    sku = normalise_token(row.get("sku"))
    if sku not in catalog.item_prices:
        return None, "unknown_sku"

    extra = normalise_optional(row.get("extra_code"))
    expected_total = catalog.total_for(sku, extra)
    if expected_total is None:  # extra set but not sold with this item
        return None, "extra_not_allowed"

    try:
        total = int((row.get("total_cents") or "").strip())
    except ValueError:
        return None, "bad_total"
    if total != expected_total:
        return None, "bad_total"

    try:
        created_at = parse_iso_datetime(row.get("created_at"))
    except ValueError:
        return None, "bad_date"

    try:
        returned = parse_bool(row.get("returned"))
    except ValueError:
        return None, "bad_returned"

    channel = normalise_token(row.get("channel"))
    if channel not in CHANNELS:
        return None, "bad_channel"

    return (
        HistoricalOrder(
            order_id=(row.get("order_id") or "").strip(),
            email=email,
            sku=sku,
            extra_code=extra,
            channel=channel,
            created_at=created_at,
            total_cents=total,
            returned=returned,
        ),
        None,
    )


def _chunks(rows: Iterable[HistoricalOrder], size: int) -> Iterator[list[HistoricalOrder]]:
    batch: list[HistoricalOrder] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def import_rows(rows: Iterable[dict[str, str | None]]) -> dict:
    """Import rows and return ``{"created", "skippedExisting", "rejected"}``.

    Streams the input, de-duplicates ``order_id`` within the file, checks existing ids with one
    ``IN`` query per batch and inserts with ``bulk_create``. The whole import is one transaction,
    and re-running it is a no-op because existing ids are skipped.
    """
    catalog = load_price_catalog()
    seen: set[str] = set()
    rejected: Counter[str] = Counter()
    created = skipped = 0

    def valid_rows() -> Iterator[HistoricalOrder]:
        for row in rows:
            order_id = (row.get("order_id") or "").strip()
            if order_id in seen:
                rejected["duplicate_order_id"] += 1
                continue
            seen.add(order_id)
            model, reason = validate_row(row, catalog)
            if reason:
                rejected[reason] += 1
            else:
                yield model

    with transaction.atomic():
        for batch in _chunks(valid_rows(), BATCH_SIZE):
            existing = set(
                HistoricalOrder.objects.filter(order_id__in=[o.order_id for o in batch]).values_list(
                    "order_id", flat=True
                )
            )
            fresh = [o for o in batch if o.order_id not in existing]
            skipped += len(batch) - len(fresh)
            HistoricalOrder.objects.bulk_create(fresh, batch_size=BATCH_SIZE)
            created += len(fresh)

    summary = {"created": created, "skippedExisting": skipped, "rejected": dict(rejected)}
    logger.info("history import finished: %s", summary)
    return summary


def import_csv(path: str | Path) -> dict:
    """Import a CSV file (UTF-8, optional BOM) with the documented header."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return import_rows(csv.DictReader(handle))
