from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shop", "0010_subcategory_icon")]
    operations = [
        migrations.AddField(model_name="productimage", name="path", field=models.CharField(blank=True, max_length=200)),
        migrations.AlterField(model_name="productimage", name="data", field=models.BinaryField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="subcategory", name="icon_path", field=models.CharField(blank=True, max_length=200)),
    ]
