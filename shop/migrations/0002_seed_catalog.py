"""Data migration: seed the two items and the cushion extra."""
from django.db import migrations


def seed(apps, schema_editor):
    Item = apps.get_model("shop", "Item")
    Extra = apps.get_model("shop", "Extra")
    Item.objects.update_or_create(sku="lamp", defaults={"name": "Maple lamp", "price_cents": 8000, "remaining": 2})
    stool, _ = Item.objects.update_or_create(
        sku="stool", defaults={"name": "Cedar stool", "price_cents": 12000, "remaining": 1}
    )
    cushion, _ = Extra.objects.update_or_create(code="cushion", defaults={"name": "Cushion", "price_cents": 2500})
    cushion.items.set([stool])


class Migration(migrations.Migration):
    dependencies = [("shop", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
