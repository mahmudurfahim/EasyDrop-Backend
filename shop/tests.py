import io, os, re
os.environ["SMS_MOCK"] = "1"
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image
from rest_framework.test import APIClient
from shipping import sms
from . import services
from .models import Category, Ledger, Notice, Order, Product, ProductImage, SubCategory, User, Withdraw


def sub_cat(main="Gadgets", name="Audio"):
    return SubCategory.objects.get_or_create(category=Category.objects.get(name=main), name=name)[0]


def photo(w=600, h=300, fmt="JPEG", name="p.jpg"):
    b = io.BytesIO(); Image.new("RGB", (w, h), (0, 150, 120)).save(b, fmt); b.seek(0)
    return SimpleUploadedFile(name, b.read(), content_type="image/" + fmt.lower())


class Base(TestCase):
    def post(self, url, data=None, **kw): return self.c.post(url, data or {}, format=kw.pop("format", "json"), **kw)
    def as_(self, u, pw):
        cache.clear()
        self.c.credentials(HTTP_AUTHORIZATION="Bearer " + self.post("/api/auth/login/", {"username": u, "password": pw}).data["access"])
    def setUp(self):
        self.c = APIClient()
        User.objects.create_user(username="easydrop", password="easydrop", is_staff=True, status="approved", phone_verified=True)
    def reseller(self, phone="01711000008"):
        return User.objects.create_user(username=phone, phone=phone, password="x12345", first_name="Rahim", status="approved", phone_verified=True)
    def product(self, sc=None, base=500, photos=1, name="Pan", **kw):
        p = Product.objects.create(name=name, subcategory=sc or sub_cat(), base_price=base, description="good", **kw)
        for i in range(photos): ProductImage.objects.create(product=p, data=b"x", position=i)
        return p
    def order(self, u, fee=60, profit=140, status="Confirmed", code_zone="inside_dhaka"):
        return Order.objects.create(reseller=u, customer_name="K", phone="01712345678", address="D", cod=900, zone=code_zone, delivery_fee=fee, profit=profit, status=status)


class Journey(Base):
    def test_journey(self):
        c = self.c; ph = "01711000001"; card = {"customer_name": "Karim", "phone": "01712345678", "address": "Dhaka", "zone": "inside_dhaka"}
        sc = sub_cat()
        p = Product.objects.create(name="Earbuds", subcategory=sc, base_price=900, description="d", video_url="https://youtu.be/abcdefghijk")
        Product.objects.create(name="NoPhotos", subcategory=sc, base_price=100, description="d")
        for i in range(3): ProductImage.objects.create(product=p, data=b"x", position=i)
        self.assertEqual(self.post("/api/auth/register/", {"name": "Rahim", "phone": ph}).status_code, 400)
        self.assertEqual(self.post("/api/auth/register/", {"name": "Rahim", "phone": ph, "shop_name": "Rahim Store", "address": "Dhaka", "password": "pass123"}).status_code, 201)
        self.assertEqual(self.post("/api/auth/verify/", {"phone": ph, "code": re.search(r"\d{6}", sms.LAST[ph]).group()}).status_code, 200)
        self.as_(ph, "pass123"); self.assertEqual(c.get("/api/products/").status_code, 403)  # waits for approval
        User.objects.filter(phone=ph).update(status="approved"); me = User.objects.get(phone=ph)
        ps = c.get("/api/products/").data
        self.assertEqual([x["name"] for x in ps], ["Earbuds"]); self.assertEqual(len(ps[0]["images"]), 3)  # 3 photos rule
        self.assertTrue(self.post(f"/api/favorites/{p.id}/").data["favorite"]); self.assertEqual(len(c.get("/api/favorites/").data), 1)
        self.assertEqual(self.post("/api/cart/?zone=inside_dhaka", {"product": p.id, "qty": 1, "sell_price": 1200}).data["profit"], 240)
        o = self.post("/api/cart/checkout/", card); self.assertEqual((o.status_code, o.data["profit"]), (201, 240)); oid = o.data["id"]
        self.assertEqual(c.get("/api/cart/").data["items"], [])
        self.as_("easydrop", "easydrop"); self.assertEqual(self.post(f"/api/admin/orders/{oid}/note/", {"text": "Checking stock"}).status_code, 200)
        self.as_(ph, "pass123"); self.assertEqual(c.get(f"/api/orders/{oid}/").data["notes"][-1]["text"], "Checking stock")
        o2 = self.post("/api/orders/", {**card, "items": [{"product": p.id, "qty": 1, "sell_price": 1200}]}).data["id"]
        self.assertEqual(self.post(f"/api/orders/{o2}/cancel/").data["status"], "Cancelled")
        self.assertEqual(self.post(f"/api/orders/{o2}/cancel/").status_code, 400)
        # confirm (manual, no courier) -> shipped -> delivered
        self.as_("easydrop", "easydrop"); self.assertEqual(self.post(f"/api/admin/orders/{oid}/confirm/").data["status"], "Confirmed")
        self.as_(ph, "pass123"); self.assertEqual(self.post(f"/api/orders/{oid}/cancel/").status_code, 400)
        self.as_("easydrop", "easydrop")
        self.assertEqual([self.post(f"/api/admin/orders/{oid}/status/", {"status": s}).data["status"] for s in ("Shipped", "Delivered")], ["Shipped", "Delivered"])
        self.assertEqual(self.post(f"/api/admin/orders/{oid}/sync/").status_code, 404)  # courier sync is gone
        d = self.c.get("/api/admin/dashboard/").data; self.assertNotIn("steadfast_balance", d)
        self.as_(ph, "pass123"); d = c.get("/api/dashboard/").data
        self.assertEqual((d["completed_orders"], d["total_profit"], d["available"]), (1, 240, 240)); self.assertEqual((d["monthly"][0]["orders"], d["monthly"][0]["delivered"]), (2, 1))
        Ledger.objects.create(user=me, amount=500, kind="refund", ref="t")
        self.assertEqual(self.post("/api/accounts/", {"kind": "bkash", "number": "123"}).status_code, 400)
        a1 = self.post("/api/accounts/", {"kind": "bkash", "number": ph}).data["id"]
        self.assertEqual(self.post("/api/accounts/", {"kind": "bank", "bank_name": "DBBL", "holder": "Rahim", "number": "1234567"}).status_code, 201)
        self.assertEqual(self.post("/api/withdraws/", {"amount": 500}).status_code, 400)
        self.assertEqual(self.post("/api/withdraws/", {"amount": 500, "account_id": a1}).status_code, 201)
        self.assertEqual(c.get("/api/wallet/").data["available"], 240)

    def test_delivery_zones(self):  # 60 inside Dhaka, 100 sub Dhaka, 120 outside Dhaka
        self.reseller(); p = self.product(base=500); self.as_("01711000008", "x12345")
        base = {"customer_name": "K", "phone": "01712345678", "address": "Dhaka", "items": [{"product": p.id, "qty": 1, "sell_price": 700}]}
        for zone, fee in (("inside_dhaka", 60), ("sub_dhaka", 100), ("outside_dhaka", 120)):
            r = self.post("/api/orders/", {**base, "zone": zone})
            self.assertEqual((r.status_code, r.data["delivery_fee"], r.data["profit"], r.data["zone"]), (201, fee, 200 - fee, zone))
        self.assertEqual(self.post("/api/orders/", base).status_code, 400)
        self.assertEqual(self.post("/api/orders/", {**base, "zone": "mars"}).status_code, 400)
        self.assertEqual(self.c.get("/api/wallet/").data["delivery_charges"], {"inside_dhaka": 60, "sub_dhaka": 100, "outside_dhaka": 120})
        self.assertEqual(self.post("/api/cart/", {"product": p.id, "qty": 1, "sell_price": 700}).data["profit"], None)
        self.assertEqual(self.c.get("/api/cart/?zone=sub_dhaka").data["profit"], 100)
        low = {**base, "items": [{"product": p.id, "qty": 1, "sell_price": 580}]}
        self.assertEqual(self.post("/api/orders/", {**low, "zone": "inside_dhaka"}).status_code, 201)
        self.assertEqual(self.post("/api/orders/", {**low, "zone": "outside_dhaka"}).status_code, 400)
        self.as_("easydrop", "easydrop")
        self.assertEqual(self.post("/api/admin/settings/", {"delivery_inside": 70}).data["delivery_inside"], 70)
        self.assertEqual(Order.objects.filter(zone="inside_dhaka").first().delivery_fee, 60)


class Catalog(Base):
    def test_categories_and_subcategories(self):
        self.assertEqual(Category.objects.count(), 12)
        self.assertEqual(list(Category.objects.values_list("name", flat=True)[:3]), ["Men's Clothing", "Women's Clothing", "Baby Fashion"])
        self.as_("easydrop", "easydrop")
        men = Category.objects.get(name="Men's Clothing")
        self.assertEqual(self.post("/api/admin/subcategories/", {"name": "T-Shirt"}).status_code, 400)  # a category must be chosen
        r = self.post("/api/admin/subcategories/", {"name": "T-Shirt", "category": men.id})
        self.assertEqual((r.status_code, r.data["category_name"]), (201, "Men's Clothing")); sid = r.data["id"]
        self.assertEqual(self.post("/api/admin/subcategories/", {"name": "t-shirt", "category": men.id}).status_code, 400)  # duplicate in the same category
        self.assertEqual(self.post("/api/admin/subcategories/", {"name": "T-Shirt", "category": Category.objects.get(name="Gadgets").id}).status_code, 201)  # same name elsewhere is fine
        self.assertEqual(self.c.delete(f"/api/admin/categories/{men.id}/").status_code, 409)  # still has a sub category
        self.assertEqual(len(self.c.get("/api/admin/categories/").data), 12)
        # a product needs a sub category; the category comes from it
        base = {"name": "Polo", "base_price": 350, "in_stock": True, "description": "nice"}
        self.assertEqual(self.post("/api/admin/products/", base).status_code, 400)
        r = self.post("/api/admin/products/", {**base, "subcategory": sid})
        self.assertEqual((r.status_code, r.data["category_name"], r.data["subcategory_name"], r.data["size"], r.data["color"]), (201, "Men's Clothing", "T-Shirt", "", ""))
        r = self.post("/api/admin/products/", {**base, "name": "Polo2", "subcategory": sid, "size": "M, L", "color": "Red"})
        self.assertEqual((r.data["size"], r.data["color"]), ("M, L", "Red")); pid = r.data["id"]
        # a sub category with products cannot be deleted
        self.assertEqual(self.c.delete(f"/api/admin/subcategories/{sid}/").status_code, 409)
        # reseller side: nested list + filters
        for i in range(3): ProductImage.objects.create(product_id=pid, data=b"x", position=i)
        self.reseller(); self.as_("01711000008", "x12345")
        cats = self.c.get("/api/categories/").data
        self.assertEqual(len(cats), 12); self.assertEqual([s["name"] for s in cats[0]["subcategories"]], ["T-Shirt"])
        pr = self.c.get(f"/api/products/?category={men.id}").data; self.assertEqual([x["name"] for x in pr], ["Polo2"])
        self.assertEqual((pr[0]["category_name"], pr[0]["subcategory_name"], pr[0]["size"], pr[0]["color"]), ("Men's Clothing", "T-Shirt", "M, L", "Red"))
        self.assertEqual(len(self.c.get(f"/api/products/?subcategory={sid}").data), 1)
        self.assertEqual(len(self.c.get(f"/api/products/?category={Category.objects.get(name='Gadgets').id}").data), 0)

    def test_subcategory_icon(self):  # upload keeps shape + transparency, replace, remove, public url
        import io
        from PIL import Image
        self.as_("easydrop", "easydrop"); sc = sub_cat()
        def png(w, h):
            b = io.BytesIO(); Image.new("RGBA", (w, h), (255, 0, 0, 0)).save(b, "PNG"); b.seek(0); b.name = "i.png"; return b
        r = self.c.post(f"/api/admin/subcategories/{sc.id}/icon/", {"icon": png(1200, 600)}, format="multipart")
        self.assertEqual(r.status_code, 200); self.assertIn(f"/api/media/subcategory/{sc.id}/?v=1", r.data["icon"])
        img = Image.open(io.BytesIO(self.c.get(f"/api/media/subcategory/{sc.id}/").content))
        self.assertEqual((img.size, img.mode), ((512, 256), "RGBA"))
        r = self.c.post(f"/api/admin/subcategories/{sc.id}/icon/", {"icon": png(40, 40)}, format="multipart")
        self.assertIn("?v=2", r.data["icon"])
        self.assertEqual(self.c.post(f"/api/admin/subcategories/{sc.id}/icon/", {}, format="multipart").status_code, 400)
        self.reseller(); self.as_("01711000008", "x12345")
        cats = self.c.get("/api/categories/").data
        self.assertTrue(any(s["icon"] for c in cats for s in c["subcategories"]))
        self.as_("easydrop", "easydrop")
        self.assertIsNone(self.c.delete(f"/api/admin/subcategories/{sc.id}/icon/").data["icon"])
        self.assertEqual(self.c.get(f"/api/media/subcategory/{sc.id}/").status_code, 404)

    def test_category_add_rename_delete_and_icon(self):
        import io
        from PIL import Image
        self.as_("easydrop", "easydrop")
        def png(w, h):
            b = io.BytesIO(); Image.new("RGBA", (w, h), (0, 128, 0, 0)).save(b, "PNG"); b.seek(0); b.name = "i.png"; return b
        r = self.post("/api/admin/categories/", {"name": "  Toys  "})
        self.assertEqual((r.status_code, r.data["name"], r.data["position"], r.data["icon"]), (201, "Toys", 13, None))  # goes last
        cid = r.data["id"]
        self.assertEqual(self.post("/api/admin/categories/", {"name": "toys"}).status_code, 400)  # duplicate (any case)
        self.assertEqual(self.post("/api/admin/categories/", {"name": "   "}).status_code, 400)
        r = self.c.patch(f"/api/admin/categories/{cid}/", {"name": "Toys & Games"}, format="json"); self.assertEqual((r.status_code, r.data["name"]), (200, "Toys & Games"))
        r = self.post("/api/admin/categories/", {"name": "First one", "position": 1}); self.assertEqual(r.data["position"], 1)
        self.assertEqual(Category.objects.get(name="Men's Clothing").position, 2)  # the old number 1 moved down
        # icon: add, replace, public url, shows in the app list, remove
        r = self.c.post(f"/api/admin/categories/{cid}/icon/", {"icon": png(1200, 600)}, format="multipart")
        self.assertEqual(r.status_code, 200); self.assertIn(f"/api/media/category/{cid}/?v=1", r.data["icon"])
        img = Image.open(io.BytesIO(self.c.get(f"/api/media/category/{cid}/").content)); self.assertEqual((img.size, img.mode), ((512, 256), "RGBA"))
        self.assertIn("?v=2", self.c.post(f"/api/admin/categories/{cid}/icon/", {"icon": png(40, 40)}, format="multipart").data["icon"])
        self.assertEqual(self.c.post(f"/api/admin/categories/{cid}/icon/", {}, format="multipart").status_code, 400)
        self.reseller(); self.as_("01711000008", "x12345")
        mine = [c for c in self.c.get("/api/categories/").data if c["id"] == cid][0]
        self.assertTrue(mine["icon"]); self.assertEqual(mine["subcategories"], [])
        self.as_("easydrop", "easydrop")
        self.assertIsNone(self.c.delete(f"/api/admin/categories/{cid}/icon/").data["icon"])
        self.assertEqual(self.c.get(f"/api/media/category/{cid}/").status_code, 404)
        # a category with a sub category cannot be deleted; an empty one can
        SubCategory.objects.create(category_id=cid, name="Cars")
        self.assertEqual(self.c.delete(f"/api/admin/categories/{cid}/").status_code, 409)
        SubCategory.objects.filter(category_id=cid).delete()
        self.assertEqual(self.c.delete(f"/api/admin/categories/{cid}/").status_code, 204)
        self.assertFalse(Category.objects.filter(pk=cid).exists())

    def test_product_description(self):  # required, max 100 words
        self.as_("easydrop", "easydrop"); sc = sub_cat()
        base = {"name": "Pan", "subcategory": sc.id, "base_price": 500, "in_stock": True}
        self.assertEqual(self.post("/api/admin/products/", base).status_code, 400)
        self.assertEqual(self.post("/api/admin/products/", {**base, "description": "  "}).status_code, 400)
        self.assertEqual(self.post("/api/admin/products/", {**base, "description": "word " * 101}).status_code, 400)
        r = self.post("/api/admin/products/", {**base, "description": "word " * 100})
        self.assertEqual((r.status_code, r.data["description"].split()[0]), (201, "word"))

    def test_image_upload(self):
        self.as_("easydrop", "easydrop"); sc = sub_cat()
        pid = self.post("/api/admin/products/", {"name": "Pan", "subcategory": sc.id, "base_price": 500, "in_stock": True, "description": "good"}).data["id"]
        url = f"/api/admin/products/{pid}/images/"
        self.assertEqual(self.post(url, {}, format="multipart").status_code, 400)  # no file
        bad = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        self.assertEqual(self.post(url, {"images": bad}, format="multipart").status_code, 400)
        r = self.post(url, {"images": [photo(600, 300), photo(300, 700, "PNG", "q.png"), photo(50, 50)]}, format="multipart")
        self.assertEqual(r.status_code, 201); self.assertEqual(len(r.data["photos"]), 3)
        sizes = sorted(Image.open(io.BytesIO(bytes(i.data))).size for i in ProductImage.objects.filter(product_id=pid))
        self.assertEqual(sizes, [(50, 50), (300, 700), (600, 300)])  # nothing is cropped, nothing is enlarged
        big = self.post(url, {"images": [photo(2400, 1200, name="big.jpg")]}, format="multipart"); self.assertEqual(big.status_code, 201)
        self.assertIn((1000, 500), [Image.open(io.BytesIO(bytes(i.data))).size for i in ProductImage.objects.filter(product_id=pid)])  # shrunk, same shape
        ProductImage.objects.filter(product_id=pid).order_by("-id").first().delete()
        # visible to resellers (1 photo is enough)
        self.reseller(); self.as_("01711000008", "x12345"); self.assertEqual(len(self.c.get("/api/products/").data), 1)
        self.as_("easydrop", "easydrop"); first = r.data["photos"][0]["id"]
        r = self.c.delete(f"/api/admin/products/{pid}/images/{first}/"); self.assertEqual((r.status_code, len(r.data["photos"])), (200, 2))
        self.assertEqual(self.c.delete(f"/api/admin/products/{pid}/images/{first}/").status_code, 404)
        self.assertEqual(self.c.get(f"/api/media/product/{r.data['photos'][0]['id']}/").status_code, 200)


class Money(Base):
    def test_one_pending_withdraw(self):
        u = self.reseller(); Ledger.objects.create(user=u, amount=3000, kind="refund", ref="t"); self.as_("01711000008", "x12345")
        acc = self.post("/api/accounts/", {"kind": "bkash", "number": "01711000008"}).data["id"]
        w = lambda amt=500: self.post("/api/withdraws/", {"amount": amt, "account_id": acc})
        first = w(); self.assertEqual(first.status_code, 201)
        r = w(); self.assertEqual(r.status_code, 409); self.assertIn("pending", r.data["error"])
        self.as_("easydrop", "easydrop"); self.assertEqual(self.post(f"/api/admin/withdraws/{first.data['id']}/reject/").status_code, 200)  # cancelled -> allowed again
        self.as_("01711000008", "x12345"); second = w(); self.assertEqual(second.status_code, 201)
        self.assertEqual(w().status_code, 409)
        self.as_("easydrop", "easydrop"); self.assertEqual(self.post(f"/api/admin/withdraws/{second.data['id']}/paid/").status_code, 200)  # completed -> allowed again
        self.as_("01711000008", "x12345"); self.assertEqual(w().status_code, 201)
        self.assertEqual(Withdraw.objects.filter(user=u, status="Pending").count(), 1)

    def test_return_charges_delivery_fee(self):
        u = self.reseller(); self.assertEqual(services.available(u), 0)
        a = self.order(u, fee=80, profit=120)
        services.manual_status(a, "Returned")
        self.assertEqual(services.available(u), -80)  # balance 0 -> -80
        b = self.order(u, fee=120, profit=80, status="Shipped"); services.manual_status(b, "Returned")
        self.assertEqual(services.available(u), -200)
        self.assertEqual(Ledger.objects.filter(user=u, kind="return_fee").count(), 2)
        with self.assertRaises(ValueError): services.set_status(Order.objects.get(pk=a.pk), "Returned")  # charged only once
        self.assertEqual(services.available(u), -200)
        # a later delivered order pays the balance back up
        d = self.order(u, fee=100, profit=300); services.manual_status(d, "Delivered")
        self.assertEqual(services.available(u), 100)
        # needs-review orders resolved as returned are charged too
        e = self.order(u, fee=60, profit=140, status="NeedsReview")
        self.as_("easydrop", "easydrop"); self.assertEqual(self.post(f"/api/admin/orders/{e.id}/resolve/", {"status": "Returned"}).status_code, 200)
        self.assertEqual(services.available(u), 40)
        # a negative balance cannot be withdrawn and shows in the wallet and the admin list
        Ledger.objects.create(user=u, amount=-1000, kind="return_fee", ref="manual")
        self.as_("01711000008", "x12345"); w = self.c.get("/api/wallet/").data
        self.assertEqual(w["available"], -960); self.assertIn("return_fee", [x["kind"] for x in w["ledger"]])
        acc = self.post("/api/accounts/", {"kind": "bkash", "number": "01711000008"}).data["id"]
        self.assertEqual(self.post("/api/withdraws/", {"amount": 500, "account_id": acc}).status_code, 400)
        self.as_("easydrop", "easydrop"); self.assertEqual([r["balance"] for r in self.c.get("/api/admin/resellers/").data], [-960])

    def test_cancel_charges_nothing(self):
        u = self.reseller(); o = self.order(u, status="Pending"); services.set_status(o, "Cancelled")
        self.assertEqual(Ledger.objects.filter(user=u).count(), 0)


class AdminTools(Base):
    def test_confirm_is_plain(self):  # the admin confirms with one click, no note and no courier
        u = self.reseller(); a = self.order(u, status="Pending"); self.as_("easydrop", "easydrop")
        r = self.post(f"/api/admin/orders/{a.id}/confirm/"); self.assertEqual((r.status_code, r.data["status"]), (200, "Confirmed"))
        self.assertEqual(self.post(f"/api/admin/orders/{a.id}/confirm/").status_code, 400)
        self.assertNotIn("tracking", r.data); self.assertNotIn("courier_status", r.data)

    def test_order_note_from_reseller(self):  # the reseller adds an optional note when placing the order
        u = self.reseller(); p = self.product(base=500); self.as_("01711000008", "x12345")
        card = {"customer_name": "K", "phone": "01712345678", "address": "Dhaka", "zone": "inside_dhaka"}
        self.post("/api/cart/", {"product": p.id, "qty": 1, "sell_price": 800})
        r = self.post("/api/cart/checkout/", {**card, "note": "  Please call before delivery  "})
        self.assertEqual((r.status_code, r.data["note"]), (201, "Please call before delivery"))
        r2 = self.post("/api/orders/", {**card, "note": "Gift wrap", "items": [{"product": p.id, "qty": 1, "sell_price": 800}]}); self.assertEqual(r2.data["note"], "Gift wrap")
        r3 = self.post("/api/orders/", {**card, "items": [{"product": p.id, "qty": 1, "sell_price": 800}]}); self.assertEqual((r3.status_code, r3.data["note"]), (201, ""))  # optional
        r4 = self.post("/api/orders/", {**card, "note": "x" * 500, "items": [{"product": p.id, "qty": 1, "sell_price": 800}]}); self.assertEqual(len(r4.data["note"]), 300)
        self.assertEqual(self.c.get(f"/api/orders/{r.data['id']}/").data["note"], "Please call before delivery")
        self.as_("easydrop", "easydrop")  # the admin sees it and can search it
        self.assertEqual(self.c.get(f"/api/admin/orders/?q=gift").data[0]["note"], "Gift wrap")
        self.assertEqual({o["note"] for o in self.c.get("/api/admin/orders/").data}, {"Please call before delivery", "Gift wrap", "", "x" * 300})

    def test_order_search_and_filter(self):
        u1, u2 = self.reseller("01711000008"), self.reseller("01811000009")
        a = self.order(u1, status="Pending"); b = self.order(u2, status="Confirmed", code_zone="outside_dhaka"); b.customer_name = "Salma Akter"; b.phone = "01999888777"; b.save()
        self.as_("easydrop", "easydrop"); g = lambda q: sorted(x["id"] for x in self.c.get("/api/admin/orders/" + q).data)
        self.assertEqual(g(""), [a.id, b.id]); self.assertEqual(g("?status=Pending"), [a.id]); self.assertEqual(g("?zone=outside_dhaka"), [b.id])
        self.assertEqual(g("?q=salma"), [b.id]); self.assertEqual(g("?q=0199988"), [b.id]); self.assertEqual(g("?q=01811000009"), [b.id])
        self.assertEqual(g(f"?q={a.code}"), [a.id]); self.assertEqual(g(f"?q={a.code.lower()}"), [a.id]); self.assertEqual(g(f"?q={1000 + b.id}"), [b.id])
        self.assertEqual(g("?q=zzz"), [])

    def test_notices(self):
        self.as_("easydrop", "easydrop")
        self.assertEqual(self.post("/api/admin/notices/", {"title": " "}).status_code, 400)
        n1 = self.post("/api/admin/notices/", {"title": "Eid offer", "body": "Delivery charge is the same"}).data["id"]
        n2 = self.post("/api/admin/notices/", {"title": "Pinned", "body": "Read me", "pinned": True}).data["id"]
        n3 = self.post("/api/admin/notices/", {"title": "Draft", "active": False}).data["id"]
        self.assertEqual(len(self.c.get("/api/admin/notices/").data), 3)
        self.reseller(); self.as_("01711000008", "x12345")
        self.assertEqual([n["title"] for n in self.c.get("/api/notices/").data], ["Pinned", "Eid offer"])  # pinned first, hidden ones not shown
        self.as_("easydrop", "easydrop")
        self.assertEqual(self.c.patch(f"/api/admin/notices/{n3}/", {"active": True}, format="json").status_code, 200)
        self.assertEqual(self.c.delete(f"/api/admin/notices/{n1}/").status_code, 204)
        self.as_("01711000008", "x12345"); self.assertEqual([n["title"] for n in self.c.get("/api/notices/").data], ["Pinned", "Draft"])
        self.assertEqual(self.c.post("/api/admin/notices/", {"title": "x"}, format="json").status_code, 403)  # resellers cannot write


class Variants(Base):
    def make(self, **kw):
        return self.product(size="M, L", color="Black, White", base=500, **kw)

    def test_must_choose_size_and_color(self):
        self.reseller(); p = self.make(); plain = self.product(name="Plain"); self.as_("01711000008", "x12345")
        pj = [x for x in self.c.get("/api/products/").data if x["id"] == p.id][0]
        self.assertEqual((pj["sizes"], pj["colors"], pj["in_stock"]), (["M", "L"], ["Black", "White"], True)); self.assertNotIn("stock", pj)
        add = lambda **kw: self.post("/api/cart/", {"product": p.id, "qty": 1, "sell_price": 800, **kw})
        self.assertEqual(add().status_code, 400)                                       # nothing chosen
        self.assertEqual(add(size="M").status_code, 400)                               # color missing
        r = add(size="XXL", color="Black"); self.assertEqual(r.status_code, 400); self.assertIn("size", r.data["error"])
        self.assertEqual(add(size="m", color="BLACK").status_code, 200)                # case does not matter, the exact option name is saved
        self.assertEqual(add(size="M", color="White").status_code, 200)                # same product, other color = another cart line
        self.assertEqual(add(size="M", color="White", qty=3).status_code, 200)         # same variant again = updated, not duplicated
        items = self.c.get("/api/cart/").data["items"]
        self.assertEqual([(i["size"], i["color"], i["qty"]) for i in items], [("M", "Black", 1), ("M", "White", 3)])
        self.assertEqual(self.post("/api/cart/", {"product": plain.id, "qty": 1, "sell_price": 800, "size": "XL", "color": "Pink"}).status_code, 200)  # nothing to choose: ignored
        o = self.post("/api/cart/checkout/", {"customer_name": "K", "phone": "01712345678", "address": "D", "zone": "inside_dhaka"}); self.assertEqual(o.status_code, 201)
        got = sorted((i["product"], i["size"], i["color"]) for i in o.data["items"])
        self.assertEqual(got, [("Pan", "M", "Black"), ("Pan", "M", "White"), ("Plain", "", "")])  # the product without options saves no size or color
        sizes = sorted((i["size"], i["color"]) for i in o.data["items"] if i["size"]); self.assertEqual(sizes, [("M", "Black"), ("M", "White")])
        # direct order is validated the same way
        line = {"product": p.id, "qty": 1, "sell_price": 800}
        base = {"customer_name": "K", "phone": "01712345678", "address": "D", "zone": "inside_dhaka"}
        self.assertEqual(self.post("/api/orders/", {**base, "items": [line]}).status_code, 400)
        self.assertEqual(self.post("/api/orders/", {**base, "items": [{**line, "size": "L", "color": "White"}]}).status_code, 201)
        # the admin sees category, sub category, size and color on every line
        self.as_("easydrop", "easydrop"); first = self.c.get("/api/admin/orders/").data[0]["items"][0]
        self.assertEqual((first["category"], first["subcategory"], first["size"], first["color"]), ("Gadgets", "Audio", "L", "White"))

    def test_out_of_stock(self):
        self.reseller(); p = self.make(in_stock=False); self.as_("01711000008", "x12345")
        self.assertEqual([x["in_stock"] for x in self.c.get("/api/products/").data], [False])
        r = self.post("/api/cart/", {"product": p.id, "qty": 1, "sell_price": 800, "size": "M", "color": "Black"}); self.assertEqual(r.status_code, 400); self.assertIn("out of stock", r.data["error"])
        base = {"customer_name": "K", "phone": "01712345678", "address": "D", "zone": "inside_dhaka", "items": [{"product": p.id, "qty": 1, "sell_price": 800, "size": "M", "color": "Black"}]}
        self.assertEqual(self.post("/api/orders/", base).status_code, 400)
        self.as_("easydrop", "easydrop"); self.assertEqual(self.c.patch(f"/api/admin/products/{p.id}/", {"in_stock": True}, format="json").data["in_stock"], True)
        self.as_("01711000008", "x12345"); self.assertEqual(self.post("/api/orders/", base).status_code, 201)

    def test_delete_product_with_orders(self):
        u = self.reseller(); p = self.make(); self.as_("01711000008", "x12345")
        self.post("/api/cart/", {"product": p.id, "qty": 1, "sell_price": 800, "size": "L", "color": "Black"})
        oid = self.post("/api/cart/checkout/", {"customer_name": "K", "phone": "01712345678", "address": "D", "zone": "inside_dhaka"}).data["id"]
        self.post("/api/cart/", {"product": p.id, "qty": 1, "sell_price": 800, "size": "M", "color": "White"})  # still in the cart when the product is deleted
        self.as_("easydrop", "easydrop")
        self.assertEqual(self.c.delete(f"/api/admin/products/{p.id}/").status_code, 204)  # allowed any time, even with orders
        self.assertEqual(Product.objects.count(), 0)
        o = self.c.get("/api/admin/orders/").data[0]; it = o["items"][0]
        self.assertEqual((it["product"], it["category"], it["subcategory"], it["size"], it["color"], o["status"]), ("Pan", "Gadgets", "Audio", "L", "Black", "Pending"))
        self.assertEqual(self.post(f"/api/admin/orders/{oid}/confirm/").data["status"], "Confirmed")  # still trackable
        self.assertEqual(self.post(f"/api/admin/orders/{oid}/status/", {"status": "Shipped"}).status_code, 200)
        self.assertEqual(self.post(f"/api/admin/orders/{oid}/status/", {"status": "Returned"}).status_code, 200)  # returns work too
        self.as_("01711000008", "x12345")
        self.assertEqual(self.c.get("/api/products/").data, [])                    # gone from the product list
        self.assertEqual(self.c.get("/api/cart/").data["items"], [])               # and from the cart
        self.assertEqual(self.c.get(f"/api/orders/{oid}/").data["items"][0]["product"], "Pan")  # but the reseller can still track it

    def test_one_photo_is_enough(self):
        self.as_("easydrop", "easydrop"); sc = sub_cat()
        pid = self.post("/api/admin/products/", {"name": "Pan", "subcategory": sc.id, "base_price": 500, "in_stock": True, "description": "good"}).data["id"]
        self.reseller(); self.as_("01711000008", "x12345"); self.assertEqual(self.c.get("/api/products/").data, [])
        ProductImage.objects.create(product_id=pid, data=b"x", position=0); self.assertEqual(len(self.c.get("/api/products/").data), 1)


class Priority(Base):
    """Priority is a number (1 = first). Setting a number that is taken puts the new item there and moves the rest down by one."""
    def test_category_subcategory_product_numbers(self):
        self.as_("easydrop", "easydrop"); g = Category.objects.get(name="Gadgets"); men = Category.objects.get(name="Men's Clothing")
        cats = lambda: [c["name"] for c in self.c.get("/api/admin/categories/").data]
        pos = lambda: {c["name"]: c["position"] for c in self.c.get("/api/admin/categories/").data}
        self.assertEqual(cats()[:3], ["Men's Clothing", "Women's Clothing", "Baby Fashion"]); self.assertEqual(pos()["Men's Clothing"], 1)  # numbered 1..12 by the migration
        r = self.c.patch(f"/api/admin/categories/{g.id}/", {"position": 1, "name": "Hacked"}, format="json")
        self.assertEqual((r.status_code, r.data["name"], r.data["position"]), (200, "Gadgets", 1))  # the name cannot change
        self.assertEqual(cats()[:4], ["Gadgets", "Men's Clothing", "Women's Clothing", "Baby Fashion"])  # Gadgets takes 1, the old 1 becomes 2 ... everyone moves back one
        p = pos(); self.assertEqual((p["Men's Clothing"], p["Women's Clothing"], p["Baby Fashion"]), (2, 3, 4))
        self.assertEqual(self.c.patch(f"/api/admin/categories/{men.id}/", {"position": 0}, format="json").status_code, 400)  # a category always keeps a number
        self.assertEqual(self.c.patch(f"/api/admin/categories/{men.id}/", {"position": 12}, format="json").status_code, 200)
        self.assertEqual(cats()[0], "Gadgets"); self.assertEqual(pos()["Men's Clothing"], 12)
        self.assertEqual(self.post(f"/api/admin/categories/{men.id}/move/", {"direction": "up"}).status_code, 404)  # the arrows are gone
        # sub categories: numbered ones first, unnumbered after (by name); setting a taken number pushes the rest down
        a = SubCategory.objects.create(category=g, name="Audio"); b = SubCategory.objects.create(category=g, name="Watches"); c = SubCategory.objects.create(category=g, name="Cables")
        other = SubCategory.objects.create(category=men, name="Polo")
        order = lambda: [x["name"] for x in self.c.get("/api/admin/subcategories/").data if x["category"] == g.id]
        sp = lambda i: self.c.patch(f"/api/admin/subcategories/{i}/", {"position": 1 if i != a.id else 2}, format="json")
        self.assertEqual(order(), ["Audio", "Cables", "Watches"])  # nobody has a number yet
        sp(b.id); self.assertEqual(order(), ["Watches", "Audio", "Cables"])
        sp(c.id); self.assertEqual(order(), ["Cables", "Watches", "Audio"])   # Cables took 1, Watches moved to 2
        sp(a.id); self.assertEqual(order(), ["Cables", "Audio", "Watches"])   # Audio took 2, Watches moved to 3
        self.assertEqual(SubCategory.objects.get(pk=other.id).position, 0)    # other categories are untouched
        self.assertEqual(self.post("/api/admin/subcategories/", {"name": "Chargers", "category": g.id, "position": 1}).status_code, 201)
        self.assertEqual(order()[:2], ["Chargers", "Cables"])                 # a new one can be put on a number too
        self.reseller(); self.as_("01711000008", "x12345")
        gad = [x for x in self.c.get("/api/categories/").data if x["name"] == "Gadgets"][0]
        self.assertEqual([x["name"] for x in gad["subcategories"]], ["Chargers", "Cables", "Audio", "Watches"])
        self.as_("easydrop", "easydrop")
        # products: the number counts inside the sub category, unnumbered new products stay on top of each other by newest
        p1, p2, p3 = (self.product(sc=a, name=n) for n in ("One", "Two", "Three"))
        pl = lambda: [x["name"] for x in self.c.get("/api/admin/products/").data if x["subcategory"] == a.id]
        pp = lambda i, n: self.c.patch(f"/api/admin/products/{i}/", {"position": n}, format="json")
        self.assertEqual(pl(), ["Three", "Two", "One"])
        pp(p1.id, 1); self.assertEqual(pl(), ["One", "Three", "Two"])
        pp(p2.id, 1); self.assertEqual(pl(), ["Two", "One", "Three"])          # Two took 1, One moved to 2, the unnumbered one stays last
        pp(p3.id, 2); self.assertEqual(pl(), ["Two", "Three", "One"])          # Three took 2, One moved to 3
        self.assertEqual(Product.objects.get(pk=p1.id).position, 3)
        self.assertEqual(self.c.patch(f"/api/admin/products/{p1.id}/", {"active": False}, format="json").status_code, 200)  # other edits do not touch the numbers
        self.assertEqual(Product.objects.get(pk=p1.id).position, 3)
        for i in (p1, p2, p3): ProductImage.objects.create(product=i, data=b"x", position=0)
        self.c.patch(f"/api/admin/products/{p1.id}/", {"active": True}, format="json")
        self.reseller("01711000009"); self.as_("01711000009", "x12345")
        self.assertEqual([x["name"] for x in self.c.get(f"/api/products/?subcategory={a.id}").data], ["Two", "Three", "One"])  # resellers see the same order

    def test_withdraw_list_shows_balance(self):
        u = self.reseller(); Ledger.objects.create(user=u, amount=1500, kind="refund", ref="t"); self.as_("01711000008", "x12345")
        acc = self.post("/api/accounts/", {"kind": "bkash", "number": "01711000008"}).data["id"]; self.assertEqual(self.post("/api/withdraws/", {"amount": 500, "account_id": acc}).status_code, 201)
        self.as_("easydrop", "easydrop"); row = self.c.get("/api/admin/withdraws/").data[0]
        self.assertEqual((row["amount"], row["balance"]), (500, 1000))  # the request is already taken out of the balance
        Ledger.objects.create(user=u, amount=-1700, kind="return_fee", ref="x"); self.assertEqual(self.c.get("/api/admin/withdraws/").data[0]["balance"], -700)


class ProductCode(Base):
    def test_code_is_automatic_and_searchable(self):
        a, b = self.product(name="Alpha"), self.product(name="Beta")
        self.assertEqual((a.code, b.code), (f"EP{1000 + a.id}", f"EP{1000 + b.id}"))
        self.reseller(); self.as_("01711000008", "x12345")
        rows = self.c.get("/api/products/").data; self.assertEqual({x["name"]: x["code"] for x in rows}, {"Alpha": a.code, "Beta": b.code})
        for q in (b.code, b.code.lower(), str(1000 + b.id)):
            self.assertEqual([x["id"] for x in self.c.get("/api/products/?q=" + q).data], [b.id])
        self.as_("easydrop", "easydrop")
        r = self.post("/api/admin/products/", {"name": "Gamma", "subcategory": sub_cat().id, "base_price": 100, "description": "d"})
        self.assertEqual((r.status_code, r.data["code"]), (201, f"EP{1000 + r.data['id']}"))


class RetailCustomers(Base):
    def test_every_order_saves_its_customer_and_dashboard_totals(self):
        from .models import RetailCustomer
        u = self.reseller(); p = self.product(base=500); self.as_("01711000008", "x12345")
        body = {"customer_name": "Karim", "phone": "01712345678", "address": "Mirpur, Dhaka", "zone": "inside_dhaka", "items": [{"product": p.id, "qty": 1, "sell_price": 900}]}
        self.assertEqual(self.post("/api/orders/", body).status_code, 201); self.assertEqual(self.post("/api/orders/", body).status_code, 201)
        rows = RetailCustomer.objects.all()
        self.assertEqual((rows.count(), rows[0].name, rows[0].phone, rows[0].address, rows[0].reseller_id), (2, "Karim", "01712345678", "Mirpur, Dhaka", u.id))
        self.as_("easydrop", "easydrop"); d = self.c.get("/api/admin/dashboard/").data
        self.assertEqual((d["orders_total"], d["resellers_total"]), (2, 1))


class TrackingLink(Base):  # Steadfast tracking link: optional, given by the admin when the parcel is shipped
    LINK = "https://steadfast.com.bd/tl/SsTx7EQ_jAcbK0zGQmKlUOKGADlBSOAL"

    def test_ship_without_link_is_as_before(self):
        u = self.reseller(); o = self.order(u, status="Confirmed"); self.as_("easydrop", "easydrop")
        r = self.post(f"/api/admin/orders/{o.id}/status/", {"status": "Shipped"})
        self.assertEqual((r.status_code, r.data["status"], r.data["tracking_url"]), (200, "Shipped", ""))

    def test_ship_with_link_and_reseller_sees_it(self):
        u = self.reseller(); o = self.order(u, status="Confirmed"); self.as_("easydrop", "easydrop")
        r = self.post(f"/api/admin/orders/{o.id}/status/", {"status": "Shipped", "tracking_url": f"  {self.LINK} "})
        self.assertEqual((r.status_code, r.data["tracking_url"]), (200, self.LINK))
        self.as_("01711000008", "x12345")
        self.assertEqual(self.c.get(f"/api/orders/{o.id}/").data["tracking_url"], self.LINK)

    def test_only_steadfast_links(self):
        u = self.reseller(); o = self.order(u, status="Confirmed"); self.as_("easydrop", "easydrop")
        for bad in ("https://evil.example/tl/abc", "http://steadfast.com.bd/tl/abc", "https://steadfast.com.bd.evil.example/tl/abc",
                    "https://steadfast.com.bd/", "javascript:alert(1)", "https://user@steadfast.com.bd/tl/abc"):
            r = self.post(f"/api/admin/orders/{o.id}/status/", {"status": "Shipped", "tracking_url": bad})
            self.assertEqual(r.status_code, 400, bad)
        o.refresh_from_db(); self.assertEqual((o.status, o.tracking_url), ("Confirmed", ""))

    def test_set_change_clear_later(self):
        u = self.reseller(); o = self.order(u, status="Confirmed"); self.as_("easydrop", "easydrop")
        self.assertEqual(self.post(f"/api/admin/orders/{o.id}/tracking/", {"tracking_url": self.LINK}).status_code, 400)  # not shipped yet
        self.post(f"/api/admin/orders/{o.id}/status/", {"status": "Shipped"})
        r = self.post(f"/api/admin/orders/{o.id}/tracking/", {"tracking_url": self.LINK}); self.assertEqual(r.data["tracking_url"], self.LINK)
        r = self.post(f"/api/admin/orders/{o.id}/tracking/", {"tracking_url": ""}); self.assertEqual((r.status_code, r.data["tracking_url"]), (200, ""))


class RetailTotals(Base):  # the retail customers table shows each order's status and the customer's delivered / returned totals
    def test_status_and_totals_follow_the_orders(self):
        from .models import RetailCustomer
        u = self.reseller(); rows = {}
        for st in ("Confirmed", "Confirmed", "Confirmed"):
            o = self.order(u, status=st)  # every test order goes to 01712345678, COD 900
            rows[o.id] = RetailCustomer.objects.create(order=o, reseller=u, name="K", phone=o.phone, address="D")
        ids = list(rows)
        self.assertEqual([(r.status, r.total_delivered, r.total_returned) for r in RetailCustomer.objects.order_by("id")], [("Confirmed", 0, 0)] * 3)
        services.set_status(Order.objects.get(pk=ids[0]), "Delivered"); services.set_status(Order.objects.get(pk=ids[1]), "Delivered"); services.set_status(Order.objects.get(pk=ids[2]), "Returned")
        r = {x.order_id: x for x in RetailCustomer.objects.all()}
        self.assertEqual([r[i].status for i in ids], ["Delivered", "Delivered", "Returned"])
        for x in r.values():  # all rows of that phone show the same totals
            self.assertEqual((x.total_delivered, x.total_returned, x.delivered_cod, x.returned_cod), (2, 1, 1800, 900))

    def test_new_row_starts_with_the_customers_history(self):
        from .models import RetailCustomer
        u = self.reseller(); old = self.order(u, status="Confirmed"); RetailCustomer.objects.create(order=old, reseller=u, name="K", phone=old.phone, address="D")
        services.set_status(old, "Delivered")
        new = self.order(u, status="Pending"); rc = RetailCustomer.objects.create(order=new, reseller=u, name="K", phone=new.phone, address="D")
        rc.refresh_from_db(); self.assertEqual((rc.status, rc.total_delivered, rc.delivered_cod, rc.total_returned), ("Pending", 1, 900, 0))

    def test_placing_an_order_through_the_api_fills_it(self):
        from .models import RetailCustomer
        u = self.reseller(); p = self.product(base=500); self.as_("01711000008", "x12345")
        body = {"customer_name": "Karim", "phone": "01799999999", "address": "Mirpur", "zone": "inside_dhaka", "items": [{"product": p.id, "qty": 1, "sell_price": 900}]}
        self.assertEqual(self.post("/api/orders/", body).status_code, 201)
        rc = RetailCustomer.objects.get(); self.assertEqual((rc.status, rc.total_delivered, rc.total_returned), ("Pending", 0, 0))
