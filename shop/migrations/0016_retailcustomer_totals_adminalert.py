from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0015_category_icon"),
    ]

    operations = [
        migrations.AddField(model_name="retailcustomer", name="status", field=models.CharField(default="Pending", max_length=12)),
        migrations.AddField(model_name="retailcustomer", name="total_delivered", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="retailcustomer", name="total_returned", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="retailcustomer", name="delivered_cod", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="retailcustomer", name="returned_cod", field=models.PositiveIntegerField(default=0)),
        migrations.CreateModel(
            name="AdminAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=20, unique=True)),
                ("sent", models.BooleanField(default=False)),
                ("updated", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
