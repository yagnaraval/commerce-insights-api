"""Purchase use-case. All business rules live here, in one transaction."""
from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.db.models import F

from ..errors import (
    DuplicatePurchase,
    ExtraNotAllowed,
    InvalidChargeId,
    SoldOut,
    UnknownItem,
)
from ..models import Customer, Extra, Item, Order, OutboxMessage
from ..validation import is_valid_charge_id, normalise_email, normalise_optional, normalise_token

logger = logging.getLogger(__name__)


def _find_replay(external_charge_id: str) -> Order | None:
    order = (
        Order.objects.select_related("customer", "item", "extra")
        .filter(external_charge_id=external_charge_id)
        .first()
    )
    if order is not None:
        order.replayed = True
    return order


def record_purchase(
    email: str, sku: str, external_charge_id: str, extra_code: str | None = None
) -> Order:
    """Record a purchase atomically and return the :class:`Order`.

    The returned order carries a ``replayed`` attribute: ``True`` when the charge id had been
    processed before (nothing is written or changed in that case), ``False`` for a new order.

    Rules are evaluated in this order: charge-id format, replay, unknown sku, extra not sold
    with the item, sold out, duplicate customer+sku, then success. Any :class:`DomainError`
    rolls back every write made by the call.
    """
    if not is_valid_charge_id(external_charge_id):
        raise InvalidChargeId()

    with transaction.atomic():
        replay = _find_replay(external_charge_id)
        if replay is not None:
            return replay

        email = normalise_email(email)
        item = Item.objects.select_for_update().filter(sku=normalise_token(sku)).first()
        if item is None:
            raise UnknownItem()

        extra: Extra | None = None
        code = normalise_optional(extra_code)
        if code is not None:
            extra = item.extras.filter(code=code).first()
            if extra is None:
                raise ExtraNotAllowed()

        if item.remaining <= 0:
            raise SoldOut()

        if Order.objects.filter(customer__email=email, item=item).exists():
            raise DuplicatePurchase()

        try:
            # Inner savepoint: if a concurrent request wins a unique constraint we roll back
            # the customer/outbox/stock writes below without poisoning the outer transaction.
            with transaction.atomic():
                order = _commit_purchase(email, item, extra, external_charge_id)
        except IntegrityError:
            replay = _find_replay(external_charge_id)
            if replay is not None:  # lost a race on the same charge id: behave like a replay
                return replay
            raise DuplicatePurchase() from None

        order.replayed = False
        logger.info("purchase recorded charge=%s sku=%s", external_charge_id, item.sku)
        return order


def _commit_purchase(email: str, item: Item, extra: Extra | None, charge_id: str) -> Order:
    """Write customer, order, stock decrement and outbox rows. Caller owns the savepoint."""
    # Conditional UPDATE keeps stock non-negative even without row locks (e.g. SQLite).
    if not Item.objects.filter(pk=item.pk, remaining__gt=0).update(remaining=F("remaining") - 1):
        raise SoldOut()

    customer, is_new = Customer.objects.get_or_create(email=email)
    total = item.price_cents + (extra.price_cents if extra else 0)
    order = Order.objects.create(
        customer=customer,
        item=item,
        extra=extra,
        external_charge_id=charge_id,
        total_cents=total,
    )

    messages = []
    if is_new:
        messages.append(OutboxMessage(to_email=email, kind=OutboxMessage.ACCOUNT_CREATED, external_charge_id=charge_id))
    messages.append(OutboxMessage(to_email=email, kind=OutboxMessage.PURCHASE_RECORDED, external_charge_id=charge_id))
    OutboxMessage.objects.bulk_create(messages)  # ids follow list order => creation order
    return order
