from django.db import migrations


def fill(apps, schema_editor):
    """Existing retail customer rows get their order's status and the delivered / returned totals of their phone number."""
    Order = apps.get_model("shop", "Order")
    RC = apps.get_model("shop", "RetailCustomer")
    tot = {}
    for o in Order.objects.filter(status__in=["Delivered", "Returned"]).values("phone", "status", "cod"):
        t = tot.setdefault(o["phone"], [0, 0, 0, 0])
        i = 0 if o["status"] == "Delivered" else 2
        t[i] += 1
        t[i + 1] += o["cod"]
    rows = []
    for rc in RC.objects.select_related("order").iterator():
        t = tot.get(rc.phone, [0, 0, 0, 0])
        rc.status = rc.order.status
        rc.total_delivered, rc.delivered_cod, rc.total_returned, rc.returned_cod = t
        rows.append(rc)
        if len(rows) >= 500:
            RC.objects.bulk_update(rows, ["status", "total_delivered", "delivered_cod", "total_returned", "returned_cod"])
            rows = []
    if rows:
        RC.objects.bulk_update(rows, ["status", "total_delivered", "delivered_cod", "total_returned", "returned_cod"])


class Migration(migrations.Migration):

    dependencies = [
        ("shop", "0016_retailcustomer_totals_adminalert"),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
