import shop.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0003_order_zone_delivery_fee'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='description',
            field=models.TextField(default='', validators=[shop.models.desc_ok]),
            preserve_default=False,
        ),
    ]
