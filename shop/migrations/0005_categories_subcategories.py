from django.db import migrations, models
import django.db.models.deletion

CATEGORIES = [
    "Men's Clothing", "Women's Clothing", "Baby Fashion", "Home Decor", "Cosmetics", "Jewelries",
    "Food Item", "Gift item", "Accessories", "Gadgets", "Combo", "Seasonal",
]


def seed(apps, schema_editor):
    Category = apps.get_model("shop", "Category")
    for i, name in enumerate(CATEGORIES):
        Category.objects.get_or_create(name=name, defaults={"position": i})


class Migration(migrations.Migration):
    """The old 'category' table becomes 'sub category' (rows and product links are kept),
    and 12 fixed top-level categories are created. The database table is NOT renamed."""

    dependencies = [("shop", "0004_product_description")]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameModel("Category", "SubCategory"),
                migrations.AlterModelTable("SubCategory", "shop_category"),
            ],
        ),
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60, unique=True)),
                ("position", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"db_table": "shop_maincategory", "ordering": ["position", "id"]},
        ),
        migrations.AddField(
            model_name="subcategory",
            name="category",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="subcategories", to="shop.category"),
        ),
        migrations.AlterField(model_name="subcategory", name="name", field=models.CharField(max_length=60)),
        migrations.AddConstraint(
            model_name="subcategory",
            constraint=models.UniqueConstraint(fields=("category", "name"), name="unique_subcategory_per_category"),
        ),
        migrations.AlterModelOptions(name="subcategory", options={"ordering": ["name"], "verbose_name_plural": "sub categories"}),
        migrations.RenameField(model_name="product", old_name="category", new_name="subcategory"),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
