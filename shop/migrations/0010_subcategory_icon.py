from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shop", "0009_number_categories")]
    operations = [
        migrations.AddField(model_name="subcategory", name="icon_data", field=models.BinaryField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="subcategory", name="icon_ver", field=models.PositiveIntegerField(default=0)),
    ]
