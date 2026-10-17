import os
from django.core.management.base import BaseCommand
from shop.models import User


class Command(BaseCommand):
    help = "Create the admin login (default easydrop / easydrop). Runs on every deploy."

    def handle(self, *a, **k):
        name, pw = os.getenv("ADMIN_USER", "easydrop"), os.getenv("ADMIN_PASSWORD", "")
        u, created = User.objects.get_or_create(username=name, defaults={"first_name": "Admin"})
        u.is_staff = u.is_superuser = u.phone_verified = u.is_active = True
        u.status = "approved"
        if created or pw:  # an existing admin keeps the password you changed in the panel
            u.set_password(pw or name)
        u.save()
        self.stdout.write(f"Admin ready: {name}")
