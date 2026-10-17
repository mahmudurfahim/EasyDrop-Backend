from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0014_order_tracking_url"),
    ]

    operations = [
        migrations.AddField(model_name="category", name="icon_data", field=models.BinaryField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="category", name="icon_ver", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="category", name="icon_path", field=models.CharField(blank=True, max_length=200)),
    ]
