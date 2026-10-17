"""One-time move of the photos and icons that are still inside the database to Supabase Storage.

Safe to run again and again: it only touches rows that have no storage path yet.

    python manage.py migrate_images_to_storage --dry-run   # only counts, changes nothing
    python manage.py migrate_images_to_storage             # copies to Supabase, keeps the database bytes as a backup
    python manage.py migrate_images_to_storage --clear     # same, and then empties the database bytes of every copied image
"""
from django.core.management.base import BaseCommand, CommandError

from shop import storage
from shop.models import ProductImage, SubCategory

EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


class Command(BaseCommand):
    help = "Copy photos/icons stored in the database to Supabase Storage (optionally clearing the database copy)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only show what would be moved")
        parser.add_argument("--clear", action="store_true",
                            help="after a successful upload, empty the bytes in the database (also for images copied by an earlier run)")

    def handle(self, *args, **o):
        dry, clear = o["dry_run"], o["clear"]
        if not storage.enabled():
            raise CommandError("Set SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_BUCKET and SUPABASE_ICON_BUCKET first.")
        failed = 0

        # ---- product photos ----
        todo = list(ProductImage.objects.filter(path="").exclude(data__isnull=True).order_by("id").values_list("id", flat=True))
        self.stdout.write(f"Product photos to copy: {len(todo)}")
        copied = 0
        for pk in todo:  # one at a time so memory stays small
            if dry:
                continue
            i = ProductImage.objects.get(pk=pk)
            try:
                ext = EXT.get(i.ctype, "jpg")
                i.path = storage.upload(storage.PHOTO, storage.new_path("products", ext), bytes(i.data), i.ctype or "image/jpeg")
            except storage.StorageError as e:
                failed += 1
                self.stderr.write(f"  photo {pk}: {e}")
                continue
            i.data = None if clear else i.data
            i.save(update_fields=["path", "data"])
            copied += 1
        if clear and not dry:  # photos copied by an earlier run (without --clear)
            n = ProductImage.objects.exclude(path="").exclude(data__isnull=True).update(data=None)
            self.stdout.write(f"Database bytes cleared for {n} earlier-copied photos")
        self.stdout.write(f"Product photos copied: {copied}")

        # ---- sub category icons ----
        todo = list(SubCategory.objects.filter(icon_path="").exclude(icon_data__isnull=True).order_by("id").values_list("id", flat=True))
        self.stdout.write(f"Sub category icons to copy: {len(todo)}")
        copied = 0
        for pk in todo:
            if dry:
                continue
            s = SubCategory.objects.get(pk=pk)
            try:
                s.icon_path = storage.upload(storage.ICON, storage.new_path("icons", "png"), bytes(s.icon_data), "image/png")
            except storage.StorageError as e:
                failed += 1
                self.stderr.write(f"  icon {pk}: {e}")
                continue
            s.icon_data = None if clear else s.icon_data
            s.icon_ver += 1  # a new link, so no app shows the old cached icon
            s.save(update_fields=["icon_path", "icon_data", "icon_ver"])
            copied += 1
        if clear and not dry:
            n = SubCategory.objects.exclude(icon_path="").exclude(icon_data__isnull=True).update(icon_data=None)
            self.stdout.write(f"Database bytes cleared for {n} earlier-copied icons")
        self.stdout.write(f"Sub category icons copied: {copied}")

        if failed:
            raise CommandError(f"{failed} image(s) failed. Nothing was lost; run the command again.")
        self.stdout.write(self.style.SUCCESS("Done." if not dry else "Dry run finished, nothing changed."))
