from django.db import migrations, models

import shop.models


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0013_backfill_retailcustomers"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="tracking_url",
            field=models.URLField(blank=True, max_length=300, validators=[shop.models.tracking_ok]),
        ),
    ]
