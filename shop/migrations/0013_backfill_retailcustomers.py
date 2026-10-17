from django.db import migrations


def fill(apps, schema_editor):  # orders placed before this table existed get their customer row too
    Order, RetailCustomer = apps.get_model("shop", "Order"), apps.get_model("shop", "RetailCustomer")
    for o in Order.objects.filter(retail_customer__isnull=True).iterator():
        c = RetailCustomer.objects.create(order=o, reseller_id=o.reseller_id, name=o.customer_name, phone=o.phone, address=o.address)
        RetailCustomer.objects.filter(pk=c.pk).update(created=o.created)


class Migration(migrations.Migration):
    dependencies = [("shop", "0012_retailcustomer")]
    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
