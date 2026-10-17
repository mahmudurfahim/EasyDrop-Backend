from django.db import migrations


def renumber(apps, schema_editor):
    """Priority numbers start at 1 (0 now means 'no number, show after the numbered ones'), so the 12 categories get 1..12 in their current order."""
    Category = apps.get_model("shop", "Category")
    for i, c in enumerate(Category.objects.order_by("position", "id"), start=1):
        if c.position != i:
            c.position = i
            c.save(update_fields=["position"])


class Migration(migrations.Migration):
    dependencies = [("shop", "0008_stock_variants_priority")]
    operations = [migrations.RunPython(renumber, migrations.RunPython.noop)]
