import io
from django.core.management.base import BaseCommand
from PIL import Image
from shop.models import Category, Product, ProductImage, SubCategory, User

DATA = {("Men's Clothing", "T-Shirt"): [("Men's Polo T-Shirt", 350, 40)],
        ("Women's Clothing", "Bags"): [("Ladies Handbag", 620, 15)],
        ("Gadgets", "Audio"): [("Bluetooth Earbuds", 900, 25)],
        ("Gadgets", "Watches"): [("Smart Watch", 1500, 10)],
        ("Home Decor", "Kitchen"): [("Non-stick Pan", 480, 30)],
        ("Home Decor", "Lighting"): [("LED Table Lamp", 390, 22)],
        ("Cosmetics", "Skin Care"): [("Face Wash", 220, 60)],
        ("Cosmetics", "Makeup"): [("Lipstick Set", 310, 35)]}


def jpeg(color):
    b = io.BytesIO(); Image.new("RGB", (600, 600), color).save(b, "JPEG"); return b.getvalue()


class Command(BaseCommand):
    help = "TEST data: resellers, categories, products with 3 placeholder photos each (never use in production)"

    def handle(self, *a, **k):
        for phone, name, st in [("01711000001", "Rahim Hossain", "approved"), ("01911000003", "Tanvir Ahmed", "pending")]:
            if not User.objects.filter(phone=phone).exists():
                User.objects.create_user(username=phone, phone=phone, first_name=name, password="pass123", status=st, phone_verified=True,
                                         shop_name=f"{name.split()[0]} Fashion House", address="Dhanmondi, Dhaka")
        for (cat, sub), items in DATA.items():
            c, _ = SubCategory.objects.get_or_create(category=Category.objects.get(name=cat), name=sub)
            for n, b, s in items:
                p, new = Product.objects.get_or_create(name=n, defaults={"subcategory": c, "base_price": b, "in_stock": True, "description": f"{n}. Quality product, ready to ship.", "size": "M, L, XL", "color": "Black, Blue", "video_url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ"})
                if new:
                    for i, col in enumerate([(0, 188, 212), (0, 131, 143), (178, 235, 242)]):
                        ProductImage.objects.create(product=p, data=jpeg(col), position=i)
        self.stdout.write("Seeded. Reseller 01711000001 / pass123 (admin: run ensure_admin)")
