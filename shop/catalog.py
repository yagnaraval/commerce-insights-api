"""In-memory snapshot of sku/extra prices, loaded with two queries."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import Extra, Item


@dataclass(frozen=True)
class PriceCatalog:
    item_prices: dict[str, int]
    # sku -> {extra_code: price_cents}
    extra_prices: dict[str, dict[str, int]] = field(default_factory=dict)

    def total_for(self, sku: str, extra_code: str | None) -> int | None:
        """Expected total, or ``None`` when the sku/extra combination is not sellable."""
        if sku not in self.item_prices:
            return None
        if extra_code is None:
            return self.item_prices[sku]
        extra_price = self.extra_prices.get(sku, {}).get(extra_code)
        return None if extra_price is None else self.item_prices[sku] + extra_price


def load_price_catalog() -> PriceCatalog:
    item_prices = dict(Item.objects.values_list("sku", "price_cents"))
    extra_prices: dict[str, dict[str, int]] = {sku: {} for sku in item_prices}
    for sku, code, price in Extra.objects.values_list("items__sku", "code", "price_cents"):
        if sku is not None:
            extra_prices[sku][code] = price
    return PriceCatalog(item_prices, extra_prices)
