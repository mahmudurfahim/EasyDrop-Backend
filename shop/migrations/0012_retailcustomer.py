from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("shop", "0011_supabase_storage_paths"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="RetailCustomer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("phone", models.CharField(max_length=11)),
                ("address", models.CharField(max_length=490)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("order", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="retail_customer", to="shop.order")),
                ("reseller", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="retail_customers", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name_plural": "retail customers", "db_table": "shop_retailcustomer", "ordering": ["-id"]},
        ),
    ]
