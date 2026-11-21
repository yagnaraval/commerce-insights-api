"""Train and persist the return-risk model."""
from __future__ import annotations

import json
import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
from django.conf import settings
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from ..models import HistoricalOrder
from .features import FEATURE_NAMES, build_training_set
from .registry import METRICS_FILE, MODEL_FILE, registry

logger = logging.getLogger(__name__)


class TrainingError(Exception):
    """Raised when the data cannot support training (too few rows / a single class)."""


def _atomic_write(path: Path, write) -> None:
    """Write via a temp file + ``os.replace`` so readers never see a half-written artifact."""
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def train() -> dict:
    """Train on all :class:`HistoricalOrder` rows, save artifact + metrics, return metrics."""
    rows = HistoricalOrder.objects.order_by("email", "created_at").values_list(
        "email", "sku", "extra_code", "channel", "created_at", "returned"
    )
    X, y = build_training_set(rows.iterator(chunk_size=2000))
    counts = Counter(y)
    if len(counts) < 2 or min(counts.values()) < 2:
        raise TrainingError("need at least two rows of each class (returned / not returned) to train")

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=0, stratify=y)
    model = LogisticRegression(max_iter=1000, random_state=0).fit(X_train, y_train)

    holdout_accuracy = float(model.score(X_test, y_test))
    majority = Counter(y_test).most_common(1)[0][1]
    baseline_accuracy = majority / len(y_test)

    version = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    metrics = {
        "version": version,
        "rows": len(y),
        "trainRows": len(y_train),
        "holdoutAccuracy": round(holdout_accuracy, 4),
        "baselineAccuracy": round(baseline_accuracy, 4),
        "features": FEATURE_NAMES,
    }

    model_dir = Path(settings.MODEL_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)
    bundle = {"model": model, "version": version, "features": FEATURE_NAMES}
    _atomic_write(model_dir / MODEL_FILE, lambda p: joblib.dump(bundle, p))
    _atomic_write(model_dir / METRICS_FILE, lambda p: p.write_text(json.dumps(metrics, indent=2), encoding="utf-8"))
    registry.reset()
    logger.info("trained return model %s", metrics)
    return metrics
