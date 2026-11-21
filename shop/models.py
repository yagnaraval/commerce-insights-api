"""Relational model. Money is always integer cents."""
from django.db import models


class Item(models.Model):
    sku = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField()
    remaining = models.PositiveIntegerField()

    def __str__(self) -> str:
        return self.sku


class Extra(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField()
    items = models.ManyToManyField(Item, related_name="extras")

    def __str__(self) -> str:
        return self.code


class Customer(models.Model):
    email = models.EmailField(unique=True)  # always stored trimmed + lowercase
    created_at = models.DateTimeField(auto_now_add=True)


class Order(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="orders")
    extra = models.ForeignKey(Extra, null=True, blank=True, on_delete=models.PROTECT)
    external_charge_id = models.CharField(max_length=64, unique=True)
    total_cents = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # DB-level backstop for the "one purchase per customer per item" rule.
            models.UniqueConstraint(fields=["customer", "item"], name="uniq_customer_item"),
        ]


class OutboxMessage(models.Model):
    ACCOUNT_CREATED = "account_created"
    PURCHASE_RECORDED = "purchase_recorded"
    KIND_CHOICES = [(ACCOUNT_CREATED, ACCOUNT_CREATED), (PURCHASE_RECORDED, PURCHASE_RECORDED)]

    to_email = models.EmailField()
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    external_charge_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class HistoricalOrder(models.Model):
    """Imported pre-migration order. Never touches stock; used for analytics and ML."""

    order_id = models.CharField(max_length=64, unique=True)
    email = models.CharField(max_length=254, db_index=True)
    sku = models.CharField(max_length=32)
    extra_code = models.CharField(max_length=32, null=True, blank=True)
    channel = models.CharField(max_length=16)
    created_at = models.DateTimeField()
    total_cents = models.PositiveIntegerField()
    returned = models.BooleanField()

    class Meta:
        indexes = [
            models.Index(fields=["created_at", "order_id"], name="hist_created_order_idx"),
            models.Index(fields=["sku", "created_at"], name="hist_sku_created_idx"),
        ]
