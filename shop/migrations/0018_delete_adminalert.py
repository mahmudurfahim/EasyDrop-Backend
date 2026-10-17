from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0017_backfill_retail_totals"),
    ]

    operations = [
        migrations.DeleteModel(name="AdminAlert"),
    ]
