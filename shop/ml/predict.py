"""Serving-side validation and inference for the return-risk model."""
from __future__ import annotations

from ..catalog import load_price_catalog
from ..errors import ExtraNotAllowed, InvalidInput, ModelNotTrained, UnknownItem
from ..models import HistoricalOrder
from ..validation import CHANNELS, is_valid_email, normalise_email, normalise_optional, normalise_token, parse_iso_datetime
from .features import feature_vector
from .registry import registry


def predict_return_risk(email: str, sku: str, extra_code: str | None, channel: str, created_at: str) -> tuple[float, str]:
    """Return ``(probability_rounded_3dp, model_version)`` for a hypothetical order.

    Input is validated first (400-class errors), then the model is looked up (503 if absent).
    """
    email = normalise_email(email)
    if not is_valid_email(email):
        raise InvalidInput("invalid_email")
    sku = normalise_token(sku)
    catalog = load_price_catalog()
    if sku not in catalog.item_prices:
        raise UnknownItem()
    extra = normalise_optional(extra_code)
    if catalog.total_for(sku, extra) is None:
        raise ExtraNotAllowed()
    channel = normalise_token(channel)
    if channel not in CHANNELS:
        raise InvalidInput("bad_channel")
    try:
        when = parse_iso_datetime(created_at)
    except ValueError:
        raise InvalidInput("bad_date") from None

    loaded = registry.get()
    if loaded is None:
        raise ModelNotTrained()

    prior = HistoricalOrder.objects.filter(email=email, created_at__lt=when).count()
    vector = feature_vector(sku, extra, channel, when, prior)
    classes = list(loaded.estimator.classes_)
    probability = loaded.estimator.predict_proba([vector])[0][classes.index(1)]
    return round(float(probability), 3), loaded.version
